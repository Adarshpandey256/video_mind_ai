"""Markdown reports that stay valid even when the content contains markdown.

Two different problems are solved here:

* prose (a summary, a chatbot answer) is escaped so user or model text cannot
  accidentally create headings, tables or lists;
* the transcript is placed in a fenced code block, which preserves its exact
  wording, punctuation and Unicode without interpreting any of it as markup.
"""

from __future__ import annotations

import re

from src.reports.export_types import RenderResult
from src.reports.report_models import (
    REPORT_SECTION_LABELS,
    ReportChatEntry,
    ReportSection,
    VideoMindReport,
)

_INLINE_SPECIALS = set("\\`*_{}[]<>|$")
_NUMBERED_LIST = re.compile(r"^\d+[.)]\s")


def _escape_inline(text: str) -> str:
    """Escape characters that change inline Markdown formatting."""
    return "".join(f"\\{char}" if char in _INLINE_SPECIALS else char for char in str(text or ""))


def escape_block(text: str) -> str:
    """Escape one block of prose so it cannot become headings or lists."""
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        escaped = _escape_inline(line)
        if line[0] in "#>-+" or _NUMBERED_LIST.match(line):
            escaped = "\\" + escaped
        lines.append(escaped)
    return "\n".join(lines).strip()


def _fence(content: str) -> tuple[str, str]:
    """Pick a code fence long enough to contain ``content`` safely."""
    longest = max((len(match) for match in re.findall(r"`+", content)), default=0)
    marker = "`" * max(3, longest + 1)
    return marker, marker


def _table_row(*values: str) -> str:
    cells = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
    return "| " + " | ".join(cells) + " |"


def _citation_bullet(citation) -> str:  # noqa: ANN001 - ReportCitation
    label = citation.source_name or "Untitled recording"
    line = (
        f"- **[{citation.source_ref}] {_escape_inline(label)}** · "
        f"{citation.kind_label} · {_escape_inline(citation.time_label)}"
    )
    if citation.quote:
        line += f"\n  > {escape_block(citation.quote)}"
    return line


def _chat_block(entry: ReportChatEntry, position: int) -> str:
    lines = [f"### Question {position}", "", escape_block(entry.question), "", "**Answer**", "", escape_block(entry.answer)]
    if entry.citations:
        lines.extend(["", "**Sources**", ""])
        lines.extend(_citation_bullet(citation) for citation in entry.citations)
    else:
        lines.extend(["", "_No resolved source citations were recorded for this answer._"])
    if entry.notes:
        lines.append("")
        lines.extend(f"> Note: {escape_block(note)}" for note in entry.notes)
    return "\n".join(lines)


