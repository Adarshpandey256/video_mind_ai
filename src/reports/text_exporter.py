"""Plain-text reports: readable in any editor, with no markup artifacts."""

from __future__ import annotations

import textwrap

from src.reports.export_types import RenderResult
from src.reports.report_models import (
    REPORT_SECTION_LABELS,
    ReportChatEntry,
    ReportSource,
    ReportSection,
    VideoMindReport,
)

WIDTH = 100
DIVIDER = "=" * WIDTH
SUB_DIVIDER = "-" * WIDTH


def _wrap(text: str, *, width: int = WIDTH) -> str:
    """Wrap prose without changing words; blank lines are preserved."""
    blocks = [block.strip() for block in str(text or "").split("\n\n")]
    return "\n\n".join(
        textwrap.fill(" ".join(block.split()), width=width) for block in blocks if block
    )


def _bullet(text: str, *, indent: str = "  ") -> str:
    """Render one bullet, wrapping continuation lines under the text."""
    return textwrap.fill(
        " ".join(str(text).split()),
        width=WIDTH,
        initial_indent=f"{indent}- ",
        subsequent_indent=f"{indent}  ",
    )


def _citation_lines(citation) -> list[str]:  # noqa: ANN001 - ReportCitation
    label = citation.source_name or "Untitled recording"
    line = f"[{citation.source_ref}] {label} · {citation.kind_label} · {citation.time_label}"
    lines = [line]
    if citation.quote:
        lines.append(_wrap(f'"{citation.quote}"', width=WIDTH - 4))
    return lines


def _chat_block(entry: ReportChatEntry, position: int) -> list[str]:
    lines = [f"Q{position}. {entry.question}", ""]
    lines.extend(_wrap(f"A: {entry.answer}").split("\n"))
    if entry.citations:
        lines.extend(["", "   Sources:"])
        for citation in entry.citations:
            for line in _citation_lines(citation):
                lines.extend(f"   {part}" for part in line.split("\n"))
    else:
        lines.extend(["", "   No resolved source citations were recorded for this answer."])
    for note in entry.notes:
        lines.append(f"   Note: {note}")
    return lines


def _source_block(source: ReportSource) -> list[str]:
    header = f"[{source.source_ref}] {source.label} · {source.kind_label} · {source.time_label}"
    return [header, _wrap(f'   "{source.excerpt}"')]


