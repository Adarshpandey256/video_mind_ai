"""Transcription workflow for the Home page (Step 2).

The interface stays thin: it validates what the user typed, calls
:func:`execute_transcription` (which drives :class:`VideoPipeline`), and renders
honest stage-level progress plus the resulting timestamped transcript.

No fake transcripts, no invented percentages, and no media kept after a job
finishes - only the structured transcript document is retained.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import streamlit as st

from config.settings import WHISPER_MODEL_CHOICES, Settings, get_settings
from src.models.schemas import (
    PipelineOutcome,
    PipelineStageReport,
    TranscriptMetadata,
    TranscriptRecord,
    TranscriptionOptions,
    ValidatedSource,
)
from src.services import source_service, url_ingestion_service
from src.services.media_service import FFMPEG_SETUP_MESSAGE, detect_media_tooling
from src.services.source_service import SourceValidationError
from src.services.transcript_service import TranscriptService
from src.services.transcript_storage import TranscriptStorageError
from src.services.transcription_service import (
    TranscriptionService,
    cuda_available_cached,
    whispered_language_names,
)
from src.services.url_ingestion_service import (
    UrlIngestionError,
    build_url_source,
    strategy_explanation,
    validate_url_syntax,
)
from src.services.video_pipeline import JobRegistry, VideoPipeline, job_key_for
from src.ui import components as ui
from src.utils.file_utils import guess_source_type, human_readable_size
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_duration, format_timestamp

logger = logging.getLogger(__name__)

NOTICE_KEY = "transcription_notice"
TRANSCRIPT_ID_KEY = "active_transcript_id"
STAGES_KEY = "last_job_stages"
OUTCOME_KEY = "last_job_outcome"
REGISTRY_KEY = "job_registry"
SAVED_CHOICE_KEY = "saved_transcript_choice"
MODEL_CHOICE_KEY = "whisper_model_choice"
LANGUAGE_CHOICE_KEY = "whisper_language_choice"
DEVICE_CHOICE_KEY = "whisper_device_choice"
UPLOAD_KEY = "media_upload"
URL_KEY = "video_url"

SEGMENTS_PER_BLOCK = 40
GENERIC_FAILURE_MESSAGE = (
    "Processing stopped unexpectedly. Check logs/app.log for the technical details."
)

JobStatus = Literal["success", "failed", "invalid_input", "duplicate", "running"]


@dataclass
class TranscriptionJobOutcome:
    """Result of a user-triggered transcription attempt, for the interface."""

    status: JobStatus
    message: str
    transcript_id: str | None = None
    stages: list[PipelineStageReport] = field(default_factory=list)
    outcome: PipelineOutcome | None = None

    @property
    def succeeded(self) -> bool:
        """True only when a validated transcript was produced and stored."""
        return self.status == "success" and bool(self.transcript_id)


# --------------------------------------------------------------------------- state
def initialise_state() -> None:
    """Create the session keys used by the transcription workflow."""
    st.session_state.setdefault(NOTICE_KEY, None)
    st.session_state.setdefault(TRANSCRIPT_ID_KEY, None)
    st.session_state.setdefault(STAGES_KEY, [])
    st.session_state.setdefault(OUTCOME_KEY, None)
    st.session_state.setdefault(REGISTRY_KEY, JobRegistry())


def _registry() -> JobRegistry:
    registry = st.session_state.get(REGISTRY_KEY)
    if not isinstance(registry, JobRegistry):
        registry = JobRegistry()
        st.session_state[REGISTRY_KEY] = registry
    return registry


def _set_notice(level: str, title: str, message: str) -> None:
    st.session_state[NOTICE_KEY] = {"level": level, "title": title, "message": message}


# --------------------------------------------------------------------------- job logic
def execute_transcription(
    settings: Settings,
    *,
    upload: Any | None = None,
    url: str | None = None,
    options: TranscriptionOptions | None = None,
    registry: JobRegistry | None = None,
    progress: Any | None = None,
    pipeline_factory: Any | None = None,
) -> TranscriptionJobOutcome:
    """Validate the input, guard against duplicates and run the pipeline.

    Kept free of Streamlit calls so it can be tested directly.
    """
    if upload is None and not (url or "").strip():
        return TranscriptionJobOutcome(
            "invalid_input",
            "Choose a video or audio file, or enter a URL, before starting.",
        )

    try:
        if upload is not None:
            size = int(getattr(upload, "size", 0) or 0)
            stored = source_service.save_upload(
                upload, getattr(upload, "name", "") or "upload", settings, expected_size=size
            )
            source: ValidatedSource = stored.as_source()
        else:
            source = build_url_source(url, settings)
    except (SourceValidationError, UrlIngestionError) as error:
        return TranscriptionJobOutcome("invalid_input", str(error))

    guard = registry or JobRegistry()
    key = job_key_for(source)

    # A browser disconnect or killed script can leave a lock with no worker behind
    # it. Only a lock older than every configured job bound is treated as stale, so
    # a genuinely long transcription is never restarted underneath the user.
    stale_after = (
        float(settings.media_processing_timeout_seconds)
        + float(settings.url_download_timeout_seconds)
        + 60.0
    )
    stale = guard.reset_stale(stale_after)
    if stale:
        logger.warning(
            "Cleared %s stale transcription job lock(s) after an interrupted run.",
            len(stale),
        )

    if guard.is_running(key):
        elapsed = guard.running_for(key) or 0.0
        return TranscriptionJobOutcome(
            "running",
            "This source is already being processed "
            f"({elapsed:.0f} s so far). Please wait for it to finish. If the previous "
            "run was interrupted, use Reset stuck job lock below.",
        )

    finished = guard.completed_transcript(key)
    if finished:
        return TranscriptionJobOutcome(
            "duplicate",
            "This source was already transcribed in this session; showing the stored transcript.",
            transcript_id=finished,
        )

    guard.start(key)
    try:
        factory = pipeline_factory or VideoPipeline
        pipeline = factory(settings)
        result = pipeline.run(source, options=options, progress=progress)
    except Exception:  # noqa: BLE001 - the interface must never crash
        logger.exception("Unexpected failure while running the transcription pipeline")
        guard.finish(key)
        return TranscriptionJobOutcome("failed", GENERIC_FAILURE_MESSAGE)
    finally:
        _discard_upload(source, settings)

    if result.succeeded and result.transcript and result.transcript.transcript_id:
        guard.finish(key, result.transcript.transcript_id)
        return TranscriptionJobOutcome(
            "success",
            result.message,
            transcript_id=result.transcript.transcript_id,
            stages=result.stages,
            outcome=result.outcome,
        )

    guard.finish(key)
    return TranscriptionJobOutcome(
        "failed", result.message, stages=result.stages, outcome=result.outcome
    )


def _discard_upload(source: ValidatedSource, settings: Settings) -> None:
    """Delete the stored upload copy - the application keeps transcripts, not media."""
    if source.upload_path:
        source_service.cleanup_stored_file(source.upload_path, settings)


# --------------------------------------------------------------------------- rendering
def render_transcription_workflow(settings: Settings) -> None:
    """Render the complete transcription experience on the Home page."""
    ui.section_header(
        "Transcribe a video or audio file",
        "Upload local media or paste a supported URL. VideoMind AI extracts the audio, "
        "transcribes it with Whisper and stores a timestamped transcript.",
    )

    ready, readiness_message, readiness_status = _readiness(settings)
    ui.render_status_banner(readiness_status, readiness_message, "Media tools")
    if not ready:
        ui.render_bullets(
            [
                "Install FFmpeg on Windows: winget install Gyan.FFmpeg, then reopen the terminal.",
                "Install the Python media stack: pip install -r requirements.txt.",
                "Restart the application and refresh this page.",
            ]
        )
        return

    upload_tab, url_tab = st.tabs(["📁 Upload media", "🔗 Video URL"])
    with upload_tab:
        upload = _render_upload_input(settings)
    with url_tab:
        url_text = _render_url_input(settings)

    options = _render_options(settings)

    clicked = st.button(
        "Transcribe video",
        type="primary",
        key="start_transcription",
        help="Validates the source, extracts audio, transcribes it and stores the transcript.",
    )
    if clicked:
        _handle_start(settings, upload=upload, url_text=url_text, options=options)

    _render_notice()
    _render_stuck_job_reset()
    render_job_stages()
    render_active_transcript(settings)

    st.divider()
    render_saved_transcripts(settings)


def _render_upload_input(settings: Settings) -> Any | None:
    """Render the upload control with immediate local validation feedback."""
    upload = st.file_uploader(
        "Upload a video or audio file",
        type=[extension.lstrip(".") for extension in settings.allowed_media_extensions],
        key=UPLOAD_KEY,
        help=(
            f"Supported: {settings.supported_extension_description()}. "
            f"Maximum size: {settings.max_upload_size_mb} MB."
        ),
    )
    st.caption(
        f"Accepted formats: {settings.supported_extension_description()} "
        f"(max {settings.max_upload_size_mb} MB, "
        f"max {settings.max_video_duration_seconds // 60} minutes)."
    )

    if upload is None:
        return None

    size = int(getattr(upload, "size", 0) or 0)
    category = guess_source_type(upload.name).value.replace("_", " ")
    rows = [
        ("Filename", upload.name),
        ("Size", human_readable_size(size)),
        ("Detected category", category),
    ]
    try:
        safe_name, _ = source_service.validate_upload(upload.name, size, settings)
        rows.append(("Validation", f"Accepted as {safe_name}"))
        ui.render_key_values(rows)
    except SourceValidationError as error:
        rows.append(("Validation", "Rejected"))
        ui.render_key_values(rows)
        ui.render_status_banner("warning", str(error), "Unsupported file")
    return upload


def _render_url_input(settings: Settings) -> str:
    """Render the URL control with syntax feedback and acquisition-path preview."""
    url_text = st.text_input(
        "Video URL",
        key=URL_KEY,
        placeholder="https://www.youtube.com/watch?v=... or https://example.com/lecture.mp4",
    )
    st.caption(
        "Direct media links download over HTTP. Pages on supported public platforms are handled "
        "by yt-dlp. Private, paid or DRM protected material is not downloaded, and nothing is "
        "fetched until you press Transcribe video."
    )

    text = (url_text or "").strip()
    if not text:
        return text

    try:
        normalised = validate_url_syntax(text, settings)
    except UrlIngestionError as error:
        ui.render_status_banner("warning", str(error), "URL needs attention")
        return text

    ui.render_key_values(
        [("Normalised URL", normalised), ("Acquisition path", strategy_explanation(normalised, settings))]
    )
    return text


def _render_options(settings: Settings) -> TranscriptionOptions:
    """Render per-request transcription options (defaults come from configuration)."""
    with st.expander("Transcription settings"):
        model_index = (
            WHISPER_MODEL_CHOICES.index(settings.whisper_model)
            if settings.whisper_model in WHISPER_MODEL_CHOICES
            else 3
        )
        model = st.selectbox(
            "Whisper model",
            options=list(WHISPER_MODEL_CHOICES),
            index=model_index,
            key=MODEL_CHOICE_KEY,
            help=(
                "tiny/base are fast and good enough for clear speech; small and medium are more "
                "accurate but slower. large/turbo need considerably more memory."
            ),
        )
        choices = _language_choices()
        default_index = 0
        if settings.whisper_language and settings.whisper_language in choices:
            default_index = choices.index(settings.whisper_language)
        language = st.selectbox(
            "Language",
            options=choices,
            index=default_index,
            format_func=_language_label,
            key=LANGUAGE_CHOICE_KEY,
            help="Automatic detection is usually best; set a language for short or noisy clips.",
        )
        device_options = ["auto", "cpu", "cuda"]
        device_index = (
            device_options.index(settings.whisper_device)
            if settings.whisper_device in device_options
            else 0
        )
        device = st.selectbox(
            "Device",
            options=device_options,
            index=device_index,
            key=DEVICE_CHOICE_KEY,
            help="auto uses the GPU only when PyTorch reports CUDA. CPU always works.",
        )

        cuda_available = cuda_available_cached()
        model_url = f"https://openaipublic.azureedge.net/main/whisper/models/"
        st.caption(
            f"CUDA available: {'yes' if cuda_available else 'no (CPU only)'}. "
            f"The model '{model}' is downloaded once on first use from {model_url} "
            "and cached by Whisper."
        )
        st.caption(
            "Transcription runs on this machine. Longer recordings and larger models take longer; "
            "the interface reports real stages instead of estimated percentages."
        )

    return TranscriptionOptions(model_name=model, language=language, device=device)


def _handle_start(
    settings: Settings,
    *,
    upload: Any | None,
    url_text: str,
    options: TranscriptionOptions,
) -> None:
    """Run one job with live stage feedback and store its outcome."""
    label = "Processing media..."
    with st.status(label, expanded=True) as status:
        result = execute_transcription(
            settings,
            upload=upload,
            url=url_text if upload is None else None,
            options=options,
            registry=_registry(),
            progress=st.write,
        )
        if result.status == "success":
            status.update(label="Transcription complete", state="complete", expanded=True)
        elif result.status == "duplicate":
            status.update(label="Existing transcript reused", state="complete", expanded=True)
        elif result.status == "running":
            status.update(label="Job already running", state="error", expanded=True)
        elif result.status == "invalid_input":
            status.update(label="Nothing was processed", state="error", expanded=True)
        else:
            status.update(label="Transcription failed", state="error", expanded=True)

    st.session_state[STAGES_KEY] = [entry.model_dump(mode="json") for entry in result.stages]
    st.session_state[OUTCOME_KEY] = result.outcome.value if result.outcome else None

    if result.transcript_id and result.status in ("success", "duplicate"):
        st.session_state[TRANSCRIPT_ID_KEY] = result.transcript_id

    if result.status == "success":
        _set_notice("ok", "Transcription complete", result.message)
    elif result.status == "duplicate":
        _set_notice("pending", "Reused an existing transcript", result.message)
    elif result.status == "running":
        _set_notice("pending", "Job already running", result.message)
    elif result.status == "invalid_input":
        _set_notice("warning", "Input needs attention", result.message)
    else:
        _set_notice("error", "Transcription failed", result.message)


def _render_notice() -> None:
    notice = st.session_state.get(NOTICE_KEY)
    if not notice:
        return
    ui.render_status_banner(notice["level"], notice["message"], notice.get("title"))


def _render_stuck_job_reset() -> None:
    """Offer recovery when a lock outlived its worker (browser closed, run killed).

    Rendered independently of the button that created the lock, because on the next
    rerun that button's handler is not called at all.
    """
    registry = _registry()
    running = registry.running_keys()
    if not running:
        return
    durations = [registry.running_for(key) or 0.0 for key in running]
    st.caption(
        "A transcription job is marked as running "
        f"({max(durations):.0f} s). If the browser was closed or the run was killed, "
        "no worker exists any more and the lock can be cleared safely."
    )
    if st.button("Reset stuck job lock", key="reset_job_lock"):
        cleared = registry.reset_running()
        _set_notice(
            "warning",
            "Job lock cleared",
            f"Cleared {len(cleared)} stuck job lock(s). The source can be started again.",
        )
        st.rerun()


def render_job_stages() -> None:
    """Render the stage checklist of the last job (honest, per-stage statuses)."""
    raw_stages = st.session_state.get(STAGES_KEY) or []
    if not raw_stages:
        return
    try:
        stages = [PipelineStageReport.model_validate(entry) for entry in raw_stages]
    except Exception:  # noqa: BLE001 - stale session data must not break the page
        return
    outcome = st.session_state.get(OUTCOME_KEY)
    ui.section_header(
        "Processing stages",
        f"Last job outcome: {outcome}" if outcome else "Last job stages",
    )
    ui.render_pipeline_stages(stages)


def render_active_transcript(settings: Settings) -> None:
    """Render the transcript selected in this session."""
    transcript_id = st.session_state.get(TRANSCRIPT_ID_KEY)
    if not transcript_id:
        return
    try:
        record = TranscriptService(settings).load(transcript_id)
    except TranscriptStorageError as error:
        st.session_state[TRANSCRIPT_ID_KEY] = None
        ui.render_status_banner("warning", str(error), "Transcript unavailable")
        return
    render_transcript_detail(settings, record)


def render_transcript_detail(settings: Settings, record: TranscriptRecord) -> None:
    """Render metadata, downloads, timestamped segments and the full text."""
    ui.section_header(
        record.title or "Transcript",
        "Generated with Whisper on this machine.",
    )

    language = _language_label(record.language) if record.language else "not reported"
    ui.render_key_values(
        [
            ("Source", record.source_reference or "unknown"),
            ("Duration", format_duration(record.duration_seconds)),
            ("Language", language),
            ("Whisper model", record.transcription_model or "unknown"),
            ("Segments", str(record.segment_count)),
            ("Stored as", record.transcript_id or "not stored"),
            ("Created", record.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")),
        ]
    )

    service = TranscriptService(settings)
    column_txt, column_json, _ = st.columns([1, 1, 2])
    with column_txt:
        st.download_button(
            "Download TXT",
            data=service.to_txt(record),
            file_name=service.download_filename(record, "txt"),
            mime="text/plain",
            key=f"download_txt_{record.transcript_id}",
        )
    with column_json:
        st.download_button(
            "Download JSON",
            data=service.to_json(record),
            file_name=service.download_filename(record, "json"),
            mime="application/json",
            key=f"download_json_{record.transcript_id}",
        )

    segment_tab, text_tab = st.tabs(["⏱ Timestamped segments", "📄 Full transcript"])
    with segment_tab:
        if not record.segments:
            st.caption("No timestamped segments were produced for this recording.")
        else:
            for block_label, block in _segment_blocks(record):
                st.markdown(f"**{block_label}**")
                st.markdown(_segment_html(block), unsafe_allow_html=True)
    with text_tab:
        st.text_area(
            "Full transcript",
            value=record.full_text,
            height=260,
            key=f"full_text_{record.transcript_id}",
            help="Select and copy, or use the download buttons above.",
        )


def render_saved_transcripts(settings: Settings) -> None:
    """List previously stored transcripts and let the user open or delete one."""
    ui.section_header(
        "Stored transcripts",
        "Transcripts are saved as JSON in the application data directory and can be reopened "
        "at any time - the media itself is not kept.",
    )
    service = TranscriptService(settings)
    try:
        stored = service.list_transcripts()
    except TranscriptStorageError as error:
        ui.render_status_banner("error", str(error), "Transcript storage unavailable")
        return

    if not stored:
        st.caption("No transcripts stored yet. Transcribe a recording to create the first one.")
        return

    selected = st.selectbox(
        "Saved transcripts",
        options=[item.transcript_id for item in stored if item.transcript_id],
        format_func=lambda identifier: _saved_label(stored, identifier),
        key=SAVED_CHOICE_KEY,
    )

    column_open, column_delete, _ = st.columns([1, 1, 2])
    with column_open:
        if st.button("Open transcript", key="open_saved_transcript"):
            st.session_state[TRANSCRIPT_ID_KEY] = selected
            # Re-run so the viewer above (already rendered) shows the selection.
            st.rerun()
    with column_delete:
        if st.button("Delete transcript", key="delete_saved_transcript"):
            try:
                deleted = service.delete(selected)
            except TranscriptStorageError as error:
                _set_notice("error", "Could not delete transcript", str(error))
            else:
                if st.session_state.get(TRANSCRIPT_ID_KEY) == selected:
                    st.session_state[TRANSCRIPT_ID_KEY] = None
                _registry().forget_transcript(selected)
                if deleted:
                    _set_notice("ok", "Transcript deleted", "The stored transcript was removed.")
            st.rerun()


# --------------------------------------------------------------------------- helpers
def media_stack_ready(settings: Settings) -> bool:
    """True when FFmpeg and Whisper are both available for transcription."""
    return _readiness(settings)[0]


def _readiness(settings: Settings) -> tuple[bool, str, str]:
    """Report whether the local media stack can actually transcribe."""
    tooling = detect_media_tooling(settings)
    if not tooling.available:
        return False, FFMPEG_SETUP_MESSAGE, "warning"
    if not TranscriptionService.is_installed():
        return (
            False,
            "openai-whisper is not installed, so transcription is unavailable. Run "
            "'pip install -r requirements.txt' and restart the application.",
            "warning",
        )
    device = "cuda" if cuda_available_cached() else "cpu"
    return (
        True,
        f"FFmpeg and Whisper are ready (configured model: {settings.whisper_model}, device: {device}).",
        "ok",
    )


def _language_choices() -> list[str | None]:
    """Language options for the selectbox: automatic detection plus Whisper's codes."""
    names = whispered_language_names()
    if not names:
        return [None, "en", "es", "de", "fr", "hi", "ta", "te", "zh", "ja", "ar", "pt", "ru", "it"]
    return [None] + sorted(names, key=lambda code: names[code])