def _section_body(report: VideoMindReport, section: ReportSection) -> str:
    """Return the Markdown body of one included section."""
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
        table = [_table_row("Field", "Value"), _table_row("---", "---")]
        table.extend(_table_row(label, value) for label, value in rows)
        return "\n".join(table)

    if section is ReportSection.SUMMARY and report.summary is not None:
        summary = report.summary
        lines = [f"- **Style:** {_escape_inline(summary.style)}"]
        if summary.generated_at:
            lines.append(f"- **Generated:** {summary.generated_at.isoformat()}")
        if summary.model:
            lines.append(f"- **Model:** {_escape_inline(summary.model)}")
        if summary.partial_note:
            lines.append(f"- **Coverage:** {escape_block(summary.partial_note)}")
        body = []
        if summary.short_text and summary.short_text != summary.text:
            body.extend(["", "**In brief**", "", escape_block(summary.short_text)])
        if summary.text:
            body.extend(["", escape_block(summary.text)])
        return "\n".join(lines + body)

    if section is ReportSection.KEY_TAKEAWAYS:
        blocks = []
        for item in report.key_takeaways:
            block = [f"- **{escape_block(item.title)}** ({escape_block(item.time_label)})"]
            if item.explanation:
                block.append(f"  {escape_block(item.explanation)}")
            if item.excerpt:
                block.append(f"  > {escape_block(item.excerpt)}")
            blocks.append("\n".join(block))
        return "\n".join(blocks)

    if section is ReportSection.TOPICS:
        blocks = []
        for item in report.topics:
            block = [f"- **{escape_block(item.title)}** ({escape_block(item.time_label)})"]
            if item.explanation:
                block.append(f"  {escape_block(item.explanation)}")
            for point in item.key_points:
                block.append(f"  - {escape_block(point)}")
            blocks.append("\n".join(block))
        return "\n".join(blocks)

    if section is ReportSection.CHAPTERS:
        blocks = []
        for item in report.chapters:
            block = [f"- **{escape_block(item.title)}** ({escape_block(item.time_label)})"]
            if item.description:
                block.append(f"  {escape_block(item.description)}")
            for point in item.major_points:
                block.append(f"  - {escape_block(point)}")
            blocks.append("\n".join(block))
        return "\n".join(blocks)

    if section is ReportSection.ACTION_ITEMS:
        lines = []
        for item in report.action_items:
            marker = "Action" if item.kind == "action" else "Decision"
            details = [part for part in (item.owner, item.deadline) if part]
            suffix = f" ({_escape_inline(', '.join(details))})" if details else ""
            lines.append(
                f"- **{marker}:** {escape_block(item.text)}{suffix} "
                f"— {escape_block(item.time_label)}"
            )
            if item.context:
                lines.append(f"  {escape_block(item.context)}")
        return "\n".join(lines)

    if section is ReportSection.TRANSCRIPT:
        if not report.transcript:
            return "_No transcript was included._"
        if report.shows_timestamps:
            body = "\n".join(
                f"[{entry.time_label}] {entry.text}" for entry in report.transcript
            )
        elif not report.timestamps_available:
            body = "No timestamps are available for this transcript.\n\n" + "\n".join(
                entry.text for entry in report.transcript
            )
        else:
            body = "\n".join(entry.text for entry in report.transcript)
        opening, closing = _fence(body)
        return f"{opening}text\n{body}\n{closing}"

    if section is ReportSection.CHAT_QA:
        if not report.chat:
            return "_No chatbot questions were included._"
        return "\n\n".join(
            _chat_block(entry, position) for position, entry in enumerate(report.chat, start=1)
        )

    if section is ReportSection.SOURCES:
        if not report.sources:
            return "_No chatbot source passages are available for this report._"
        blocks = []
        for source in report.sources:
            block = [
                f"- **[{source.source_ref}] {escape_block(source.label)}** · "
                f"{source.kind_label} · {escape_block(source.time_label)}",
                f"  > {escape_block(source.excerpt)}",
            ]
            blocks.append("\n".join(block))
        return "\n".join(blocks)

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
                "available from the transcript"
                if processing.timestamps_available
                else "not available",
            ),
            ("Report schema", report.schema_version),
        ]
        table = [_table_row("Field", "Value"), _table_row("---", "---")]
        table.extend(_table_row(label, value) for label, value in rows)
        if processing.notes:
            table.extend(["", "**Notes**", ""])
            table.extend(f"- {escape_block(note)}" for note in processing.notes)
        return "\n".join(table)

    return ""


def render_markdown(report: VideoMindReport) -> RenderResult:
    """Render the report as a structured Markdown document."""
    lines: list[str] = [f"# {escape_block(report.title)}", ""]
    header = []
    if report.author:
        header.append(f"**Prepared by:** {escape_block(report.author)}")
    if report.organisation:
        header.append(f"**Organisation:** {escape_block(report.organisation)}")
    header.append(f"**Generated:** {report.generated_at.isoformat()}")
    header.append(f"**Report schema:** {report.schema_version}")
    lines.extend(header)

    if report.notes:
        lines.extend(["", "**Notes**", "", escape_block(report.notes)])

    position = 0
    for section in (item for item in ReportSection if item in report.sections):
        position += 1
        lines.extend(["", f"## {position}. {REPORT_SECTION_LABELS[section]}", ""])
        lines.append(_section_body(report, section))

    if report.omitted_sections:
        lines.extend(["", "## Sections not included", ""])
        for entry in report.omitted_sections:
            label = REPORT_SECTION_LABELS[entry.section]
            reason = entry.reason or "no data available"
            lines.append(f"- **{label}:** {escape_block(reason)}")

    lines.extend(["", "---", "", f"Generated by {report.processing.app_name}."])
    return RenderResult(data=("\n".join(lines) + "\n").encode("utf-8"))


__all__ = ["escape_block", "render_markdown"]