def _section_body(report: VideoMindReport, section: ReportSection) -> list[str]:
    """Render one included section as text lines (no heading)."""
    if section is ReportSection.METADATA:
        video = report.video
        rows = [
            ("Title", video.title),
            ("Source type", video.source_type.value),
            ("Original file", video.original_filename or "not recorded"),
            ("Source URL", video.source_url or "not available"),
            ("Duration", video.duration_label),
            ("Language", video.language or "not reported"),
            ("Transcribed with", video.transcription_model or "not recorded"),
            ("Recorded", video.created_at.isoformat() if video.created_at else "not recorded"),
            ("Transcript segments", str(video.segment_count)),
        ]
        return [f"{label}: {value}" for label, value in rows]

    if section is ReportSection.SUMMARY and report.summary is not None:
        summary = report.summary
        lines = [f"Style: {summary.style}"]
        if summary.generated_at:
            lines.append(f"Generated: {summary.generated_at.isoformat()}")
        if summary.model:
            lines.append(f"Model: {summary.model}")
        if summary.partial_note:
            lines.append(f"Coverage: {summary.partial_note}")
        if summary.short_text and summary.short_text != summary.text:
            lines.extend(["", "In brief:", _wrap(summary.short_text)])
        if summary.text:
            lines.extend(["", _wrap(summary.text)])
        return lines

    if section is ReportSection.KEY_TAKEAWAYS:
        lines: list[str] = []
        for item in report.key_takeaways:
            header = item.title
            if item.time_label:
                header = f"{header} ({item.time_label})"
            lines.append(_bullet(header))
            if item.explanation:
                lines.append(_wrap(item.explanation, width=WIDTH - 4))
            if item.excerpt:
                lines.append(_wrap(f'Excerpt: "{item.excerpt}"', width=WIDTH - 4))
            lines.append("")
        return lines

    if section is ReportSection.TOPICS:
        lines = []
        for item in report.topics:
            lines.append(_bullet(f"{item.title} ({item.time_label})"))
            if item.explanation:
                lines.append(_wrap(item.explanation, width=WIDTH - 4))
            if item.key_points:
                lines.append("    Key points:")
                lines.extend(_bullet(point, indent="      ") for point in item.key_points)
            lines.append("")
        return lines

    if section is ReportSection.CHAPTERS:
        lines = []
        for item in report.chapters:
            lines.append(_bullet(f"{item.title} ({item.time_label})"))
            if item.description:
                lines.append(_wrap(item.description, width=WIDTH - 4))
            if item.major_points:
                lines.append("    Major points:")
                lines.extend(_bullet(point, indent="      ") for point in item.major_points)
            lines.append("")
        return lines

    if section is ReportSection.ACTION_ITEMS:
        lines = []
        for item in report.action_items:
            marker = "Action" if item.kind == "action" else "Decision"
            details = [part for part in (item.owner, item.deadline) if part]
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(_bullet(f"{marker}: {item.text}{suffix} [{item.time_label}]"))
            if item.context:
                lines.append(_wrap(item.context, width=WIDTH - 4))
        return lines

    if section is ReportSection.TRANSCRIPT:
        if not report.transcript:
            return ["No transcript was included."]
        lines = []
        if not report.timestamps_available:
            lines.extend(
                [
                    "This transcript has no timestamps, so no time ranges are shown.",
                    "",
                ]
            )
        for entry in report.transcript:
            prefix = f"[{entry.time_label}] " if report.shows_timestamps else ""
            lines.append(_wrap(f"{prefix}{entry.text}"))
            lines.append("")
        return lines

    if section is ReportSection.CHAT_QA:
        if not report.chat:
            return ["No chatbot questions were included."]
        lines = []
        for position, entry in enumerate(report.chat, start=1):
            lines.extend(_chat_block(entry, position))
            lines.append("")
        return lines

    if section is ReportSection.SOURCES:
        if not report.sources:
            return ["No chatbot source passages are available for this report."]
        lines = []
        for source in report.sources:
            lines.extend(_source_block(source))
            lines.append("")
        return lines

    if section is ReportSection.PROCESSING:
        processing = report.processing
        rows = [
            ("Generated by", processing.app_name),
            ("Application version", processing.app_version or "not recorded"),
            ("Generated at", processing.generated_at.isoformat()),
            ("Transcript segments", str(processing.transcript_segments)),
            ("Transcription model", processing.transcript_model or "not recorded"),
            ("Summary style", processing.summary_style or "not included"),
            ("Chat answers", str(processing.chat_answers)),
            (
                "Timestamps",
                "available from the transcript" if processing.timestamps_available else "not available",
            ),
            ("Report schema", report.schema_version),
        ]
        lines = [f"{label}: {value}" for label, value in rows]
        if processing.notes:
            lines.extend(["", "Notes:"])
            lines.extend(_bullet(note) for note in processing.notes)
        return lines

    return []


def render_text(report: VideoMindReport) -> RenderResult:
    """Render the report as a readable plain-text document."""
    lines: list[str] = [report.title, DIVIDER]

    header = []
    if report.author:
        header.append(f"Prepared by: {report.author}")
    if report.organisation:
        header.append(f"Organisation: {report.organisation}")
    header.append(f"Generated: {report.generated_at.isoformat()}")
    header.append(f"Report schema: {report.schema_version}")
    lines.extend(header)

    if report.notes:
        lines.extend(["", "Notes:", _wrap(report.notes)])

    position = 0
    for section in _included_sections(report):
        position += 1
        lines.extend(["", f"{position}. {REPORT_SECTION_LABELS[section]}", SUB_DIVIDER])
        lines.extend(_section_body(report, section))

    if report.omitted_sections:
        lines.extend(["", "Sections not included", SUB_DIVIDER])
        for entry in report.omitted_sections:
            label = REPORT_SECTION_LABELS[entry.section]
            lines.append(_bullet(f"{label}: {entry.reason or 'no data available'}"))

    lines.extend(["", DIVIDER, f"Generated by {report.processing.app_name}."])
    text = "\n".join(line.rstrip() for line in lines) + "\n"
    return RenderResult(data=text.encode("utf-8"))


def _included_sections(report: VideoMindReport) -> list[ReportSection]:
    """Included sections in canonical order."""
    return [section for section in ReportSection if section in report.sections]


__all__ = ["render_text"]
