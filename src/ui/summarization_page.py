"""Summarisation page for VideoMind AI (Step 3).

The interface stays thin, exactly like the Step 2 transcription view: it gathers
preferences, calls :class:`SummarizationService`, and renders what came back. No
prompt text and no provider call lives here, and nothing runs on page load - a
summary is only generated when the user presses the button.

State rules:

* the transcript produced in Step 2 is never modified, not even when summarising
  fails, so the user can always retry;
* an existing stored summary is loaded from disk (never re-requested) when the
  transcript and settings still match;
* progress labels come from the real pipeline stages, and the run is refused while
  another run is in flight.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import streamlit as st

from config.settings import Settings, get_settings
from src.models.schemas import StageStatus, TranscriptMetadata, TranscriptRecord
from src.models.summary_schemas import (
    DEFAULT_SUMMARY_LENGTH,
    DEFAULT_SUMMARY_MODE,
    SUMMARY_LENGTH_CHOICES,
    SUMMARY_LENGTH_LABELS,
    SUMMARY_MODE_CHOICES,
    SUMMARY_MODE_DESCRIPTIONS,
    SUMMARY_MODE_LABELS,
    StructuredSummary,
    SummaryMetadata,
    SummaryMode,
    SummaryOptions,
    SummaryOutcome,
    SummaryResult,
    SummaryStatus,
)
from src.services.summarization_service import SummarizationService
from src.services.summary_storage import SummaryStorage, SummaryStorageError
from src.services.transcript_preprocessor import TranscriptPreprocessor
from src.services.transcript_service import TranscriptService
from src.services.transcript_storage import TranscriptStorageError
from src.ui import components as ui
from src.ui.transcription_view import TRANSCRIPT_ID_KEY
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_duration, format_timestamp

logger = logging.getLogger(__name__)

NOTICE_KEY = "summary_notice"
RESULT_KEY = "summary_result"
TRANSCRIPT_CHOICE_KEY = "summary_transcript_choice"
MODE_CHOICE_KEY = "summary_mode_choice"
LENGTH_CHOICE_KEY = "summary_length_choice"
LANGUAGE_KEY = "summary_language"
INSTRUCTION_KEY = "summary_instruction"
TIMESTAMPS_KEY = "summary_include_timestamps"
ACTIONS_KEY = "summary_extract_actions"
REUSE_KEY = "summary_reuse_saved"
RUNNING_KEY = "summary_running"
SAVED_CHOICE_KEY = "summary_saved_choice"
PREVIEW_SEGMENTS = 25

SETTINGS_CHANGED_MESSAGE = (
    "The saved summary was generated with different settings. Generate a new one to "
    "apply the current settings."
)


# --------------------------------------------------------------------------- state
def initialise_state() -> None:
    """Create the session keys used by the summarisation workflow."""
    defaults: dict[str, Any] = {
        NOTICE_KEY: None,
        RESULT_KEY: None,
        TRANSCRIPT_CHOICE_KEY: None,
        MODE_CHOICE_KEY: DEFAULT_SUMMARY_MODE,
        LENGTH_CHOICE_KEY: DEFAULT_SUMMARY_LENGTH,
        LANGUAGE_KEY: "",
        INSTRUCTION_KEY: "",
        TIMESTAMPS_KEY: True,
        ACTIONS_KEY: True,
        REUSE_KEY: True,
        RUNNING_KEY: False,
        SAVED_CHOICE_KEY: None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _set_notice(level: str, title: str, message: str) -> None:
    st.session_state[NOTICE_KEY] = {"level": level, "title": title, "message": message}


def _render_notice() -> None:
    notice = st.session_state.get(NOTICE_KEY)
    if not notice:
        return
    st.session_state[NOTICE_KEY] = None
    status = {
        "success": "ok",
        "warning": "warning",
        "error": "error",
    }.get(notice.get("level"), "warning")
    from src.models.schemas import HealthStatus

    ui.render_status_banner(
        {
            "ok": HealthStatus.OK,
            "warning": HealthStatus.WARNING,
            "error": HealthStatus.ERROR,
        }.get(status, HealthStatus.WARNING),
        notice.get("message", ""),
        notice.get("title"),
    )


# --------------------------------------------------------------------------- helpers
def stored_transcripts(settings: Settings) -> list[TranscriptMetadata]:
    """Return metadata for every readable transcript, newest first."""
    try:
        return TranscriptService(settings).list_transcripts()
    except Exception:  # noqa: BLE001 - listing must never break the page
        logger.warning("Stored transcripts could not be listed.")
        return []


def load_transcript(settings: Settings, transcript_id: str | None) -> TranscriptRecord | None:
    """Load one transcript, reporting (not raising) a storage problem."""
    if not transcript_id:
        return None
    try:
        return TranscriptService(settings).load(transcript_id)
    except (TranscriptStorageError, ValueError) as error:
        logger.warning("Transcript could not be loaded: %s", type(error).__name__)
        _set_notice(
            "error",
            "Transcript unavailable",
            f"{error} Re-run transcription on the Home page if the file is gone.",
        )
        return None


def transcript_label(metadata: TranscriptMetadata) -> str:
    """Readable selector label for a stored transcript."""
    title = (metadata.title or metadata.original_filename or "Untitled recording").strip()
    created = metadata.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    duration = format_duration(metadata.duration_seconds) if metadata.duration_seconds else "unknown length"
    return f"{title} · {created} · {duration} · {metadata.segment_count} segments"


def summary_options(
    *,
    mode: str,
    length: str,
    language: str | None = None,
    instruction: str | None = None,
    include_timestamps: bool = True,
    extract_action_items: bool = True,
    settings: Settings | None = None,
) -> SummaryOptions:
    """Build validated options from the interface controls.

    Raises :class:`pydantic.ValidationError` (via ``SummaryOptions``) when a custom
    summary has no instruction, so the caller can show a useful message.
    """
    resolved_settings = settings or get_settings()
    clean_language = (language or "").strip().lower() or None
    return SummaryOptions(
        mode=mode,
        length=length,
        language=clean_language,
        custom_instruction=instruction,
        include_timestamps=include_timestamps,
        extract_action_items=extract_action_items,
        temperature=resolved_settings.summary_temperature,
        max_tokens=resolved_settings.summary_max_tokens,
    )


def run_summarization(
    settings: Settings,
    transcript: TranscriptRecord,
    options: SummaryOptions,
    *,
    service: SummarizationService | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> SummaryResult:
    """Generate a summary through the service (the only provider call on this page)."""
    active = service or SummarizationService(settings)
    return active.summarize(transcript, options, on_stage=on_stage)


def estimate_requests(settings: Settings, transcript: TranscriptRecord, options: SummaryOptions) -> tuple[int, int]:
    """Return ``(chunk_count, estimated_transcript_tokens)`` without calling the model."""
    try:
        prepared = TranscriptPreprocessor(settings).prepare(
            transcript, include_timestamps=options.include_timestamps
        )
    except Exception:  # noqa: BLE001 - an estimate must never block the page
        logger.debug("Transcript size could not be estimated.")
        return 1, 0
    return prepared.chunk_count, prepared.tokens


def matching_saved_summary(
    settings: Settings, transcript: TranscriptRecord, options: SummaryOptions
) -> StructuredSummary | None:
    """Load the newest stored summary for this transcript, without a model call."""
    if not transcript.transcript_id:
        return None
    try:
        return SummaryStorage(settings.summary_dir).latest_for_transcript(
            transcript.transcript_id, options_fingerprint=options.fingerprint
        )
    except SummaryStorageError:
        logger.warning("Stored summaries could not be read.")
        return None


def saved_summaries_for(
    settings: Settings, transcript: TranscriptRecord | None
) -> list[SummaryMetadata]:
    """List stored summaries for one transcript (or all when none is selected)."""
    try:
        storage = SummaryStorage(settings.summary_dir)
        if transcript and transcript.transcript_id:
            return storage.list_metadata(transcript_id=transcript.transcript_id)
        return storage.list_metadata()
    except SummaryStorageError:
        logger.warning("Stored summaries could not be listed.")
        return []


# --------------------------------------------------------------------------- rendering
def render_summarization_page(settings: Settings) -> None:
    """Render the summarisation workflow."""
    initialise_state()
    ui.section_header(
        "Summarization",
        "Turn a Step 2 transcript into structured insights with NVIDIA Nemotron.",
    )
    _render_notice()

    transcripts = stored_transcripts(settings)
    if not transcripts:
        ui.render_status_banner(
            "warning",
            "No transcript is available yet. Upload a video or audio file on the Home page "
            "and run transcription first - summarisation works from that transcript.",
            "Transcript required",
        )
        if st.button("Go to the transcription workflow", key="summary_go_home"):
            from src.ui.layout import go_to_page

            go_to_page("home")
        return

    transcript = _render_transcript_selector(transcripts, settings)
    if transcript is None:
        return

    options, ready = _render_controls(settings, transcript)
    _render_generate_area(settings, transcript, options, ready)
    _render_result_area(settings, transcript)
    _render_saved_summaries(settings, transcript)


def _render_transcript_selector(
    transcripts: list[TranscriptMetadata], settings: Settings
) -> TranscriptRecord | None:
    """Render the transcript picker and a short preview; return the loaded record."""
    active_id = st.session_state.get(TRANSCRIPT_ID_KEY)
    identifiers = [item.transcript_id for item in transcripts if item.transcript_id]
    if not identifiers:
        ui.render_status_banner(
            "error",
            "Stored transcripts could not be read. Check that the data directory is writable.",
            "Transcripts unavailable",
        )
        return None

    if st.session_state.get(TRANSCRIPT_CHOICE_KEY) not in identifiers:
        st.session_state[TRANSCRIPT_CHOICE_KEY] = active_id if active_id in identifiers else identifiers[0]

    labels = {item.transcript_id: transcript_label(item) for item in transcripts if item.transcript_id}
    chosen = st.selectbox(
        "Transcript",
        options=identifiers,
        format_func=lambda value: labels.get(value, value),
        key=TRANSCRIPT_CHOICE_KEY,
        help="Transcripts produced by the Step 2 pipeline, newest first.",
    )

    record = load_transcript(settings, chosen)
    if record is None:
        return None

    ui.render_key_values(
        [
            ("Source", record.source_reference or record.original_filename or "unknown"),
            ("Language", record.language or "not reported"),
            ("Whisper model", record.transcription_model or "unknown"),
            ("Duration", format_duration(record.duration_seconds) if record.duration_seconds else "unknown"),
            ("Segments", str(record.segment_count)),
        ]
    )

    with st.expander("Preview the transcript", expanded=False):
        if record.segments:
            preview = record.segments[:PREVIEW_SEGMENTS]
            lines = [
                f"{format_timestamp(segment.start_seconds)}  {segment.text}"
                if segment.has_timestamps
                else f"{UNAVAILABLE_TIMESTAMP}  {segment.text}"
                for segment in preview
            ]
            st.text("\n".join(lines))
            hidden = record.segment_count - len(preview)
            if hidden > 0:
                st.caption(f"{hidden} further segment(s) are not shown.")
        else:
            st.text((record.full_text or "")[:4000] or "This transcript has no text.")
    return record


def _render_controls(
    settings: Settings, transcript: TranscriptRecord
) -> tuple[SummaryOptions, bool]:
    """Render the summary controls; returns ``(options, can_generate)``."""
    ui.section_header("Summary settings", "Choose the style and the level of detail.")
    mode_column, length_column = st.columns(2)
    with mode_column:
        mode = st.selectbox(
            "Summary style",
            options=list(SUMMARY_MODE_CHOICES),
            format_func=lambda value: SUMMARY_MODE_LABELS.get(value, value),
            key=MODE_CHOICE_KEY,
        )
    with length_column:
        length = st.selectbox(
            "Detail level",
            options=list(SUMMARY_LENGTH_CHOICES),
            format_func=lambda value: SUMMARY_LENGTH_LABELS.get(value, value),
            key=LENGTH_CHOICE_KEY,
        )

    st.caption(SUMMARY_MODE_DESCRIPTIONS.get(mode, ""))

    language = st.text_input(
        "Output language (optional)",
        key=LANGUAGE_KEY,
        placeholder="ISO code such as en, de, hi - leave empty to match the transcript",
        help="Only the summary language changes; quotes from the recording stay in the original language.",
    )

    instruction = ""
    if mode == SummaryMode.CUSTOM.value:
        instruction = st.text_area(
            "Custom instruction",
            key=INSTRUCTION_KEY,
            placeholder="Explain the technical concepts in beginner-friendly language.",
            help=(
                "Describe what the summary should focus on. This steers emphasis and tone only - "
                "it can never make the assistant invent facts."
            ),
            max_chars=settings.summary_max_custom_instruction_chars,
        )
    else:
        instruction = st.text_area(
            "Extra instruction (optional)",
            key=INSTRUCTION_KEY,
            placeholder="Focus on the practical lessons and examples.",
            max_chars=settings.summary_max_custom_instruction_chars,
        )

    toggle_column, action_column = st.columns(2)
    with toggle_column:
        include_timestamps = st.checkbox(
            "Include source timestamps", key=TIMESTAMPS_KEY
        )
    with action_column:
        extract_actions = st.checkbox("Extract action items", key=ACTIONS_KEY)
    st.checkbox(
        "Reuse a saved summary when the transcript and settings are unchanged",
        key=REUSE_KEY,
        help="Avoids paying for an identical request twice.",
    )

    # Validate the controls *before* anything else uses the options: building them is
    # what rejects an empty custom instruction, and that must be a message rather than
    # a page error.
    try:
        options = summary_options(
            mode=mode,
            length=length,
            language=language,
            instruction=instruction,
            include_timestamps=include_timestamps,
            extract_action_items=extract_actions,
            settings=settings,
        )
        ready = True
    except Exception:  # noqa: BLE001 - a validation problem is a user-facing message
        ui.render_status_banner(
            "warning",
            "A custom summary needs an instruction. Describe what the summary should "
            "contain, then generate it.",
            "Instruction required",
        )
        options = summary_options(
            mode=DEFAULT_SUMMARY_MODE,
            length=length,
            language=language,
            instruction="",
            include_timestamps=include_timestamps,
            extract_action_items=extract_actions,
            settings=settings,
        )
        ready = False

    chunk_count, tokens = estimate_requests(settings, transcript, options)
    if chunk_count > 1:
        st.caption(
            f"This transcript is about {tokens:,} tokens, so it will be processed in "
            f"{chunk_count} requests and merged afterwards. Every section is summarised "
            "separately, and any section that fails is reported."
        )
    else:
        st.caption(
            f"This transcript is about {tokens:,} tokens and fits in one request."
        )

    return options, ready


def _render_generate_area(
    settings: Settings,
    transcript: TranscriptRecord,
    options: SummaryOptions,
    ready: bool,
) -> None:
    """Render the explicit generate/regenerate buttons and run the summary."""
    reuse = bool(st.session_state.get(REUSE_KEY, True))
    saved = matching_saved_summary(settings, transcript, options) if reuse else None

    button_column, note_column = st.columns([1, 3])
    with button_column:
        if saved is not None and reuse:
            pressed = st.button("Regenerate anyway", key="summary_regenerate", disabled=not ready)
        else:
            pressed = st.button(
                "Generate summary",
                key="summary_generate",
                type="primary",
                disabled=not ready,
            )
    with note_column:
        if saved is not None and reuse:
            st.caption(
                "A saved summary from these exact settings already exists. Use "
                "**Open this summary** below to read it, or regenerate only if you want "
                "the model to run again."
            )
        elif not settings.has_nvidia_credentials:
            st.caption("NVIDIA_API_KEY is not configured; generation will report that instead of failing.")

    if not pressed:
        return
    if st.session_state.get(RUNNING_KEY):
        st.info("A summary is already being generated.", icon="⏳")
        return

    st.session_state[RUNNING_KEY] = True
    try:
        with st.status("Generating summary", expanded=True) as status_box:
            status_box.write("Starting the summarisation pipeline")

            def on_stage(label: str) -> None:
                status_box.write(label)

            result = run_summarization(settings, transcript, options, on_stage=on_stage)
            if result.succeeded:
                status_box.update(
                    label="Summary ready" if not result.is_partial else "Partial summary ready",
                    state="complete" if not result.is_partial else "error",
                )
            else:
                status_box.update(label="Summary not generated", state="error")
        st.session_state[RESULT_KEY] = {
            "transcript_id": transcript.transcript_id,
            "result": result,
            "loaded_from_storage": False,
        }
        if result.succeeded:
            _set_notice(
                "success" if not result.is_partial else "warning",
                "Summary ready" if not result.is_partial else "Partial summary",
                result.message,
            )
        else:
            _set_notice("error", "Summary not generated", result.message)
        logger.info(
            "Summary run finished (outcome=%s, partial=%s)",
            result.outcome.value,
            result.is_partial,
        )
    finally:
        st.session_state[RUNNING_KEY] = False


def _render_result_area(settings: Settings, transcript: TranscriptRecord) -> None:
    """Render the active result (a fresh run or a summary loaded from storage)."""
    stored = st.session_state.get(RESULT_KEY)
    if not isinstance(stored, dict):
        return
    result = stored.get("result")
    if not isinstance(result, SummaryResult) or result.summary is None:
        if isinstance(result, SummaryResult):
            _render_failure(result)
        return

    ui.section_header("Result")
    if stored.get("loaded_from_storage"):
        st.caption("Loaded from a saved summary. No new model request was made.")
    render_summary(result, transcript)
    if result.stages:
        with st.expander("Pipeline stages", expanded=False):
            _render_stages(result)


def _render_failure(result: SummaryResult) -> None:
    """Show an honest explanation of a run that produced no summary."""
    ui.render_status_banner("error", result.message, "No summary was generated")
    if result.stages:
        _render_stages(result)
    if result.outcome is SummaryOutcome.MISSING_CREDENTIALS:
        ui.render_bullets(
            [
                "Add NVIDIA_API_KEY to your local .env file and restart the application.",
                "The transcript is unaffected and can be summarised once the key is configured.",
            ]
        )


def _render_stages(result: SummaryResult) -> None:
    """Render the real pipeline stages (never synthetic progress)."""
    from src.models.schemas import PipelineStageReport

    ui.render_pipeline_stages(
        [
            PipelineStageReport(stage=entry.stage, status=entry.status, detail=entry.detail)
            for entry in result.stages
        ]
    )


def render_summary(result: SummaryResult, transcript: TranscriptRecord) -> None:
    """Render a validated structured summary."""
    summary = result.summary
    if summary is None:  # pragma: no cover - guarded by the caller
        return

    if result.is_partial or summary.processing_status is SummaryStatus.PARTIAL:
        ui.render_status_banner(
            "warning", summary.coverage.describe(), "This summary is incomplete"
        )
    elif result.outcome is SummaryOutcome.STORAGE_FAILED:
        ui.render_status_banner("warning", result.message, "Not saved")
    else:
        ui.render_status_banner(
            "ok",
            f"{SUMMARY_MODE_LABELS.get(summary.summary_type.value, summary.summary_type.value)} "
            f"generated from {transcript.segment_count} transcript segments.",
            "Summary ready",
        )

    ui.render_key_values(
        [
            ("Source", summary.source_title or "untitled"),
            ("Summary type", SUMMARY_MODE_LABELS.get(summary.summary_type.value, summary.summary_type.value)),
            ("Model", summary.generation_model or "unknown"),
            ("Generated", summary.generated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")),
            ("Requests", str(summary.model_calls)),
            (
                "Processing time",
                f"{summary.latency_ms / 1000:.1f} s" if summary.latency_ms else "unknown",
            ),
            ("Coverage", summary.coverage.describe()),
            ("Summary id", summary.summary_id or "not stored"),
        ]
    )

    if summary.limitations:
        with st.expander("Warnings and limitations", expanded=True):
            ui.render_bullets(summary.limitations)

    tabs = st.tabs(
        ["Overview", "Key takeaways", "Topics", "Chapters", "Action items & decisions", "Raw data"]
    )
    with tabs[0]:
        if summary.short_summary:
            st.markdown(f"**In short** - {summary.short_summary}")
        st.markdown(summary.overall_summary or "_The model returned no overall summary._")
        if summary.important_points:
            ui.section_header("Important points")
            for point in summary.important_points:
                st.markdown(f"- **{_stamp(point.start_seconds)}** {point.point}")
                if point.explanation:
                    st.caption(point.explanation)
    with tabs[1]:
        _render_takeaways(summary)
    with tabs[2]:
        _render_topics(summary)
    with tabs[3]:
        _render_chapters(summary)
    with tabs[4]:
        _render_actions(summary)
    with tabs[5]:
        st.caption(
            "The validated summary object. Step 5 will add TXT, Markdown, PDF and DOCX exports "
            "built from exactly this structure."
        )
        st.code(summary.model_dump_json(indent=2), language="json")
        st.download_button(
            "Download this JSON",
            data=summary.model_dump_json(indent=2),
            file_name=f"{summary.summary_id or 'summary'}.json",
            mime="application/json",
            key="summary_download_json",
        )
        if summary.coverage.failed_chunk_indexes:
            ui.render_bullets(
                [
                    f"Section {index + 1} of {summary.coverage.chunks_total} could not be summarised."
                    for index in summary.coverage.failed_chunk_indexes
                ]
            )


def _stamp(seconds: float | None) -> str:
    """Format an optional timestamp for inline display."""
    return format_timestamp(seconds) if seconds is not None else UNAVAILABLE_TIMESTAMP


def _render_takeaways(summary: StructuredSummary) -> None:
    if not summary.key_takeaways:
        st.caption("No separate key takeaways were extracted for this recording.")
        return
    for item in summary.key_takeaways:
        st.markdown(f"**{item.title}** · `{_stamp(item.start_seconds)}`")
        if item.explanation:
            st.markdown(item.explanation)
        if item.excerpt:
            st.caption(f"From the transcript: “{item.excerpt}”")


def _render_topics(summary: StructuredSummary) -> None:
    if not summary.main_topics:
        st.caption("The model did not identify separate topics.")
        return
    for topic in summary.main_topics:
        st.markdown(f"**{topic.title}** · `{_stamp(topic.start_seconds)}`")
        if topic.explanation:
            st.markdown(topic.explanation)
        ui.render_bullets(topic.key_points)


def _render_chapters(summary: StructuredSummary) -> None:
    if not summary.chapters:
        st.caption("This summary has no chapter breakdown.")
        return
    for chapter in summary.chapters:
        label = _stamp(chapter.start_seconds)
        if chapter.end_seconds is not None and chapter.start_seconds is not None:
            label = f"{label} - {format_timestamp(chapter.end_seconds)}"
        st.markdown(f"**{label} · {chapter.title}**")
        if chapter.description:
            st.markdown(chapter.description)
        ui.render_bullets(chapter.major_points)


def _render_actions(summary: StructuredSummary) -> None:
    if summary.action_items:
        ui.section_header("Action items")
        for item in summary.action_items:
            details = [part for part in (item.owner, item.deadline) if part]
            suffix = f" _({', '.join(details)})_" if details else ""
            st.markdown(f"- **{_stamp(item.start_seconds)}** {item.task}{suffix}")
        st.caption(
            "An owner or deadline appears only when the transcript states it; the transcript is "
            "not diarised, so no statement is attributed to a named person."
        )
    else:
        st.caption("No clear action items were identified in this recording.")

    if summary.decisions:
        ui.section_header("Decisions")
        for decision in summary.decisions:
            st.markdown(f"- **{_stamp(decision.start_seconds)}** {decision.decision}")
            if decision.context:
                st.caption(decision.context)
    else:
        st.caption("No explicit decisions were recorded in this transcript.")


def _render_saved_summaries(settings: Settings, transcript: TranscriptRecord) -> None:
    """Let the user reopen or delete a previously generated summary."""
    saved = saved_summaries_for(settings, transcript)
    if not saved:
        return
    ui.section_header("Saved summaries", "Stored on disk; opening one makes no model request.")
    labels = {
        item.summary_id: (
            f"{SUMMARY_MODE_LABELS.get(item.summary_type.value, item.summary_type.value)} · "
            f"{item.created_at.astimezone().strftime('%Y-%m-%d %H:%M')} · "
            f"{item.processing_status.value}"
            + (f" · {item.preview[:60]}" if item.preview else "")
        )
        for item in saved
        if item.summary_id
    }
    identifiers = list(labels)
    if st.session_state.get(SAVED_CHOICE_KEY) not in identifiers:
        st.session_state[SAVED_CHOICE_KEY] = identifiers[0]

    open_column, delete_column = st.columns([1, 1])
    with open_column:
        chosen = st.selectbox(
            "Saved summary",
            options=identifiers,
            format_func=lambda value: labels.get(value, value),
            key=SAVED_CHOICE_KEY,
        )
        if st.button("Open this summary", key="summary_open_saved"):
            _open_saved_summary(settings, transcript, chosen)
    with delete_column:
        if st.button("Delete this summary", key="summary_delete_saved"):
            _delete_saved_summary(settings, chosen)


def _open_saved_summary(settings: Settings, transcript: TranscriptRecord, summary_id: str) -> None:
    """Load a stored summary into the session (no provider call)."""
    try:
        summary = SummaryStorage(settings.summary_dir).load(summary_id)
    except SummaryStorageError as error:
        _set_notice("error", "Summary unavailable", str(error))
        return
    options = SummaryOptions(mode=summary.summary_type, length=DEFAULT_SUMMARY_LENGTH)
    st.session_state[RESULT_KEY] = {
        "transcript_id": transcript.transcript_id,
        "result": SummaryResult(
            outcome=SummaryOutcome.SUCCESS
            if summary.processing_status is SummaryStatus.COMPLETED
            else SummaryOutcome.PARTIAL,
            message="Loaded from a saved summary.",
            summary=summary,
        ),
        "loaded_from_storage": True,
    }
    if summary.options_fingerprint != options.fingerprint:
        _set_notice("warning", "Saved summary opened", SETTINGS_CHANGED_MESSAGE)
    else:
        _set_notice("success", "Saved summary opened", "No new model request was made.")
    # The result area is rendered before this section, so without a rerun the stored
    # summary would only appear after the user interacted with the page again.
    st.rerun()


def _delete_saved_summary(settings: Settings, summary_id: str) -> None:
    """Delete one stored summary after an explicit click."""
    try:
        removed = SummaryStorage(settings.summary_dir).delete(summary_id)
    except SummaryStorageError as error:
        _set_notice("error", "Summary not deleted", str(error))
        return
    if st.session_state.get(RESULT_KEY, {}).get("result", None):
        current = st.session_state[RESULT_KEY].get("result")
        if current and current.summary and current.summary.summary_id == summary_id:
            st.session_state[RESULT_KEY] = None
    st.session_state[SAVED_CHOICE_KEY] = None
    _set_notice(
        "success" if removed else "warning",
        "Summary deleted" if removed else "Nothing to delete",
        "The stored summary was removed." if removed else "That summary was already gone.",
    )
    st.rerun()
