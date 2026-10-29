"""Build the canonical Step 5 report from the real Steps 2-4 data.

The builder is deterministic: it never calls the NVIDIA model, never re-runs
transcription or summarisation, and never rewrites text. It only selects,
validates and copies existing content into :class:`VideoMindReport`.

What it refuses to do:

* invent a timestamp for a transcript that has none;
* invent a citation, a question or an answer for the chat section;
* show a section that was requested but has no real data - it is listed in
  ``omitted_sections`` with a reason instead;
* silently truncate a report: a transcript above the configured size is refused
  with an explicit message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models.rag_schemas import DocumentType
from src.models.schemas import SourceType, TranscriptRecord, utc_now
from src.models.summary_schemas import SUMMARY_MODE_LABELS, StructuredSummary
from src.rag.transcript_adapter import (
    NormalizedTranscript,
    TranscriptNormalizationError,
    normalize_transcript,
)
from src.reports.report_models import (
    DEFAULT_REPORT_SECTIONS,
    REPORT_SECTION_ORDER,
    ReportActionItem,
    ReportBuildError,
    ReportChapter,
    ReportChatEntry,
    ReportCitation,
    ReportOptions,
    ReportProcessing,
    ReportSection,
    ReportSectionChoice,
    ReportSource,
    ReportSummary,
    ReportTakeaway,
    ReportTopic,
    ReportTranscriptEntry,
    ReportVideo,
    SourceKind,
    VideoMindReport,
)
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP

logger = logging.getLogger(__name__)

REPORT_TITLE_SUFFIX = " - Video Report"

#: Query parameters that commonly carry credentials or tracking data and must not
#: be copied into a report that may be shared.
_SENSITIVE_QUERY_KEYWORDS = (
    "token",
    "key",
    "sig",
    "signature",
    "auth",
    "session",
    "password",
    "secret",
    "expires",
    "access",
    "credential",
)

_SOURCE_KINDS: dict[str, SourceKind] = {
    DocumentType.TRANSCRIPT.value: "transcript",
    DocumentType.SUMMARY.value: "summary",
}


def default_report_title(transcript: TranscriptRecord | None) -> str:
    """Suggest a title from the recording without inventing metadata."""
    if transcript is None:
        return "Video report"
    name = (transcript.title or transcript.original_filename or "").strip()
    return f"{name}{REPORT_TITLE_SUFFIX}" if name else "Video report"


def safe_source_url(value: str | None) -> str | None:
    """Return a display-safe http(s) URL, or ``None`` when none can be shown.

    Credentials, tracking/signed query parameters and fragments are removed, so a
    report never leaks a signed download link. Non-web schemes (``file:``,
    ``javascript:``, ``data:``) are refused.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None

    host = parsed.hostname or ""
    if not host:
        return None
    netloc = host
    if parsed.port and not (parsed.scheme == "https" and parsed.port == 443):
        netloc = f"{host}:{parsed.port}"

    kept = [
        (name, item)
        for name, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(keyword in name.lower() for keyword in _SENSITIVE_QUERY_KEYWORDS)
    ]
    query = urlencode(kept)
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))


