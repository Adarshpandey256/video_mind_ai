"""Self-contained, escaped HTML report.

Every piece of user or model content passes through :func:`html.escape`, so a
transcript containing ``<script>`` is displayed as text. There is no JavaScript,
no remote font and no external stylesheet: the file opens correctly offline.
"""

from __future__ import annotations

import html
from urllib.parse import urlsplit

from src.reports.export_types import RenderResult
from src.reports.report_models import (
    REPORT_SECTION_LABELS,
    ReportSection,
    VideoMindReport,
)

STYLES = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 16px 64px;
  background: #f6f7fb;
  color: #1c2024;
  font-family: "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  line-height: 1.6;
}
main { max-width: 900px; margin: 0 auto; background: #fff; padding: 40px 44px 48px;
  border-radius: 14px; box-shadow: 0 6px 24px rgba(16, 24, 40, .08); }
header.report-header { border-bottom: 3px solid #1f4ed8; padding-bottom: 18px; margin-bottom: 28px; }
.eyebrow { margin: 0 0 6px; text-transform: uppercase; letter-spacing: .12em; font-size: 12px;
  color: #1f4ed8; font-weight: 600; }
h1 { margin: 0 0 10px; font-size: 30px; line-height: 1.25; }
h2 { margin: 34px 0 12px; font-size: 21px; border-bottom: 1px solid #e3e7ef; padding-bottom: 6px; }
h3 { margin: 22px 0 6px; font-size: 16px; }
h4 { margin: 14px 0 6px; font-size: 14px; text-transform: uppercase; letter-spacing: .06em;
  color: #475467; }
.meta { color: #475467; font-size: 14px; margin: 2px 0; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 4px; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e3e7ef; vertical-align: top; }
th { width: 32%; color: #475467; font-weight: 600; }
ul, ol { padding-left: 22px; }
li { margin: 4px 0; }
.time { font-variant-numeric: tabular-nums; color: #1f4ed8; font-weight: 600;
  margin-right: 8px; white-space: nowrap; }
blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid #c7d2fe;
  background: #f8faff; color: #334155; }
.transcript li { margin: 6px 0; }
.qa { border: 1px solid #e3e7ef; border-radius: 10px; padding: 14px 16px; margin: 14px 0; }
.qa p { margin: 6px 0; }
.answer { white-space: pre-wrap; }
.badge { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 999px;
  background: #eef2ff; color: #3538cd; margin-left: 6px; }
.note { color: #667085; font-size: 13px; }
.omitted { color: #667085; font-size: 14px; }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid #e3e7ef;
  color: #667085; font-size: 13px; }
a { color: #1f4ed8; }
@media print {
  body { background: #fff; padding: 0; font-size: 11pt; }
  main { max-width: none; box-shadow: none; border-radius: 0; padding: 0; }
  section { break-inside: auto; }
  h2, h3 { break-after: avoid; }
  .qa, blockquote, table { break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


def _esc(value: object) -> str:
    """Escape text for HTML element or attribute context."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _paragraphs(text: str, class_name: str = "") -> str:
    """Render blank-line separated text as escaped paragraphs."""
    blocks = [block.strip() for block in str(text or "").split("\n\n") if block.strip()]
    css = f' class="{class_name}"' if class_name else ""
    return "".join(f"<p{css}>{_esc(block)}</p>" for block in blocks)


def _metadata_section(report: VideoMindReport) -> str:
    video = report.video
    rows = [
        ("Title", video.title),
        ("Source type", video.source_type.value),
        ("Original file", video.original_filename or "not recorded"),
        ("Source URL", None),
        ("Duration", video.duration_label),
        ("Language", video.language or "not reported"),
        ("Transcribed with", video.transcription_model or "not recorded"),
        ("Recorded", video.created_at.isoformat() if video.created_at else "not recorded"),
        ("Transcript segments", str(video.segment_count)),
    ]
    body = []
    for label, value in rows:
        if label == "Source URL":
            href = _safe_href(video.source_url)
            if href is not None:
                value_html = (
                    f'<a href="{_esc(href)}" rel="noopener noreferrer" '
                    f'target="_blank">{_esc(href)}</a>'
                )
            elif video.source_url:
                value_html = _esc(video.source_url)
            else:
                value_html = "not available"
        else:
            value_html = _esc(value)
        body.append(f"<tr><th>{_esc(label)}</th><td>{value_html}</td></tr>")
    return "<table>" + "".join(body) + "</table>"


def _safe_href(value: str | None) -> str | None:
    """Only an absolute http(s) URL may become a link.

    The builder already strips credentials and refuses non-web schemes, but a
    renderer must not trust its input: ``javascript:``, ``data:`` and ``file:``
    render as plain text even in a hand-built report.
    """
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _summary_section(report: VideoMindReport) -> str:
    summary = report.summary
    if summary is None:
        return ""
    parts = [
        f"<p class=\"meta\">Style: {_esc(summary.style)}"
        + (f" · Generated: {_esc(summary.generated_at.isoformat())}" if summary.generated_at else "")
        + (f" · Model: {_esc(summary.model)}" if summary.model else "")
        + "</p>"
    ]
    if summary.partial_note:
        parts.append(f'<p class="note">{_esc(summary.partial_note)}</p>')
    if summary.short_text and summary.short_text != summary.text:
        parts.append("<h3>In brief</h3>")
        parts.append(_paragraphs(summary.short_text))
    if summary.text:
        parts.append(_paragraphs(summary.text))
    return "".join(parts)


def _takeaways_section(report: VideoMindReport) -> str:
    items = []
    for item in report.key_takeaways:
        body = [f"<h3>{_esc(item.title)} <span class=\"time\">{_esc(item.time_label)}</span></h3>"]
        if item.explanation:
            body.append(_paragraphs(item.explanation))
        if item.excerpt:
            body.append(f"<blockquote>{_esc(item.excerpt)}</blockquote>")
        items.append(f"<li>{''.join(body)}</li>")
    return f"<ul>{''.join(items)}</ul>"


def _topics_section(report: VideoMindReport, *, chapters: bool = False) -> str:
    items = []
    entries = report.chapters if chapters else report.topics
    for item in entries:
        body = [f"<h3>{_esc(item.title)} <span class=\"time\">{_esc(item.time_label)}</span></h3>"]
        description = item.description if chapters else item.explanation
        if description:
            body.append(_paragraphs(description))
        points = item.major_points if chapters else item.key_points
        if points:
            body.append("<ul>" + "".join(f"<li>{_esc(point)}</li>" for point in points) + "</ul>")
        items.append(f"<li>{''.join(body)}</li>")
    return f"<ul>{''.join(items)}</ul>"


def _actions_section(report: VideoMindReport) -> str:
    rows = []
    for item in report.action_items:
        marker = "Action" if item.kind == "action" else "Decision"
        details = ", ".join(part for part in (item.owner, item.deadline) if part)
        details_html = f" <span class=\"note\">({_esc(details)})</span>" if details else ""
        context = f"<div class=\"note\">{_esc(item.context)}</div>" if item.context else ""
        rows.append(
            f"<li><strong>{_esc(marker)}:</strong> {_esc(item.text)}{details_html}"
            f' <span class="time">{_esc(item.time_label)}</span>{context}</li>'
        )
    return f"<ul>{''.join(rows)}</ul>"


def _transcript_section(report: VideoMindReport) -> str:
    if not report.transcript:
        return "<p class=\"note\">No transcript was included.</p>"
    items = []
    for entry in report.transcript:
        prefix = (
            f'<span class="time">[{_esc(entry.time_label)}]</span> '
            if report.shows_timestamps
            else ""
        )
        items.append(f"<li>{prefix}{_esc(entry.text)}</li>")
    note = (
        '<p class="note">This transcript has no timestamps, so no time ranges are shown.</p>'
        if not report.timestamps_available
        else ""
    )
    return f"{note}<ol class=\"transcript\">{''.join(items)}</ol>"


def _chat_section(report: VideoMindReport) -> str:
    if not report.chat:
        return "<p class=\"note\">No chatbot questions were included.</p>"
    articles = []
    for position, entry in enumerate(report.chat, start=1):
        citations = ""
        if entry.citations:
            items = []
            for citation in entry.citations:
                name = citation.source_name or "Untitled recording"
                quote = (
                    f"<blockquote>{_esc(citation.quote)}</blockquote>"
                    if citation.quote
                    else ""
                )
                items.append(
                    f"<li><strong>[{_esc(citation.source_ref)}] {_esc(name)}</strong>"
                    f' <span class="badge">{_esc(citation.kind_label)}</span>'
                    f' <span class="time">{_esc(citation.time_label)}</span>{quote}</li>'
                )
            citations = "<h4>Sources</h4><ul>" + "".join(items) + "</ul>"
        else:
            citations = '<p class="note">No resolved source citations were recorded.</p>'
        notes = "".join(f'<p class="note">Note: {_esc(note)}</p>' for note in entry.notes)
        articles.append(
            f'<article class="qa"><h3>Question {position}</h3>'
            f"<p>{_esc(entry.question)}</p>"
            f'<p class="answer">{_esc(entry.answer)}</p>{citations}{notes}</article>'
        )
    return "".join(articles)


def _sources_section(report: VideoMindReport) -> str:
    if not report.sources:
        return "<p class=\"note\">No chatbot source passages are available for this report.</p>"
    items = []
    for source in report.sources:
        items.append(
            f"<li><strong>[{_esc(source.source_ref)}] {_esc(source.label)}</strong>"
            f' <span class="badge">{_esc(source.kind_label)}</span>'
            f' <span class="time">{_esc(source.time_label)}</span>'
            f"<blockquote>{_esc(source.excerpt)}</blockquote></li>"
        )
    return f"<ul>{''.join(items)}</ul>"


def _processing_section(report: VideoMindReport) -> str:
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
    table = "<table>" + "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>" for label, value in rows
    ) + "</table>"
    notes = ""
    if processing.notes:
        notes = "<h3>Notes</h3><ul>" + "".join(
            f"<li>{_esc(note)}</li>" for note in processing.notes
        ) + "</ul>"
    return table + notes


def _section_html(report: VideoMindReport, section: ReportSection, position: int) -> str:
    renderers = {
        ReportSection.METADATA: _metadata_section,
        ReportSection.SUMMARY: _summary_section,
        ReportSection.KEY_TAKEAWAYS: _takeaways_section,
        ReportSection.TOPICS: lambda item: _topics_section(item),
        ReportSection.CHAPTERS: lambda item: _topics_section(item, chapters=True),
        ReportSection.ACTION_ITEMS: _actions_section,
        ReportSection.TRANSCRIPT: _transcript_section,
        ReportSection.CHAT_QA: _chat_section,
        ReportSection.SOURCES: _sources_section,
        ReportSection.PROCESSING: _processing_section,
    }
    renderer = renderers.get(section)
    if renderer is None:
        return ""
    return (
        f'<section id="section-{section.value}">'
        f"<h2>{position}. {_esc(REPORT_SECTION_LABELS[section])}</h2>"
        f"{renderer(report)}</section>"
    )


def render_html(report: VideoMindReport) -> RenderResult:
    """Render the report as one self-contained, escaped HTML document."""
    header_bits = []
    if report.author:
        header_bits.append(f'<p class="meta">Prepared by: {_esc(report.author)}</p>')
    if report.organisation:
        header_bits.append(f'<p class="meta">Organisation: {_esc(report.organisation)}</p>')
    header_bits.append(
        f'<p class="meta">Generated: {_esc(report.generated_at.isoformat())} · '
        f"Report schema {_esc(report.schema_version)}</p>"
    )
    if report.notes:
        header_bits.append(f'<div class="notes">{_paragraphs(report.notes)}</div>')

    body_sections = []
    position = 0
    for section in (item for item in ReportSection if item in report.sections):
        position += 1
        body_sections.append(_section_html(report, section, position))

    omitted = ""
    if report.omitted_sections:
        items = "".join(
            f"<li><strong>{_esc(REPORT_SECTION_LABELS[entry.section])}:</strong> "
            f"{_esc(entry.reason or 'no data available')}</li>"
            for entry in report.omitted_sections
        )
        omitted = f"<section><h2>Sections not included</h2><ul class=\"omitted\">{items}</ul></section>"

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="{_esc(report.processing.app_name)}">
<title>{_esc(report.title)}</title>
<style>{STYLES}</style>
</head>
<body>
<main>
<header class="report-header">
<p class="eyebrow">{_esc(report.processing.app_name)} report</p>
<h1>{_esc(report.title)}</h1>
{''.join(header_bits)}
</header>
{''.join(body_sections)}
{omitted}
<footer>Generated by {_esc(report.processing.app_name)} · report {_esc(report.report_id)}</footer>
</main>
</body>
</html>
"""
    return RenderResult(data=document.encode("utf-8"))


__all__ = ["render_html"]
