"""The single list of export formats the application actually implements.

The interface reads :data:`EXPORT_FORMATS`, so a format can never appear in the
selector without a working renderer behind it. Tested formats only - the
placeholder-page era of advertising formats before they existed is over.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from pathlib import Path

from src.reports.csv_exporter import (
    render_csv_chat,
    render_csv_sources,
    render_csv_transcript,
)
from src.reports.docx_exporter import build_docx
from src.reports.export_types import ExportFormat, ReportExport
from src.reports.filename_utils import report_filename
from src.reports.html_exporter import render_html
from src.reports.json_exporter import render_json
from src.reports.markdown_exporter import render_markdown
from src.reports.pdf_exporter import build_pdf
from src.reports.pptx_exporter import build_pptx
from src.reports.report_models import ReportSection, VideoMindReport
from src.reports.text_exporter import render_text
from src.reports.xlsx_exporter import build_xlsx

logger = logging.getLogger(__name__)

EXPORT_FORMATS: tuple[ExportFormat, ...] = (
    ExportFormat(
        key="pdf",
        label="PDF",
        extension="pdf",
        mime="application/pdf",
        description="Paginated, print-ready report with page numbers.",
        filename_suffix="report",
        build=build_pdf,
    ),
    ExportFormat(
        key="docx",
        label="Word (.docx)",
        extension="docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        description="Editable Word document with real headings and tables.",
        filename_suffix="report",
        build=build_docx,
    ),
    ExportFormat(
        key="txt",
        label="Plain text (.txt)",
        extension="txt",
        mime="text/plain",
        description="Readable in any editor - good for quick sharing or copy/paste.",
        filename_suffix="report",
        build=render_text,
    ),
    ExportFormat(
        key="md",
        label="Markdown (.md)",
        extension="md",
        mime="text/markdown",
        description="Structured Markdown that renders well on GitHub or in a notes app.",
        filename_suffix="report",
        build=render_markdown,
    ),
    ExportFormat(
        key="json",
        label="JSON (.json)",
        extension="json",
        mime="application/json",
        description="Machine-readable data with a documented schema version.",
        filename_suffix="report",
        build=render_json,
    ),
    ExportFormat(
        key="html",
        label="HTML (.html)",
        extension="html",
        mime="text/html",
        description="Self-contained styled page that opens offline in a browser.",
        filename_suffix="report",
        build=render_html,
    ),
    ExportFormat(
        key="csv",
        label="CSV - transcript",
        extension="csv",
        mime="text/csv",
        description="One row per transcript segment, with numeric and formatted timestamps.",
        filename_suffix="transcript",
        build=render_csv_transcript,
    ),
    ExportFormat(
        key="csv_chat",
        label="CSV - chat Q&A",
        extension="csv",
        mime="text/csv",
        description="One row per chatbot question and answer, with citation references.",
        filename_suffix="chat",
        build=render_csv_chat,
    ),
    ExportFormat(
        key="csv_sources",
        label="CSV - sources",
        extension="csv",
        mime="text/csv",
        description="One row per retrieved source passage used by the chatbot.",
        filename_suffix="sources",
        build=render_csv_sources,
    ),
    ExportFormat(
        key="xlsx",
        label="Excel (.xlsx)",
        extension="xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        description="Workbook with one worksheet per included content type.",
        filename_suffix="report",
        build=build_xlsx,
    ),
    ExportFormat(
        key="pptx",
        label="PowerPoint (.pptx)",
        extension="pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        description="Concise presentation: title, summary, takeaways and selected Q&A.",
        filename_suffix="slides",
        build=build_pptx,
    ),
)

FORMATS_BY_KEY: dict[str, ExportFormat] = {item.key: item for item in EXPORT_FORMATS}

#: Formats that need a particular section to contain data.
FORMAT_REQUIREMENTS: dict[str, tuple[ReportSection, str]] = {
    "csv": (ReportSection.TRANSCRIPT, "Include the transcript to export it as CSV."),
    "csv_chat": (ReportSection.CHAT_QA, "Include the chatbot Q&A to export it as CSV."),
    "csv_sources": (ReportSection.SOURCES, "Include sources to export them as CSV."),
}

DEFAULT_FORMAT_KEY = "pdf"


def get_format(key: str) -> ExportFormat:
    """Return one format by key, raising a clear error for an unknown key."""
    try:
        return FORMATS_BY_KEY[key]
    except KeyError as error:
        raise ValueError(f"Unknown export format: {key!r}") from error


def format_unavailable_reason(fmt: ExportFormat, report: VideoMindReport) -> str | None:
    """Return why a format cannot render this selection, or ``None``."""
    requirement = FORMAT_REQUIREMENTS.get(fmt.key)
    if requirement is None:
        return None
    section, reason = requirement
    if section in report.sections:
        return None
    return reason


def build_export(
    report: VideoMindReport,
    format_key: str,
    *,
    pdf_font_path: Path | str | None = None,
    now: datetime | None = None,
) -> ReportExport:
    """Render ``report`` in one format and build its safe download filename."""
    fmt = get_format(format_key)
    unavailable = format_unavailable_reason(fmt, report)
    if unavailable:
        raise ValueError(unavailable)

    if fmt.key == "pdf":
        result = partial(build_pdf, font_path=pdf_font_path)(report)
    else:
        result = fmt.build(report)

    filename = report_filename(
        report.title,
        fmt.extension,
        suffix=fmt.filename_suffix,
        now=now,
    )
    logger.info(
        "Rendered report %s as %s (%s bytes).",
        report.report_id,
        fmt.key,
        result.size_bytes,
    )
    return ReportExport(
        format_key=fmt.key,
        format_label=fmt.label,
        filename=filename,
        mime=fmt.mime,
        data=result.data,
        warnings=tuple(result.warnings),
    )


__all__ = [
    "DEFAULT_FORMAT_KEY",
    "EXPORT_FORMATS",
    "FORMATS_BY_KEY",
    "build_export",
    "format_unavailable_reason",
    "get_format",
]
