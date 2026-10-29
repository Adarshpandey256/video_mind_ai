"""Step 3 orchestration: transcript in, validated structured summary out.

The service owns the pipeline order and nothing else - prompt text lives in
:mod:`src.prompts.summary_prompts`, chunking in
:mod:`src.services.transcript_preprocessor`, model calls in
:mod:`src.services.summary_generation`, schema validation in
:mod:`src.services.summary_validation`, merging in
:mod:`src.services.summary_merger` and persistence in
:mod:`src.services.summary_storage`.

It has no dependency on Streamlit, so the same call works from a test, the
interface, or later Step 4/5 export code::

    result = summarize_transcript(transcript=record, options=SummaryOptions())

Honest reporting rules implemented here:

* a chunk that fails is recorded, never silently skipped;
* a run where any chunk failed is stored as ``partial`` and says so;
* a failed model call is never replaced by fabricated content;
* the transcript is untouched whatever happens.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from config.settings import Settings, get_settings
from src.models.schemas import StageStatus, TranscriptRecord
from src.models.summary_schemas import (
    SummaryCoverage,
    SummaryOptions,
    SummaryOutcome,
    SummaryResult,
    SummaryStageReport,
    SummaryStatus,
)
from src.prompts.summary_prompts import (
    JSON_CONTRACT,
    SYSTEM_PROMPT,
    build_summary_messages,
    sanitize_custom_instruction,
)
from src.services.chunk_summarizer import ChunkSummarizer
from src.services.summary_generation import SummaryGenerationError, SummaryJsonClient
from src.services.summary_merger import SummaryMerger
from src.services.summary_storage import SummaryStorage, SummaryStorageError
from src.services.summary_validation import build_structured_summary, quality_notes
from src.services.timestamp_service import validator_for
from src.services.transcript_preprocessor import PreparedTranscript, TranscriptPreprocessor
from src.services.transcript_service import TranscriptService

logger = logging.getLogger(__name__)

#: Fixed prompt overhead (requirements block + JSON contract) added to the measured
#: system prompt when reserving space for a request.
PROMPT_OVERHEAD_TOKENS = 1200
#: Error types that make continuing pointless: nothing will succeed without a fix.
FATAL_ERROR_TYPES = {"configuration", "authentication", "model"}

STAGE_LABELS = {
    "validate": "Validate transcript",
    "prepare": "Prepare transcript",
    "generate": "Generate summary",
    "chunk": "Summarise sections",
    "merge": "Merge sections",
    "verify": "Verify result",
    "store": "Save summary",
}


class SummarizationService:
    """Turn a Step 2 transcript into a validated structured summary."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: SummaryJsonClient | None = None,
        preprocessor: TranscriptPreprocessor | None = None,
        chunk_summarizer: ChunkSummarizer | None = None,
        merger: SummaryMerger | None = None,
        storage: SummaryStorage | None = None,
        transcript_service: TranscriptService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or SummaryJsonClient(self._settings)
        self._preprocessor = preprocessor or TranscriptPreprocessor(self._settings)
        self._chunk_summarizer = chunk_summarizer or ChunkSummarizer(
            self._settings, self._client
        )
        self._merger = merger or SummaryMerger(self._settings, self._client)
        self._storage = storage or SummaryStorage(self._settings.summary_dir)
        self._transcripts = transcript_service or TranscriptService(self._settings)

    # ------------------------------------------------------------------ properties
    @property
    def settings(self) -> Settings:
        """Return the settings used by this service."""
        return self._settings

    @property
    def storage(self) -> SummaryStorage:
        """Return the summary storage layer."""
        return self._storage

    @property
    def transcripts(self) -> TranscriptService:
        """Return the transcript service used to load Step 2 records."""
        return self._transcripts

    def is_configured(self) -> bool:
        """True when an API key is available, so a summary can be attempted."""
        return self._client.is_configured()

    # ------------------------------------------------------------------ entry points
    def summarize(
        self,
        transcript: TranscriptRecord | None,
        options: SummaryOptions | None = None,
        *,
        on_stage: Callable[[str], None] | None = None,
    ) -> SummaryResult:
        """Generate, validate and store a summary for one transcript.

        ``on_stage`` receives a short, human readable label as each stage begins, so
        the interface can show real progress (never a synthetic percentage). It is
        optional, and a failing callback can never break the run.
        """
        resolved_options = self._resolve_options(options)
        stages: list[SummaryStageReport] = []

        # --- validate ---------------------------------------------------------
        self._notify(on_stage, "Validating the transcript")
        problem = self._transcript_problem(transcript)
        if problem is not None:
            message, outcome = problem
            stages.append(SummaryStageReport(stage="validate", status=StageStatus.FAILED, detail=message))
            logger.warning("Summarisation rejected: %s", outcome.value)
            return SummaryResult(
                outcome=outcome, message=message, stages=stages, failed_stage="validate"
            )
        assert transcript is not None  # guarded by _transcript_problem
        stages.append(
            SummaryStageReport(
                stage="validate",
                status=StageStatus.COMPLETED,
                detail=(
                    f"{transcript.segment_count} segment(s), language "
                    f"{transcript.language or 'not reported'}"
                ),
            )
        )

        if not self.is_configured():
            message = (
                "No NVIDIA API key is configured, so no summary can be generated. "
                "Add NVIDIA_API_KEY to your local .env file and restart the application. "
                "Your transcript is unaffected."
            )
            stages.append(
                SummaryStageReport(stage="generate", status=StageStatus.SKIPPED, detail=message)
            )
            return SummaryResult(
                outcome=SummaryOutcome.MISSING_CREDENTIALS,
                message=message,
                stages=stages,
                failed_stage="generate",
            )

        # --- prepare ----------------------------------------------------------
        self._notify(on_stage, "Measuring the transcript and planning requests")
        prepared = self._preprocessor.prepare(
            transcript,
            include_timestamps=resolved_options.include_timestamps,
            reserved_tokens=self._reserved_tokens(),
        )
        stages.append(
            SummaryStageReport(
                stage="prepare",
                status=StageStatus.COMPLETED,
                detail=(
                    f"~{prepared.tokens} transcript tokens, {prepared.chunk_count} request(s)"
                    + (
                        f"; {prepared.segments_without_timestamps} segment(s) without timestamps"
                        if prepared.segments_without_timestamps
                        else ""
                    )
                ),
            )
        )
        logger.info(
            "Summarisation started (mode=%s, chunking=%s, requests=%s)",
            resolved_options.mode.value,
            prepared.needs_chunking,
            prepared.chunk_count,
        )

        validator = validator_for(
            transcript,
            tolerance_seconds=self._settings.summary_timestamp_tolerance_seconds,
        )
        started = time.perf_counter()

        if prepared.needs_chunking:
            result = self._summarize_chunked(
                transcript, prepared, resolved_options, validator, stages, on_stage
            )
        else:
            result = self._summarize_single(
                transcript, prepared, resolved_options, validator, stages, on_stage
            )

        if result.summary is None:
            result.stages = stages
            return result

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        summary = result.summary

        # --- verify -----------------------------------------------------------
        self._notify(on_stage, "Verifying the result against the transcript")
        notes = quality_notes(
            summary, expected_mode=resolved_options, source_title=transcript.title
        )
        if notes:
            summary.limitations = list(
                dict.fromkeys([*summary.limitations, *notes])
            )
        summary.latency_ms = elapsed_ms
        stages.append(
            SummaryStageReport(
                stage="verify",
                status=StageStatus.COMPLETED,
                detail=(
                    f"{len(summary.key_takeaways)} takeaway(s), {len(summary.main_topics)} topic(s), "
                    f"{len(summary.chapters)} chapter(s), {len(summary.action_items)} action item(s)"
                ),
            )
        )

        # --- store ------------------------------------------------------------
        self._notify(on_stage, "Saving the summary")
        try:
            summary = self._storage.save(summary)
        except SummaryStorageError as error:
            logger.warning("Summary could not be stored: %s", type(error).__name__)
            stages.append(
                SummaryStageReport(stage="store", status=StageStatus.FAILED, detail=str(error))
            )
            result.stages = stages
            result.outcome = SummaryOutcome.STORAGE_FAILED
            result.message = (
                f"{result.message} The summary is shown below but could not be saved to disk, "
                "so it will not be available after you leave this page."
            )
            result.summary = summary
            return result

        stages.append(
            SummaryStageReport(
                stage="store",
                status=StageStatus.COMPLETED,
                detail=f"Saved as {summary.summary_id}",
            )
        )
        result.stages = stages
        result.summary = summary
        logger.info(
            "Summarisation finished (summary_id=%s, status=%s, coverage=%.0f%%, model_calls=%s, latency_ms=%s)",
            summary.summary_id,
            summary.processing_status.value,
            summary.coverage.ratio * 100,
            summary.model_calls,
            elapsed_ms,
        )
        return result

    def load_transcript(self, transcript_id: str) -> TranscriptRecord:
        """Load a stored transcript, raising :class:`TranscriptStorageError` safely."""
        return self._transcripts.load(transcript_id)

    # ------------------------------------------------------------------ paths
    def _summarize_single(
        self,
        transcript: TranscriptRecord,
        prepared: PreparedTranscript,
        options: SummaryOptions,
        validator,
        stages: list[SummaryStageReport],
        on_stage: Callable[[str], None] | None = None,
    ) -> SummaryResult:
        """Short transcript: one request, no chunking, no merge call."""
        self._notify(on_stage, "Generating the summary with NVIDIA Nemotron")
        system_prompt, user_prompt = build_summary_messages(
            transcript_text=prepared.transcript_text,
            options=options,
            source_title=transcript.title,
            duration_seconds=transcript.duration_seconds,
        )
        if prepared.segments_without_timestamps and options.include_timestamps:
            user_prompt += (
                "\n\nNote: this transcript has no usable timestamps for some segments, so "
                "leave timestamp fields null rather than estimating times."
            )

        try:
            response = self._client.request_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage="summary",
                max_tokens=self._settings.summary_max_tokens,
            )
        except SummaryGenerationError as error:
            return self._failure_result(error, stages, stage="generate")

        data = dict(response.data)
        if not options.extract_action_items:
            data["action_items"] = []

        try:
            summary, _ = build_structured_summary(
                data,
                source_id=transcript.video_id,
                source_title=transcript.title,
                source_type=transcript.source_type,
                options=options,
                validator=validator,
                source_text=transcript.full_text or prepared.transcript_text,
                generation_model=response.response.model,
                coverage=SummaryCoverage(
                    chunks_total=1,
                    chunks_completed=1,
                    source_segments=transcript.segment_count,
                ),
                status=SummaryStatus.COMPLETED,
                used_chunking=False,
                model_calls=response.attempts,
                source_duration_seconds=transcript.duration_seconds,
                transcript_id=transcript.transcript_id,
            )
        except ValueError as error:
            stages.append(
                SummaryStageReport(
                    stage="generate", status=StageStatus.FAILED, detail=str(error)
                )
            )
            return SummaryResult(
                outcome=SummaryOutcome.VALIDATION_FAILED,
                message=(
                    "The model answered, but the response did not contain a usable summary. "
                    "Try again, or choose a different summary style."
                ),
                stages=stages,
                failed_stage="generate",
            )

        stages.append(
            SummaryStageReport(
                stage="generate",
                status=StageStatus.COMPLETED,
                detail=(
                    f"{response.response.model} · {response.attempts} request(s) · "
                    f"{response.response.latency_ms or 0:.0f} ms"
                ),
            )
        )
        return SummaryResult(
            outcome=SummaryOutcome.SUCCESS,
            message="Summary generated successfully.",
            summary=summary,
            stages=stages,
        )

    def _summarize_chunked(
        self,
        transcript: TranscriptRecord,
        prepared: PreparedTranscript,
        options: SummaryOptions,
        validator,
        stages: list[SummaryStageReport],
        on_stage: Callable[[str], None] | None = None,
    ) -> SummaryResult:
        """Long transcript: summarise each chunk, then merge the successes."""
        sections = []
        failed_indexes: list[int] = []
        failed_segments = 0
        model_calls = 0
        last_error: SummaryGenerationError | None = None
        total = len(prepared.chunks)

        for chunk in prepared.chunks:
            self._notify(
                on_stage,
                f"Summarising section {chunk.index + 1} of {total}",
            )
            try:
                section, calls = self._chunk_summarizer.summarize_chunk(
                    chunk, options, validator, chunk_total=total
                )
            except SummaryGenerationError as error:
                last_error = error
                failed_indexes.append(chunk.index)
                failed_segments += chunk.segment_count
                logger.warning(
                    "Chunk %s/%s could not be summarised (type=%s)",
                    chunk.index + 1,
                    total,
                    error.error_type,
                )
                if error.error_type in FATAL_ERROR_TYPES:
                    stages.append(
                        SummaryStageReport(
                            stage="chunk",
                            status=StageStatus.FAILED,
                            detail=f"Section {chunk.index + 1} of {total}: {error}",
                        )
                    )
                    return self._failure_result(error, stages, stage="chunk")
                continue
            model_calls += calls
            sections.append(section)

        if not sections:
            error = last_error or SummaryGenerationError(
                "No section of the transcript could be summarised.",
                error_type="provider",
            )
            return self._failure_result(error, stages, stage="chunk")

        coverage = SummaryCoverage(
            chunks_total=total,
            chunks_completed=len(sections),
            chunks_failed=len(failed_indexes),
            failed_chunk_indexes=list(failed_indexes),
            source_segments=transcript.segment_count,
            segments_in_failed_chunks=failed_segments,
        )
        stages.append(
            SummaryStageReport(
                stage="chunk",
                status=StageStatus.FAILED if failed_indexes else StageStatus.COMPLETED,
                detail=coverage.describe(),
            )
        )

        self._notify(on_stage, f"Merging {len(sections)} section summaries")
        try:
            merged, merge_calls = self._merger.merge(
                sections,
                options,
                validator,
                source_title=transcript.title,
            )
        except SummaryGenerationError as error:
            return self._failure_result(error, stages, stage="merge")
        model_calls += merge_calls
        stages.append(
            SummaryStageReport(
                stage="merge",
                status=StageStatus.COMPLETED,
                detail=f"{len(sections)} section summary/summaries merged in {merge_calls} request(s)",
            )
        )

        if not options.extract_action_items:
            merged["action_items"] = []
        if coverage.is_partial:
            merged["limitations"] = list(merged.get("limitations") or []) + [
                "Part of this recording could not be summarised, so this result does not "
                "cover the complete source."
            ]

        try:
            summary, _ = build_structured_summary(
                merged,
                source_id=transcript.video_id,
                source_title=transcript.title,
                source_type=transcript.source_type,
                options=options,
                validator=validator,
                source_text=transcript.full_text or prepared.transcript_text,
                generation_model=self._settings.nvidia_model,
                coverage=coverage,
                status=SummaryStatus.PARTIAL if coverage.is_partial else SummaryStatus.COMPLETED,
                used_chunking=True,
                model_calls=model_calls,
                source_duration_seconds=transcript.duration_seconds,
                transcript_id=transcript.transcript_id,
            )
        except ValueError as error:
            stages.append(
                SummaryStageReport(stage="merge", status=StageStatus.FAILED, detail=str(error))
            )
            return SummaryResult(
                outcome=SummaryOutcome.VALIDATION_FAILED,
                message=(
                    "The merged result did not contain a usable summary. Try again, or choose "
                    "a different summary style."
                ),
                stages=stages,
                failed_stage="merge",
            )

        if coverage.is_partial:
            return SummaryResult(
                outcome=SummaryOutcome.PARTIAL,
                message=(
                    f"Summary generated from {coverage.chunks_completed} of {coverage.chunks_total} "
                    "sections. Part of the recording is not covered - see the warnings below."
                ),
                summary=summary,
                stages=stages,
            )
        return SummaryResult(
            outcome=SummaryOutcome.SUCCESS,
            message="Summary generated successfully.",
            summary=summary,
            stages=stages,
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _notify(on_stage: Callable[[str], None] | None, label: str) -> None:
        """Report a stage label to the caller; progress must never break a run."""
        if on_stage is None:
            return
        try:
            on_stage(label)
        except Exception:  # noqa: BLE001 - a UI callback must not fail a summary
            logger.debug("Stage callback raised; continuing without progress updates.")

    def _resolve_options(self, options: SummaryOptions | None) -> SummaryOptions:
        """Apply configured defaults and sanitise the custom instruction."""
        base = options or SummaryOptions(
            mode=self._settings.summary_mode,
            length=self._settings.summary_length,
            language=self._settings.summary_language,
        )
        instruction = sanitize_custom_instruction(
            base.custom_instruction, self._settings.summary_max_custom_instruction_chars
        )
        if instruction != base.custom_instruction:
            # An extra instruction is allowed as a preference in any mode; it is cleaned
            # and length limited the same way as in custom mode.
            base = base.model_copy(update={"custom_instruction": instruction})
        return base

    def _reserved_tokens(self) -> int:
        """Space a request needs beyond the transcript itself."""
        return (
            self._preprocessor.measure(SYSTEM_PROMPT)
            + self._preprocessor.measure(JSON_CONTRACT)
            + PROMPT_OVERHEAD_TOKENS
            + min(self._settings.summary_max_tokens, 2000)
        )

    def _transcript_problem(
        self, transcript: TranscriptRecord | None
    ) -> tuple[str, SummaryOutcome] | None:
        """Return ``(message, outcome)`` when the transcript cannot be summarised."""
        if transcript is None:
            return (
                "Select a transcript before generating a summary. Transcribe a video on the "
                "Home page first.",
                SummaryOutcome.INVALID_TRANSCRIPT,
            )
        if not transcript.segments and not (transcript.full_text or "").strip():
            return (
                "This transcript is empty, so there is nothing to summarise. Re-run "
                "transcription on the recording.",
                SummaryOutcome.INVALID_TRANSCRIPT,
            )
        if transcript.duration_seconds:
            limit_seconds = self._settings.summary_max_transcript_minutes * 60
            if transcript.duration_seconds > limit_seconds:
                return (
                    "This recording is longer than the configured summarisation limit "
                    f"({self._settings.summary_max_transcript_minutes} minutes). Summarising it "
                    "would exceed the bounded request budget, so it was refused instead of "
                    "producing a misleading partial result.",
                    SummaryOutcome.TRANSCRIPT_TOO_LONG,
                )
        return None

    def _failure_result(
        self,
        error: SummaryGenerationError,
        stages: list[SummaryStageReport],
        *,
        stage: str,
    ) -> SummaryResult:
        """Convert a generation failure into an honest, non-technical result."""
        outcome = {
            "configuration": SummaryOutcome.MISSING_CREDENTIALS,
            "authentication": SummaryOutcome.MISSING_CREDENTIALS,
            "invalid_output": SummaryOutcome.VALIDATION_FAILED,
            "empty_response": SummaryOutcome.VALIDATION_FAILED,
        }.get(error.error_type, SummaryOutcome.PROVIDER_FAILED)

        message = str(error)
        if error.error_type == "truncated":
            message = (
                "The model ran out of its output budget before finishing this summary. "
                "Raise SUMMARY_MAX_TOKENS (or reduce the detail level) in your local .env file "
                "and try again."
            )
        elif error.error_type == "rate_limit":
            message = (
                "The NVIDIA service is rate limiting this key. Wait a moment and try again."
            )
        elif error.error_type == "connection":
            message = (
                "The NVIDIA endpoint could not be reached. Check your internet connection and "
                "NVIDIA_BASE_URL, then try again. Your transcript is unaffected."
            )

        stages.append(
            SummaryStageReport(stage=stage, status=StageStatus.FAILED, detail=message)
        )
        logger.warning("Summarisation failed (stage=%s, type=%s)", stage, error.error_type)
        return SummaryResult(
            outcome=outcome,
            message=message,
            stages=stages,
            failed_stage=stage,
        )


def summarize_transcript(
    *,
    transcript: TranscriptRecord | None,
    options: SummaryOptions | None = None,
    settings: Settings | None = None,
    service: SummarizationService | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> SummaryResult:
    """Generate a validated summary for a transcript (service-free entry point).

    This is the interface Step 4 and Step 5 should call.
    """
    active = service or SummarizationService(settings)
    return active.summarize(transcript, options, on_stage=on_stage)
