"""Processing orchestration: source to structured transcript (Step 2).

The interface never performs media work itself; it hands a :class:`ValidatedSource`
to :class:`VideoPipeline`, which validates input, acquires media, inspects it,
extracts audio, transcribes, validates and stores the transcript, and always
cleans up its own temporary artefacts - even when something fails.

Failures keep their meaning: each stage maps to a specific
:class:`PipelineOutcome` instead of collapsing into a generic error.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from src.models.schemas import (
    IngestionKind,
    MediaInfo,
    PipelineOutcome,
    PipelineResult,
    PipelineStageReport,
    StageStatus,
    TranscriptionOptions,
    ValidatedSource,
)
from src.services import media_service, url_ingestion_service
from src.services.source_service import SourceValidationError, is_inside
from src.services.transcript_service import TranscriptService
from src.services.transcript_storage import TranscriptStorageError
from src.services.transcription_service import (
    EmptyTranscriptError,
    InvalidTranscriptionError,
    TranscriptionService,
    TranscriptionServiceError,
    WhisperUnavailableError,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

STAGE_VALIDATE = "Validating input"
STAGE_ACQUIRE = "Acquiring media"
STAGE_INSPECT = "Inspecting media"
STAGE_EXTRACT = "Extracting audio"
STAGE_TRANSCRIBE = "Transcribing"
STAGE_STORE = "Saving transcript"
STAGE_CLEANUP = "Cleaning up"

#: Stages the interface displays, in order.
PIPELINE_STAGES: tuple[str, ...] = (
    STAGE_VALIDATE,
    STAGE_ACQUIRE,
    STAGE_INSPECT,
    STAGE_EXTRACT,
    STAGE_TRANSCRIBE,
    STAGE_STORE,
)

#: Which outcome to report when an unexpected error appears in a given stage.
_OUTCOME_BY_STAGE = {
    STAGE_VALIDATE: PipelineOutcome.INVALID_INPUT,
    STAGE_ACQUIRE: PipelineOutcome.DOWNLOAD_FAILED,
    STAGE_INSPECT: PipelineOutcome.MEDIA_INSPECTION_FAILED,
    STAGE_EXTRACT: PipelineOutcome.AUDIO_EXTRACTION_FAILED,
    STAGE_TRANSCRIBE: PipelineOutcome.TRANSCRIPTION_FAILED,
    STAGE_STORE: PipelineOutcome.STORAGE_FAILED,
    STAGE_CLEANUP: PipelineOutcome.SUCCESS,
}

UPLOAD_NOT_FOUND_MESSAGE = "The uploaded file is no longer available. Please select it again."

#: Errors we raise deliberately: they already carry safe, actionable messages.
_EXPECTED_ERRORS = (
    SourceValidationError,
    media_service.MediaToolingError,
    media_service.MediaInspectionError,
    media_service.AudioExtractionError,
    TranscriptionServiceError,
    TranscriptStorageError,
    url_ingestion_service.UrlIngestionError,
)


def job_key_for(source: ValidatedSource) -> str:
    """Stable identifier for a job so the same source is not processed twice."""
    parts = [source.kind.value, source.reference, str(source.size_bytes or "")]
    if source.download_strategy:
        parts.append(source.download_strategy)
    return "|".join(parts)


class JobRegistry:
    """Session-scoped guard against duplicate processing.

    Streamlit re-runs the script on every interaction; this registry ensures a
    source that is already running - or already transcribed in this session - is
    not processed again by accident. It is deliberately not process-global: the
    interface keeps one instance per user session.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._started_at: dict[str, float] = {}
        self._completed: dict[str, str] = {}

    def is_running(self, job_key: str) -> bool:
        """True while a job for this key is in flight."""
        with self._lock:
            return job_key in self._running

    def running_for(self, job_key: str) -> float | None:
        """Seconds since this key was marked running, or ``None`` if it is not."""
        with self._lock:
            started = self._started_at.get(job_key)
            return None if started is None else max(0.0, time.monotonic() - started)

    def completed_transcript(self, job_key: str) -> str | None:
        """Return the transcript id produced earlier for this key, if any."""
        with self._lock:
            return self._completed.get(job_key)

    def start(self, job_key: str) -> bool:
        """Mark a job as running; False when it was already running."""
        with self._lock:
            if job_key in self._running:
                return False
            self._running.add(job_key)
            self._started_at[job_key] = time.monotonic()
            return True

    def finish(self, job_key: str, transcript_id: str | None = None) -> None:
        """Clear the running flag and remember a successful result."""
        with self._lock:
            self._running.discard(job_key)
            self._started_at.pop(job_key, None)
            if transcript_id:
                self._completed[job_key] = transcript_id

    def forget(self, job_key: str) -> None:
        """Allow a source to be processed again (for example after deletion)."""
        with self._lock:
            self._completed.pop(job_key, None)
            self._running.discard(job_key)
            self._started_at.pop(job_key, None)

    def reset_stale(self, max_seconds: float) -> list[str]:
        """Clear locks older than ``max_seconds`` and return the cleared keys.

        An interrupted script (browser closed, process killed) can leave a job marked
        running. The lock is then stale: no worker exists to clear it. A generous
        bound is used so a genuinely long job is never stolen from.
        """
        if max_seconds <= 0:
            return []
        now = time.monotonic()
        with self._lock:
            stale = [
                key
                for key, started in self._started_at.items()
                if key in self._running and now - started >= max_seconds
            ]
            for key in stale:
                self._running.discard(key)
                self._started_at.pop(key, None)
        return sorted(stale)

    def running_keys(self) -> list[str]:
        """Return the keys currently marked running (used to offer a recovery action)."""
        with self._lock:
            return sorted(self._running)

    def reset_running(self) -> list[str]:
        """Clear every running lock after an explicit user request; return the keys."""
        with self._lock:
            cleared = sorted(self._running)
            self._running.clear()
            self._started_at.clear()
        return cleared

    def forget_transcript(self, transcript_id: str) -> None:
        """Forget completions pointing at a transcript that no longer exists."""
        with self._lock:
            for key, value in list(self._completed.items()):
                if value == transcript_id:
                    self._completed.pop(key, None)


