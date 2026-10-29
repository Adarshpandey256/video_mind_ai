"""CSV exports for the tabular parts of a report.

Three view types are offered because a transcript, a Q&A log and a citation list
have different columns; one merged sheet would be mostly empty cells.

Formula-injection protection (documented behaviour): a cell whose text starts
with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is prefixed with a
single quote when written to CSV, so a spreadsheet shows it as text instead of
evaluating it. The report model itself is never modified - the protection only
exists in the rendered CSV, exactly as spreadsheet applications expect.
"""

from __future__ import annotations

import csv
import io

from src.reports.export_types import RenderResult
from src.reports.report_models import VideoMindReport
from src.utils.time_utils import format_timestamp

_CSV_SAFE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def protect_csv_cell(value: object) -> str:
    """Return a spreadsheet-safe cell value (see the module docstring)."""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_SAFE_PREFIXES):
        return "'" + text
    return text


def _write(headers: list[str], rows: list[list[object]]) -> RenderResult:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([protect_csv_cell(header) for header in headers])
    for row in rows:
        writer.writerow([protect_csv_cell(value) for value in row])
    return RenderResult(data=buffer.getvalue().encode("utf-8"))


def _timestamp(value: float | None) -> str:
    return "" if value is None else format_timestamp(value)


def render_csv_transcript(report: VideoMindReport) -> RenderResult:
    """One row per transcript segment, with numeric and formatted timestamps."""
    headers = [
        "video_id",
        "video_title",
        "segment_id",
        "start_seconds",
        "end_seconds",
        "start_timestamp",
        "end_timestamp",
        "text",
    ]
    rows: list[list[object]] = []
    for entry in report.transcript:
        rows.append(
            [
                report.video.video_id,
                report.video.title,
                entry.segment_id,
                "" if entry.start_seconds is None else round(entry.start_seconds, 3),
                "" if entry.end_seconds is None else round(entry.end_seconds, 3),
                _timestamp(entry.start_seconds),
                _timestamp(entry.end_seconds),
                entry.text,
            ]
        )
    return _write(headers, rows)


def render_csv_chat(report: VideoMindReport) -> RenderResult:
    """One row per included question/answer pair."""
    headers = [
        "question",
        "answer",
        "citation_count",
        "source_ids",
        "source_timestamps",
        "citation_quotes",
    ]
    rows: list[list[object]] = []
    for entry in report.chat:
        rows.append(
            [
                entry.question,
                entry.answer,
                len(entry.citations),
                "; ".join(citation.source_ref for citation in entry.citations),
                "; ".join(citation.time_label for citation in entry.citations),
                " | ".join(citation.quote or "" for citation in entry.citations),
            ]
        )
    return _write(headers, rows)


def render_csv_sources(report: VideoMindReport) -> RenderResult:
    """One row per retrieved source passage."""
    headers = [
        "citation_id",
        "source_title",
        "source_type",
        "time_label",
        "excerpt",
        "video_id",
        "chunk_id",
    ]
    rows: list[list[object]] = [
        [
            source.source_ref,
            source.label,
            source.source_kind,
            source.time_label,
            source.excerpt,
            report.video.video_id,
            source.chunk_id or "",
        ]
        for source in report.sources
    ]
    return _write(headers, rows)


__all__ = [
    "protect_csv_cell",
    "render_csv_chat",
    "render_csv_sources",
    "render_csv_transcript",
]