def _language_label(code: str | None) -> str:
    if not code:
        return "Auto-detect"
    names = whispered_language_names()
    return f"{names.get(code, code)} ({code})"


def _segment_blocks(record: TranscriptRecord) -> list[tuple[str, list[Any]]]:
    """Group segments into readable blocks for large transcripts."""
    blocks: list[tuple[str, list[Any]]] = []
    segments = record.segments
    for start in range(0, len(segments), SEGMENTS_PER_BLOCK):
        block = segments[start : start + SEGMENTS_PER_BLOCK]
        first = block[0]
        last = block[-1]
        name = first.text[:48] + ("..." if len(first.text) > 48 else "")
        blocks.append(
            (
                f"Segments {first.segment_index + 1}-{last.segment_index + 1} "
                f"({format_timestamp(first.start_seconds)}): {name}",
                list(block),
            )
        )
    return blocks


def _segment_html(segments: list[Any]) -> str:
    """Build the escaped HTML for a block of segments."""
    rows: list[str] = []
    for segment in segments:
        stamp = (
            format_timestamp(segment.start_seconds)
            if segment.has_timestamps
            else UNAVAILABLE_TIMESTAMP
        )
        rows.append(
            '<div class="vm-seg">'
            f'<span class="vm-seg__time">{_escape(stamp)}</span>'
            f'<span class="vm-seg__text">{_escape(segment.text)}</span>'
            "</div>"
        )
    return "".join(rows)


def _escape(value: Any) -> str:
    from html import escape

    return escape(str(value))


def _saved_label(stored: list[TranscriptMetadata], identifier: str) -> str:
    for item in stored:
        if item.transcript_id == identifier:
            created = item.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            title = item.title or item.original_filename or "Untitled"
            return f"{title} · {created} · {item.segment_count} segments"
    return identifier