class VideoPipeline:
    """Coordinate ingestion, audio extraction, transcription and storage."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transcriber: TranscriptionService | None = None,
        transcripts: TranscriptService | None = None,
        media_probe: Callable[..., MediaInfo] | None = None,
        media_prepare_audio: Callable[..., tuple[Path, bool]] | None = None,
        media_tooling: Callable[..., Any] | None = None,
        url_download: Callable[..., Any] | None = None,
        duration_limiter: Callable[..., None] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._transcriber = transcriber or TranscriptionService(self._settings)
        self._transcripts = transcripts or TranscriptService(self._settings)
        self._probe = media_probe or media_service.probe_media
        self._prepare_audio = (
            media_prepare_audio or media_service.prepare_audio_for_transcription
        )
        self._tooling = media_tooling or media_service.detect_media_tooling
        self._download = url_download or url_ingestion_service.download_media
        self._enforce_duration = duration_limiter or media_service.enforce_duration_limit

    @property
    def settings(self) -> Settings:
        """Return the settings used by the pipeline."""
        return self._settings

    @property
    def transcriber(self) -> TranscriptionService:
        """Return the transcription service (used by the interface for readiness)."""
        return self._transcriber

    @property
    def transcripts(self) -> TranscriptService:
        """Return the transcript service (Steps 3-5 read transcripts through it)."""
        return self._transcripts

    # ------------------------------------------------------------------ main entry
    def run(
        self,
        source: ValidatedSource,
        *,
        options: TranscriptionOptions | None = None,
        progress: ProgressCallback | None = None,
        store: bool = True,
    ) -> PipelineResult:
        """Process one validated source and return a structured result."""
        stages: list[PipelineStageReport] = []
        current_stage = STAGE_VALIDATE
        # The pipeline owns this directory: everything written into it (downloads,
        # extracted audio) is removed in `finally`, on success and on failure alike.
        job_dir = Path(self._settings.media_temp_dir) / f"job-{uuid.uuid4().hex[:12]}"
        job_dir.mkdir(parents=True, exist_ok=True)
        title = source.title

        def report(stage: str, status: StageStatus, detail: str | None = None) -> None:
            stages.append(PipelineStageReport(stage=stage, status=status, detail=detail))

        try:
            # 1 - input validation -------------------------------------------------
            _notify(progress, f"{STAGE_VALIDATE}...")
            media_path = self._resolve_source_path(source)
            tooling = self._tooling(self._settings)
            if not getattr(tooling, "available", False):
                raise media_service.MediaToolingError(media_service.FFMPEG_SETUP_MESSAGE)
            report(STAGE_VALIDATE, StageStatus.COMPLETED, detail=_short(source.reference))

            # 2 - acquisition ------------------------------------------------------
            current_stage = STAGE_ACQUIRE
            _notify(progress, f"{STAGE_ACQUIRE}...")
            if source.kind is IngestionKind.URL:
                # The job directory is removed in `finally`, so the downloaded media
                # never outlives the run that needed it.
                downloaded = self._download(
                    source.url or "",
                    self._settings,
                    progress=progress,
                    directory=job_dir,
                )
                media_path = Path(downloaded.path)
                title = downloaded.title or title
                report(
                    STAGE_ACQUIRE,
                    StageStatus.COMPLETED,
                    detail=f"{downloaded.strategy} download ({downloaded.size_bytes} bytes)",
                )
            else:
                report(STAGE_ACQUIRE, StageStatus.COMPLETED, detail="Local upload (no download)")

            # 3 - inspection -------------------------------------------------------
            current_stage = STAGE_INSPECT
            _notify(progress, f"{STAGE_INSPECT}...")
            info = self._probe(media_path, self._settings)
            self._enforce_duration(info, self._settings)
            report(STAGE_INSPECT, StageStatus.COMPLETED, detail=_describe_media(info))

            # 4 - audio preparation (also detects a missing audio track) -----------
            current_stage = STAGE_EXTRACT
            _notify(progress, f"{STAGE_EXTRACT}...")
            audio_path, extracted = self._prepare_audio(
                media_path, job_dir, self._settings, info=info
            )
            report(
                STAGE_EXTRACT,
                StageStatus.COMPLETED,
                detail=(
                    "Extracted 16 kHz mono audio for transcription"
                    if extracted
                    else "Audio source used directly"
                ),
            )

            # 5 - transcription ----------------------------------------------------
            current_stage = STAGE_TRANSCRIBE
            transcription = self._transcriber.transcribe(
                audio_path,
                options,
                progress=progress,
                audio_duration_seconds=info.duration_seconds,
            )
            report(
                STAGE_TRANSCRIBE,
                StageStatus.COMPLETED,
                detail=(
                    f"{len(transcription.segments)} segment(s) with model "
                    f"'{transcription.model_name}' on {transcription.device}"
                ),
            )

            # 6 - build and store the transcript -----------------------------------
            current_stage = STAGE_STORE
            _notify(progress, f"{STAGE_STORE}...")
            record = self._transcripts.build_record(
                source=source, result=transcription, media=info, title=title
            )
            if store:
                record = self._transcripts.save(record)
                self._transcripts.prune_old_transcripts()
            else:
                record = record.model_copy(update={"transcript_id": None})
            report(
                STAGE_STORE,
                StageStatus.COMPLETED,
                detail=f"Transcript {record.transcript_id or 'kept in memory'}",
            )

            return PipelineResult(
                outcome=PipelineOutcome.SUCCESS,
                message=(
                    f"Transcribed {record.segment_count} segment(s) with Whisper "
                    f"'{transcription.model_name}' on {transcription.device}."
                ),
                stages=_finalise_stages(stages),
                transcript=record,
            )

        except Exception as error:  # noqa: BLE001 - every failure maps to an outcome
            outcome, message = self._translate(error, current_stage)
            report(current_stage, StageStatus.FAILED, detail=message)
            if isinstance(error, _EXPECTED_ERRORS):
                logger.info(
                    "Processing failed (stage=%s, outcome=%s, error=%s)",
                    current_stage,
                    outcome.value,
                    type(error).__name__,
                )
            else:
                logger.exception("Unexpected processing error in stage '%s'", current_stage)
            return PipelineResult(
                outcome=outcome,
                message=message,
                stages=_finalise_stages(stages),
                failed_stage=current_stage,
            )
        finally:
            self._cleanup_job(job_dir)
            _notify(progress, f"{STAGE_CLEANUP}...")

    # ------------------------------------------------------------------ helpers
    def _resolve_source_path(self, source: ValidatedSource) -> Path:
        """Verify that an uploaded source still exists where it was stored."""
        if source.kind is not IngestionKind.UPLOAD:
            return Path()
        if not source.upload_path:
            raise SourceValidationError(UPLOAD_NOT_FOUND_MESSAGE)
        path = Path(source.upload_path)
        managed = is_inside(path, self._settings.upload_dir) or is_inside(
            path, self._settings.media_temp_dir
        )
        if not managed:
            raise SourceValidationError(
                "The media source is not managed by this application. Upload the file again."
            )
        if not path.is_file():
            raise SourceValidationError(UPLOAD_NOT_FOUND_MESSAGE)
        return path

    def _cleanup_job(self, job_dir: Path) -> None:
        """Remove the job's temporary directory (extracted audio, downloads)."""
        try:
            if job_dir.exists() and is_inside(job_dir, self._settings.media_temp_dir):
                shutil.rmtree(job_dir, ignore_errors=True)
        except OSError:  # pragma: no cover - defensive
            logger.warning("Temporary job directory could not be removed.")

    def _translate(self, error: Exception, stage: str) -> tuple[PipelineOutcome, str]:
        """Map an exception onto an outcome plus a user-safe message."""
        if isinstance(error, (EmptyTranscriptError, InvalidTranscriptionError)):
            return PipelineOutcome.TRANSCRIPT_VALIDATION_FAILED, str(error)
        if isinstance(error, SourceValidationError):
            return PipelineOutcome.INVALID_INPUT, str(error)
        if isinstance(
            error,
            (url_ingestion_service.UnsupportedUrlError, url_ingestion_service.BlockedDestinationError),
        ):
            return PipelineOutcome.INVALID_INPUT, str(error)
        if isinstance(error, media_service.MediaToolingError):
            return PipelineOutcome.TOOLING_MISSING, str(error)
        if isinstance(error, WhisperUnavailableError):
            return PipelineOutcome.TOOLING_MISSING, str(error)
        if isinstance(error, media_service.MediaInspectionError):
            return PipelineOutcome.MEDIA_INSPECTION_FAILED, str(error)
        if isinstance(error, media_service.AudioExtractionError):
            return PipelineOutcome.AUDIO_EXTRACTION_FAILED, str(error)
        if isinstance(error, TranscriptionServiceError):
            return PipelineOutcome.TRANSCRIPTION_FAILED, str(error)
        if isinstance(error, TranscriptStorageError):
            return PipelineOutcome.STORAGE_FAILED, str(error)
        if isinstance(error, url_ingestion_service.UrlIngestionError):
            return PipelineOutcome.DOWNLOAD_FAILED, str(error)

        outcome = _OUTCOME_BY_STAGE.get(stage, PipelineOutcome.INVALID_INPUT)
        return (
            outcome,
            "Processing stopped unexpectedly. Check logs/app.log for the technical details.",
        )


def _finalise_stages(reported: list[PipelineStageReport]) -> list[PipelineStageReport]:
    """Return one entry per user-visible stage, marking unreached ones as skipped."""
    by_stage = {entry.stage: entry for entry in reported}
    return [
        by_stage.get(stage, PipelineStageReport(stage=stage, status=StageStatus.SKIPPED))
        for stage in PIPELINE_STAGES
    ]


def _describe_media(info: MediaInfo) -> str:
    parts: list[str] = []
    if info.duration_seconds is not None:
        parts.append(f"{info.duration_seconds:.1f}s")
    if info.container_format:
        parts.append(info.container_format)
    if info.audio_codec:
        parts.append(f"audio: {info.audio_codec}")
    if info.has_video:
        parts.append("video track")
    return ", ".join(parts) or "media information read"


def _short(value: str, limit: int = 200) -> str:
    text = (value or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _notify(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)