def _normalise_chat_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[ReportChatEntry], list[ReportSource], list[str]]:
    """Pair rendered chat messages into Q&A entries, citing only recorded data."""
    entries: list[ReportChatEntry] = []
    sources_by_key: dict[str, ReportSource] = {}
    notes: list[str] = []
    pending_question: str | None = None
    dropped_citations = 0

    iterable = messages if isinstance(messages, (list, tuple)) else ()
    for message in iterable:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role == "user":
            pending_question = content or None
            continue
        if role != "assistant" or not content or pending_question is None:
            # An answer without a recorded question is not a Q&A pair; skipping it
            # is honest, whereas inventing a question would not be.
            continue

        citations: list[ReportCitation] = []
        for raw in message.get("citations") or []:
            if not isinstance(raw, Mapping):
                continue
            reference = str(raw.get("source_ref") or "").strip()
            if not reference:
                dropped_citations += 1
                continue
            kind = _SOURCE_KINDS.get(str(raw.get("document_type") or ""), "unknown")
            citations.append(
                ReportCitation(
                    source_ref=reference,
                    source_name=_optional_text(raw.get("source_name")),
                    source_kind=kind,
                    time_label=_optional_text(raw.get("time_label")) or UNAVAILABLE_TIMESTAMP,
                    quote=_optional_text(raw.get("quote")),
                    chunk_id=_optional_text(raw.get("chunk_id")),
                )
            )

        notes_for_entry = [
            str(note).strip()
            for note in (message.get("notes") or [])
            if str(note).strip()
        ]

        for raw in message.get("sources") or []:
            if not isinstance(raw, Mapping):
                continue
            reference = str(raw.get("source_ref") or "").strip()
            excerpt = str(raw.get("excerpt") or "").strip()
            if not reference or not excerpt:
                continue
            kind = _SOURCE_KINDS.get(str(raw.get("document_type") or ""), "unknown")
            key = str(raw.get("chunk_id") or "") or f"{reference}|{raw.get('time_label') or ''}"
            sources_by_key.setdefault(
                key,
                ReportSource(
                    source_ref=reference,
                    label=str(raw.get("label") or reference),
                    source_kind=kind,
                    time_label=_optional_text(raw.get("time_label")) or UNAVAILABLE_TIMESTAMP,
                    excerpt=excerpt,
                    chunk_id=_optional_text(raw.get("chunk_id")),
                ),
            )

        entries.append(
            ReportChatEntry(
                question=pending_question,
                answer=content,
                citations=citations,
                notes=notes_for_entry,
                sources_available=bool(citations),
            )
        )
        pending_question = None

    if dropped_citations:
        notes.append(
            f"{dropped_citations} chatbot citation(s) had no source identifier and were "
            "left out of the report."
        )
    uncited = sum(1 for entry in entries if not entry.citations)
    if uncited:
        notes.append(
            f"{uncited} chatbot answer(s) have no resolved source citations, so no references "
            "are shown for them."
        )
    return entries, list(sources_by_key.values()), notes


