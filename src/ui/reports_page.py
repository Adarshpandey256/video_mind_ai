"""Generate Your Video Report - the Step 5 export page.

The page is deliberately thin, like the transcription, summarisation and chat
pages: it loads the active video's real data, lets the user choose content and a
format, calls :mod:`src.reports` and offers the produced bytes for download. No
document layout lives here.

Guarantees the page keeps:

* reports are built from the selected video only, and the video id is part of the
  cache key, so switching videos can never reuse another report;
* generating a report never re-runs transcription, summarisation or an AI call;
* a selected section with no data is reported, not filled with placeholders;
* the download button only appears for bytes that were actually generated for the
  current selections.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import streamlit as st

from config.settings import Settings
from src.models.schemas import TranscriptMetadata, TranscriptRecord
from src.models.summary_schemas import StructuredSummary
from src.reports.export_formats import (
    DEFAULT_FORMAT_KEY,
    EXPORT_FORMATS,
    FORMAT_REQUIREMENTS,
    build_export,
    get_format,
)
from src.reports.report_builder import (
    build_report,
    default_report_title,
    section_availability,
)
from src.reports.report_models import (
    DEFAULT_REPORT_SECTIONS,
    REPORT_SECTION_LABELS,
    ReportBuildError,
    ReportOptions,
    ReportSection,
)
from src.services.summary_storage import SummaryStorage, SummaryStorageError
from src.services.transcript_service import TranscriptService
from src.services.transcript_storage import TranscriptStorageError
from src.ui import components as ui
from src.ui.chat_page import chat_turns, get_history, video_label
from src.utils.time_utils import format_duration

logger = logging.getLogger(__name__)

VIDEO_KEY = "report_video_choice"
NOTICE_KEY = "report_notice"
ARTIFACT_KEY = "report_artifact"
SECTION_KEY_PREFIX = "report_section_"
FORMAT_KEY = "report_format"


# --------------------------------------------------------------------------- state
def initialise_state() -> None:
    """Create the session keys this page owns (safe to call repeatedly)."""
    st.session_state.setdefault(NOTICE_KEY, None)
    st.session_state.setdefault(VIDEO_KEY, None)
    st.session_state.setdefault(ARTIFACT_KEY, None)
    st.session_state.setdefault(FORMAT_KEY, DEFAULT_FORMAT_KEY)
    if st.session_state[FORMAT_KEY] not in {fmt.key for fmt in EXPORT_FORMATS}:
        st.session_state[FORMAT_KEY] = DEFAULT_FORMAT_KEY


def _set_notice(level: str, title: str, message: str) -> None:
    st.session_state[NOTICE_KEY] = {"level": level, "title": title, "message": message}


def _render_notice() -> None:
    notice = st.session_state.get(NOTICE_KEY)
    if not notice:
        return
    st.session_state[NOTICE_KEY] = None
    ui.render_status_banner(
        {"success": "ok", "warning": "warning", "error": "error"}.get(notice.get("level"), "warning"),
        notice.get("message", ""),
        notice.get("title"),
    )


# --------------------------------------------------------------------------- data
def stored_videos(settings: Settings) -> list[TranscriptMetadata]:
    """List stored transcripts without letting a storage error break the page."""
    try:
        return TranscriptService(settings).list_transcripts()
    except Exception:  # noqa: BLE001 - listing must never crash the page
        logger.warning("Stored transcripts could not be listed for reports.")
        return []


def load_transcript(settings: Settings, transcript_id: str | None) -> TranscriptRecord | None:
    """Load one transcript, reporting a storage problem instead of raising."""
    if not transcript_id:
        return None
    try:
        return TranscriptService(settings).load(transcript_id)
    except (TranscriptStorageError, ValueError) as error:
        logger.warning("Transcript could not be loaded for a report: %s", type(error).__name__)
        _set_notice(
            "error",
            "Transcript unavailable",
            f"{error} Re-run transcription on the Home page if the file is gone.",
        )
        return None


def load_summary(settings: Settings, transcript: TranscriptRecord) -> StructuredSummary | None:
    """Return the newest stored summary for this transcript, if any."""
    if not transcript.transcript_id:
        return None
    try:
        return SummaryStorage(settings.summary_dir).latest_for_transcript(
            transcript.transcript_id
        )
    except SummaryStorageError:
        logger.warning("A stored summary could not be read for a report.")
        return None


def chat_messages(transcript: TranscriptRecord) -> list[dict[str, Any]]:
    """This video's chat messages only (never another video's conversation)."""
    return get_history(st.session_state, transcript.video_id)


def export_cache_key(
    *,
    transcript_id: str,
    video_id: str,
    summary_id: str | None,
    sections: list[ReportSection],
    title: str,
    author: str,
    organisation: str,
    notes: str,
    include_timestamps: bool,
    format_key: str,
) -> str:
    """Fingerprint everything that affects the generated bytes."""
    payload = json.dumps(
        {
            "transcript": transcript_id,
            "video": video_id,
            "summary": summary_id,
            "sections": [section.value for section in sections],
            "title": title,
            "author": author,
            "organisation": organisation,
            "notes": notes,
            "timestamps": include_timestamps,
            "format": format_key,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# --------------------------------------------------------------------------- page
def render_reports_page(settings: Settings) -> None:
    """Render the report generator: content, format, preview, download."""
    initialise_state()
    ui.section_header(
        "Generate Your Video Report",
        "Turn your video transcript, AI summary, key takeaways and selected chatbot "
        "answers into a downloadable report.",
    )
    _render_notice()

    videos = stored_videos(settings)
    if not videos:
        ui.render_status_banner(
            "warning",
            "No transcript is available yet. Upload a video or audio file on the Home page "
            "and run transcription first - reports are built from that stored transcript.",
            "Nothing to report on",
        )
        if st.button("Go to the transcription workflow", key="report_go_home"):
            from src.ui.layout import go_to_page

            go_to_page("home")
        return

    transcript = _render_video_selector(settings, videos)
    if transcript is None:
        return

    summary = load_summary(settings, transcript)
    messages = chat_messages(transcript)
    availability = section_availability(transcript, summary, messages)

    _render_overview(settings, transcript, summary, messages)

    selected_sections, title, author, organisation, notes, include_timestamps = (
        _render_content_controls(transcript, availability)
    )

    _render_preview(
        transcript,
        summary,
        messages,
        selected_sections,
        include_timestamps=include_timestamps,
    )

    _render_export_controls(
        settings,
        transcript=transcript,
        summary=summary,
        messages=messages,
        selected_sections=selected_sections,
        title=title,
        author=author,
        organisation=organisation,
        notes=notes,
        include_timestamps=include_timestamps,
    )


def _render_video_selector(
    settings: Settings, videos: list[TranscriptMetadata]
) -> TranscriptRecord | None:
    """Video picker, newest first, using the same labels as the chat page."""
    identifiers = [item.transcript_id for item in videos if item.transcript_id]
    if not identifiers:
        ui.render_status_banner(
            "error",
            "Stored transcripts exist but could not be read. Check that the data directory "
            "is writable.",
            "Transcripts unavailable",
        )
        return None

    if st.session_state.get(VIDEO_KEY) not in identifiers:
        st.session_state[VIDEO_KEY] = identifiers[0]

    labels = {item.transcript_id: video_label(item) for item in videos if item.transcript_id}
    chosen = st.selectbox(
        "Video",
        options=identifiers,
        format_func=lambda value: labels.get(value, value),
        key=VIDEO_KEY,
        help="The report always describes the recording selected here.",
    )
    return load_transcript(settings, chosen)


def _render_overview(
    settings: Settings,
    transcript: TranscriptRecord,
    summary: StructuredSummary | None,
    messages: list[dict[str, Any]],
) -> None:
    """What the report will be able to contain, from the real stored data."""
    answers = len(chat_turns(messages))
    ui.render_key_values(
        [
            ("Video", transcript.title or transcript.original_filename or "Untitled recording"),
            (
                "Source",
                transcript.source_reference or transcript.original_filename or "unknown",
            ),
            ("Duration", format_duration(transcript.duration_seconds)),
            (
                "Transcript",
                f"{transcript.segment_count} segment(s) · {transcript.language or 'language not reported'}",
            ),
            (
                "Summary",
                f"{summary.summary_type.value} · generated {summary.generated_at:%Y-%m-%d %H:%M}"
                if summary is not None
                else "not generated yet",
            ),
            ("Chat", f"{answers} answer(s) in this session" if answers else "no questions asked yet"),
            (
                "Formats",
                f"{len(EXPORT_FORMATS)} implemented: PDF, Word, TXT, Markdown, JSON, HTML, "
                "3 CSV views, Excel and PowerPoint",
            ),
        ]
    )


def _render_content_controls(
    transcript: TranscriptRecord,
    availability: dict[ReportSection, str | None],
) -> tuple[list[ReportSection], str, str, str, str, bool]:
    """Section selection plus the report title and optional metadata."""
    ui.section_header(
        "Choose content",
        "Only sections with real data can be selected. Unavailable sections explain why.",
    )

    selected: list[ReportSection] = []
    columns = st.columns(2)
    for index, section in enumerate(ReportSection):
        reason = availability.get(section)
        label = REPORT_SECTION_LABELS[section]
        default = reason is None and section in DEFAULT_REPORT_SECTIONS
        with columns[index % 2]:
            checked = st.checkbox(
                label,
                value=default,
                key=f"{SECTION_KEY_PREFIX}{transcript.video_id}_{section.value}",
                disabled=reason is not None,
                help=reason,
            )
            if reason:
                st.caption(f"{label}: {reason}")
        if checked and reason is None:
            selected.append(section)

    ui.section_header("Report settings", "The title and optional details appear on the cover.")
    title = st.text_input(
        "Report title",
        value=default_report_title(transcript),
        max_chars=200,
        key=f"report_title_{transcript.video_id}",
    )
    column_author, column_org = st.columns(2)
    with column_author:
        author = st.text_input(
            "Prepared by (optional)",
            max_chars=200,
            key=f"report_author_{transcript.video_id}",
        )
    with column_org:
        organisation = st.text_input(
            "Organisation or course (optional)",
            max_chars=200,
            key=f"report_org_{transcript.video_id}",
        )
    notes = st.text_area(
        "Notes (optional)",
        max_chars=2000,
        height=80,
        key=f"report_notes_{transcript.video_id}",
    )
    include_timestamps = st.checkbox(
        "Show transcript timestamps",
        value=True,
        key=f"report_timestamps_{transcript.video_id}",
        help="Timestamps are only shown when the transcript has real ones.",
    )
    return selected, title, author, organisation, notes, include_timestamps


def _render_preview(
    transcript: TranscriptRecord,
    summary: StructuredSummary | None,
    messages: list[dict[str, Any]],
    selected_sections: list[ReportSection],
    *,
    include_timestamps: bool = True,
) -> None:
    """A short preview of the selected content - never the whole transcript."""
    ui.section_header("Preview", "What the selected sections will contain.")

    if not selected_sections:
        st.info("Select at least one section to generate a report.")
        return

    if ReportSection.SUMMARY in selected_sections and summary is not None:
        excerpt = (summary.short_summary or summary.overall_summary or "").strip()
        if excerpt:
            st.caption("Summary excerpt")
            st.write(excerpt[:400] + ("…" if len(excerpt) > 400 else ""))

    takeaway_count = len(summary.key_takeaways) if summary else 0
    topic_count = len(summary.main_topics) if summary else 0
    answer_count = len(chat_turns(messages))
    transcript_chars = sum(len(segment.text) for segment in transcript.segments)

    metrics = st.columns(4)
    metrics[0].metric("Key takeaways", takeaway_count)
    metrics[1].metric("Topics", topic_count)
    metrics[2].metric("Chat answers", answer_count)
    metrics[3].metric("Transcript", f"{len(transcript.segments)} seg.")

    if ReportSection.TRANSCRIPT in selected_sections:
        st.caption(
            f"The full transcript is included: {len(transcript.segments)} segment(s), "
            f"about {transcript_chars:,} characters, without truncation."
        )
        with st.expander("Preview the first transcript segments", expanded=False):
            for segment in transcript.segments[:8]:
                stamp = (
                    f"[{_display_timestamp(segment.start_seconds)}] "
                    if segment.start_seconds is not None and include_timestamps
                    else ""
                )
                st.text(f"{stamp}{segment.text}")
            if len(transcript.segments) > 8:
                st.caption(f"Showing 8 of {len(transcript.segments)} segments.")

    if ReportSection.CHAT_QA in selected_sections:
        questions = [
            str(message.get("content"))
            for message in messages
            if message.get("role") == "user"
        ]
        with st.expander("Chatbot questions that will be included", expanded=False):
            if questions:
                for question in questions[:10]:
                    st.text(f"- {question}")
                if len(questions) > 10:
                    st.caption(f"Showing 10 of {len(questions)} questions.")
            else:
                st.caption("No questions were asked about this video in this session.")


def _available_formats(selected_sections: list[ReportSection]) -> list:
    """Formats that make sense for this selection, with reasons for the rest."""
    options = []
    for fmt in EXPORT_FORMATS:
        requirement = FORMAT_REQUIREMENTS.get(fmt.key)
        if requirement and requirement[0] not in selected_sections:
            continue
        options.append(fmt)
    return options


def _render_export_controls(
    settings: Settings,
    *,
    transcript: TranscriptRecord,
    summary: StructuredSummary | None,
    messages: list[dict[str, Any]],
    selected_sections: list[ReportSection],
    title: str,
    author: str,
    organisation: str,
    notes: str,
    include_timestamps: bool,
) -> None:
    """Format choice, generation and the download of the generated bytes."""
    ui.section_header(
        "Export",
        "Generating a report reads stored data only - it never re-transcribes, "
        "re-summarises or calls the AI provider.",
    )

    formats = _available_formats(selected_sections)
    if not formats:
        st.info("Select content that at least one format can represent.")
        return

    if st.session_state.get(FORMAT_KEY) not in {fmt.key for fmt in formats}:
        st.session_state[FORMAT_KEY] = formats[0].key

    format_key = st.selectbox(
        "Format",
        options=[fmt.key for fmt in formats],
        format_func=lambda key: get_format(key).label,
        key=FORMAT_KEY,
    )
    chosen_format = get_format(format_key)
    st.caption(chosen_format.description)
    if chosen_format.key == "pdf" and not settings.report_pdf_font_path:
        st.caption(
            "PDF uses a system Unicode font when one is available. Set "
            "REPORT_PDF_FONT_PATH to use a specific font."
        )

    cache_key = export_cache_key(
        transcript_id=transcript.transcript_id or "",
        video_id=transcript.video_id,
        summary_id=summary.summary_id if summary else None,
        sections=selected_sections,
        title=title,
        author=author,
        organisation=organisation,
        notes=notes,
        include_timestamps=include_timestamps,
        format_key=format_key,
    )

    stored = st.session_state.get(ARTIFACT_KEY)
    if isinstance(stored, dict) and stored.get("cache_key") != cache_key:
        # The selections changed, so previously generated bytes are stale.
        st.session_state[ARTIFACT_KEY] = None
        stored = None

    disabled = not selected_sections or not title.strip()
    if st.button(
        f"Generate {chosen_format.label}",
        type="primary",
        key=f"report_generate_{transcript.video_id}",
        disabled=disabled,
    ):
        stored = _generate(
            settings,
            transcript=transcript,
            summary=summary,
            messages=messages,
            selected_sections=selected_sections,
            title=title,
            author=author,
            organisation=organisation,
            notes=notes,
            include_timestamps=include_timestamps,
            format_key=format_key,
            cache_key=cache_key,
        )
        # Show the success or failure banner in this run, next to the button that
        # produced it, rather than one interaction later.
        _render_notice()

    if isinstance(stored, dict) and stored.get("cache_key") == cache_key:
        _render_download(stored)
    elif stored is None:
        st.caption(
            "Choose a format and press Generate. You can export the same content in another "
            "format without processing the video again."
        )


def _generate(
    settings: Settings,
    *,
    transcript: TranscriptRecord,
    summary: StructuredSummary | None,
    messages: list[dict[str, Any]],
    selected_sections: list[ReportSection],
    title: str,
    author: str,
    organisation: str,
    notes: str,
    include_timestamps: bool,
    format_key: str,
    cache_key: str,
) -> dict[str, Any] | None:
    """Build the report and its download, reporting every failure honestly."""
    try:
        options = ReportOptions(
            title=title,
            author=author or None,
            organisation=organisation or None,
            notes=notes or None,
            sections=selected_sections,
            include_timestamps=include_timestamps,
        )
    except ValueError as error:
        _set_notice("error", "Report not generated", str(error))
        return None

    try:
        report = build_report(
            transcript,
            summary,
            messages,
            options,
            app_version=settings.app_version,
            max_characters=settings.report_max_characters,
            max_note_chars=settings.report_max_note_chars,
        )
        with st.spinner(f"Rendering {get_format(format_key).label}..."):
            export = build_export(
                report,
                format_key,
                pdf_font_path=settings.report_pdf_font_path,
            )
    except ReportBuildError as error:
        _set_notice("error", "Report not generated", str(error))
        return None
    except (ValueError, RuntimeError) as error:
        logger.warning("Report export failed: %s", type(error).__name__)
        _set_notice("error", "Export failed", str(error))
        return None
    except Exception:  # noqa: BLE001 - the page must survive an unexpected failure
        logger.exception("Report export failed unexpectedly.")
        _set_notice(
            "error",
            "Export failed",
            "The report could not be generated. See logs/app.log for the technical details.",
        )
        return None

    artifact = {
        "cache_key": cache_key,
        "format_key": export.format_key,
        "format_label": export.format_label,
        "filename": export.filename,
        "mime": export.mime,
        "data": export.data,
        "warnings": list(export.warnings),
        "report_id": report.report_id,
        "sections": [section.value for section in report.sections],
        "omitted": [
            (entry.section.value, entry.reason or "") for entry in report.omitted_sections
        ],
        "size": export.size_bytes,
    }
    st.session_state[ARTIFACT_KEY] = artifact
    _set_notice(
        "success",
        "Report generated",
        f"{export.format_label} · {_human_size(export.size_bytes)} · ready to download.",
    )
    return artifact


def _render_download(artifact: dict[str, Any]) -> None:
    """Offer the generated bytes, with warnings the exporter reported."""
    if artifact.get("omitted"):
        missing = ", ".join(section for section, _ in artifact["omitted"])
        st.warning(
            "Some selected sections had no data and were left out: "
            f"{missing}. The report states why in its 'not included' section."
        )
    for warning in artifact.get("warnings") or []:
        st.warning(warning)

    st.download_button(
        f"Download {artifact['format_label']} ({_human_size(artifact['size'])})",
        data=artifact["data"],
        file_name=artifact["filename"],
        mime=artifact["mime"],
        key=f"report_download_{artifact['format_key']}",
        type="primary",
        on_click="ignore",
    )
    # The filename contains underscores; inline code keeps Markdown from turning
    # them into emphasis and mangling the displayed name.
    safe_name = str(artifact["filename"]).replace("`", "'")
    st.caption(
        f"Saved as `{safe_name}` · report {artifact['report_id']} · "
        f"{len(artifact['sections'])} section(s)."
    )


def _display_timestamp(seconds: float | None) -> str:
    """Local helper so a missing timestamp never becomes ``00:00``."""
    if seconds is None:
        return "--:--"
    from src.utils.time_utils import format_timestamp as _format

    return _format(seconds)


__all__ = [
    "ARTIFACT_KEY",
    "FORMAT_KEY",
    "NOTICE_KEY",
    "VIDEO_KEY",
    "chat_messages",
    "export_cache_key",
    "load_summary",
    "load_transcript",
    "render_reports_page",
    "stored_videos",
]
