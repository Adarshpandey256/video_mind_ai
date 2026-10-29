"""Step 5: report assembly and multi-format export.

Public entry points::

    from src.reports import build_report, build_export, ReportOptions, ReportSection

``build_report`` is deterministic and uses the stored transcript, summary and
chat messages - it never calls the AI provider. ``build_export`` renders a built
report in one of the formats listed in :data:`EXPORT_FORMATS`.
"""

from __future__ import annotations

from src.reports.export_formats import (
    DEFAULT_FORMAT_KEY,
    EXPORT_FORMATS,
    build_export,
    format_unavailable_reason,
    get_format,
)
from src.reports.export_types import ExportFormat, RenderResult, ReportExport
from src.reports.filename_utils import report_filename, safe_stem
from src.reports.report_builder import (
    build_report,
    default_report_title,
    safe_source_url,
    section_availability,
)
from src.reports.report_models import (
    DEFAULT_REPORT_SECTIONS,
    REPORT_SCHEMA_VERSION,
    REPORT_SECTION_LABELS,
    REPORT_SECTION_ORDER,
    ReportBuildError,
    ReportOptions,
    ReportSection,
    VideoMindReport,
)

__all__ = [
    "DEFAULT_FORMAT_KEY",
    "DEFAULT_REPORT_SECTIONS",
    "EXPORT_FORMATS",
    "REPORT_SCHEMA_VERSION",
    "REPORT_SECTION_LABELS",
    "REPORT_SECTION_ORDER",
    "ExportFormat",
    "RenderResult",
    "ReportBuildError",
    "ReportExport",
    "ReportOptions",
    "ReportSection",
    "VideoMindReport",
    "build_export",
    "build_report",
    "default_report_title",
    "format_unavailable_reason",
    "get_format",
    "report_filename",
    "safe_source_url",
    "safe_stem",
    "section_availability",
]