def _optional_text(value: Any) -> str | None:
    """Return stripped text or ``None``; never stringifies arbitrary objects."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summary_model(summary: StructuredSummary | None) -> ReportSummary | None:
    """Adapt the stored Step 3 summary without changing its wording."""
    if summary is None:
        return None
    style = SUMMARY_MODE_LABELS.get(summary.summary_type.value, summary.summary_type.value)
    text = (summary.overall_summary or "").strip()
    short = (summary.short_summary or "").strip() or None
    if not text and not short:
        return None
    partial_note = None
    if summary.is_partial:
        coverage = summary.coverage.describe()
        partial_note = (
            "This summary does not cover the whole recording. " + coverage
            if coverage
            else "This summary does not cover the whole recording."
        )
    return ReportSummary(
        summary_id=summary.summary_id,
        style=style,
        heading=f"{style} summary",
        text=text,
        short_text=short,
        language=summary.language,
        model=summary.generation_model,
        generated_at=summary.generated_at,
        processing_status=summary.processing_status.value,
        partial_note=partial_note,
    )


def _takeaways(summary: StructuredSummary | None) -> list[ReportTakeaway]:
    if summary is None:
        return []
    return [
        ReportTakeaway(
            title=item.title,
            explanation=item.explanation,
            excerpt=item.excerpt,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
        )
        for item in summary.key_takeaways
        if (item.title or "").strip()
    ]


def _topics(summary: StructuredSummary | None) -> list[ReportTopic]:
    if summary is None:
        return []
    return [
        ReportTopic(
            title=item.title,
            explanation=item.explanation,
            key_points=[point for point in item.key_points if point.strip()],
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
        )
        for item in summary.main_topics
        if (item.title or "").strip()
    ]


def _chapters(summary: StructuredSummary | None) -> list[ReportChapter]:
    if summary is None:
        return []
    return [
        ReportChapter(
            title=item.title,
            description=item.description,
            major_points=[point for point in item.major_points if point.strip()],
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
        )
        for item in summary.chapters
        if (item.title or "").strip()
    ]


def _actions(summary: StructuredSummary | None) -> list[ReportActionItem]:
    if summary is None:
        return []
    items = [
        ReportActionItem(
            kind="action",
            text=item.task,
            owner=item.owner,
            deadline=item.deadline,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
        )
        for item in summary.action_items
        if (item.task or "").strip()
    ]
    items.extend(
        ReportActionItem(
            kind="decision",
            text=item.decision,
            context=item.context,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
        )
        for item in summary.decisions
        if (item.decision or "").strip()
    )
    return items


def _transcript_entries(normalized: NormalizedTranscript) -> list[ReportTranscriptEntry]:
    return [
        ReportTranscriptEntry(
            segment_id=f"seg-{segment.index}",
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            text=segment.text,
        )
        for segment in normalized.segments
    ]


def section_availability(
    transcript: TranscriptRecord | None,
    summary: StructuredSummary | None,
    chat_messages: Sequence[Mapping[str, Any]] = (),
) -> dict[ReportSection, str | None]:
    """Report which sections can be built, and why the others cannot.

    The interface uses this to disable unavailable options instead of offering a
    checkbox that would silently produce an empty section.
    """
    normalized: NormalizedTranscript | None = None
    if transcript is not None:
        try:
            normalized = normalize_transcript(transcript)
        except TranscriptNormalizationError:
            normalized = None

    entries, sources, _ = _normalise_chat_messages(chat_messages)
    has_summary_text = _summary_model(summary) is not None
    reasons: dict[ReportSection, str | None] = {
        ReportSection.METADATA: None,
        ReportSection.SUMMARY: (
            None if has_summary_text else "No stored summary exists for this recording yet."
        ),
        ReportSection.KEY_TAKEAWAYS: (
            None if summary is not None and summary.key_takeaways else "The summary has no key takeaways."
        ),
        ReportSection.TOPICS: (
            None if summary is not None and summary.main_topics else "The summary has no topic breakdown."
        ),
        ReportSection.CHAPTERS: (
            None if summary is not None and summary.chapters else "The summary has no chapters."
        ),
        ReportSection.ACTION_ITEMS: (
            None
            if summary is not None and (summary.action_items or summary.decisions)
            else "The summary has no action items or decisions."
        ),
        ReportSection.TRANSCRIPT: (
            None if normalized is not None and normalized.segments else "This recording has no transcript text."
        ),
        ReportSection.CHAT_QA: (
            None if entries else "No chatbot questions have been asked about this video in this session."
        ),
        ReportSection.SOURCES: (
            None
            if any(entry.citations for entry in entries) or sources
            else "No resolved chatbot citations are available for this video."
        ),
        ReportSection.PROCESSING: None,
    }
    return reasons


def build_report(
    transcript: TranscriptRecord | None,
    summary: StructuredSummary | None,
    chat_messages: Sequence[Mapping[str, Any]],
    options: ReportOptions,
    *,
    app_name: str = "VideoMind AI",
    app_version: str | None = None,
    max_characters: int | None = None,
    max_note_chars: int | None = None,
    generated_at: datetime | None = None,
) -> VideoMindReport:
    """Assemble a validated report from the given real data.

    Raises :class:`ReportBuildError` with an actionable message when the
    transcript is missing, the selected content exceeds the size guard, or the
    free-text fields are too long. Missing *optional* sections do not raise: they
    are recorded in ``omitted_sections``.
    """
    if transcript is None:
        raise ReportBuildError(
            "Select a recording with a stored transcript before generating a report."
        )
    try:
        normalized = normalize_transcript(transcript)
    except TranscriptNormalizationError as error:
        raise ReportBuildError(str(error)) from error

    for label, value in (
        ("author", options.author),
        ("organisation", options.organisation),
        ("notes", options.notes),
    ):
        limit = max_note_chars if max_note_chars is not None else 4000
        if value and len(value) > limit:
            raise ReportBuildError(
                f"The {label} field is {len(value)} characters long; the limit is {limit}. "
                "Shorten it and try again."
            )

    entries, sources, chat_notes = _normalise_chat_messages(chat_messages)
    summary_model = _summary_model(summary)
    takeaways = _takeaways(summary)
    topics = _topics(summary)
    chapters = _chapters(summary)
    actions = _actions(summary)
    transcript_entries = _transcript_entries(normalized)

    reasons = section_availability(transcript, summary, chat_messages)

    #: Which data each section needs, evaluated once so availability and content
    #: can never disagree.
    populated: dict[ReportSection, bool] = {
        ReportSection.METADATA: True,
        ReportSection.SUMMARY: summary_model is not None,
        ReportSection.KEY_TAKEAWAYS: bool(takeaways),
        ReportSection.TOPICS: bool(topics),
        ReportSection.CHAPTERS: bool(chapters),
        ReportSection.ACTION_ITEMS: bool(actions),
        ReportSection.TRANSCRIPT: bool(transcript_entries),
        ReportSection.CHAT_QA: bool(entries),
        ReportSection.SOURCES: bool(sources) or any(entry.citations for entry in entries),
        ReportSection.PROCESSING: True,
    }

    included: list[ReportSection] = []
    omitted: list[ReportSectionChoice] = []
    for section in REPORT_SECTION_ORDER:
        if section not in options.sections:
            continue
        if populated[section]:
            included.append(section)
        else:
            omitted.append(
                ReportSectionChoice(
                    section=section,
                    included=False,
                    reason=reasons.get(section) or "No data is available for this section.",
                )
            )

    raw_reference = (transcript.source_reference or "").strip()
    source_url = (
        safe_source_url(raw_reference) if transcript.source_type is SourceType.URL else None
    )

    processing_notes: list[str] = list(chat_notes)
    if not normalized.has_timeline:
        processing_notes.append(
            "The transcript has no timestamps, so no time ranges are shown."
            + (
                " It is stored as plain text without segments."
                if normalized.used_full_text
                else ""
            )
        )
    if normalized.skipped_segments:
        processing_notes.append(
            f"{normalized.skipped_segments} empty transcript segment(s) contained no text "
            "and were omitted."
        )
    if summary_model is not None and summary_model.partial_note:
        processing_notes.append(summary_model.partial_note)
    if transcript.source_type is SourceType.URL and raw_reference and source_url is None:
        processing_notes.append(
            "The stored source URL is not a display-safe http(s) link, so it was not "
            "included in this report."
        )
    if transcript.source_type is SourceType.URL and source_url and source_url != raw_reference:
        processing_notes.append(
            "The source URL was shortened to remove sensitive query parameters before "
            "being written to the report."
        )

    report = VideoMindReport(
        title=options.title,
        author=options.author,
        organisation=options.organisation,
        notes=options.notes,
        generated_at=generated_at or utc_now(),
        video=ReportVideo(
            video_id=normalized.video_id,
            transcript_id=normalized.transcript_id,
            title=normalized.title,
            original_filename=_optional_text(transcript.original_filename),
            source_type=normalized.source_type,
            source_url=source_url,
            duration_seconds=normalized.duration_seconds,
            language=_optional_text(transcript.language),
            transcription_model=_optional_text(transcript.transcription_model),
            processing_status=(
                transcript.processing_status.value if transcript.processing_status else None
            ),
            created_at=transcript.created_at,
            segment_count=len(transcript_entries),
            has_timestamps=normalized.has_timeline,
        ),
        sections=included,
        omitted_sections=omitted,
        summary=summary_model if ReportSection.SUMMARY in included else None,
        key_takeaways=takeaways if ReportSection.KEY_TAKEAWAYS in included else [],
        topics=topics if ReportSection.TOPICS in included else [],
        chapters=chapters if ReportSection.CHAPTERS in included else [],
        action_items=actions if ReportSection.ACTION_ITEMS in included else [],
        transcript=transcript_entries if ReportSection.TRANSCRIPT in included else [],
        transcript_available=bool(transcript_entries),
        timestamps_available=normalized.has_timeline,
        include_timestamps=options.include_timestamps,
        chat=entries if ReportSection.CHAT_QA in included else [],
        sources=sources if ReportSection.SOURCES in included else [],
        processing=ReportProcessing(
            app_name=app_name,
            app_version=app_version,
            transcript_model=_optional_text(transcript.transcription_model),
            transcript_language=_optional_text(transcript.language),
            transcript_segments=len(transcript_entries),
            summary_model=summary_model.model if summary_model else None,
            summary_style=summary_model.style if summary_model else None,
            chat_answers=len(entries),
            timestamps_available=normalized.has_timeline,
            notes=processing_notes,
        ),
    )

    if max_characters is not None:
        total = (
            len(options.title)
            + len(options.author or "")
            + len(options.organisation or "")
            + len(options.notes or "")
            + report.transcript_characters
            + len(summary_model.text if summary_model else "")
            + sum(len(entry.question) + len(entry.answer) for entry in report.chat)
        )
        if total > max_characters:
            raise ReportBuildError(
                f"This report would contain {total:,} characters, above the configured "
                f"limit of {max_characters:,}. Exclude the full transcript, or raise "
                "REPORT_MAX_CHARACTERS if you really need one file that large."
            )

    logger.info(
        "Built report %s for %s (sections=%s, omitted=%s).",
        report.report_id,
        report.video.video_id,
        ",".join(section.value for section in report.sections),
        ",".join(entry.section.value for entry in report.omitted_sections) or "none",
    )
    return report


__all__ = [
    "DEFAULT_REPORT_SECTIONS",
    "REPORT_TITLE_SUFFIX",
    "build_report",
    "default_report_title",
    "safe_source_url",
    "section_availability",
]
