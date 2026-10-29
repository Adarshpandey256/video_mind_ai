"""Machine-readable JSON export of the canonical report model.

The document is the Pydantic model itself, serialised with ``allow_nan=False``
and ``ensure_ascii=False``: valid JSON, stable field names, Unicode preserved,
no runtime objects and no configuration or secrets.
"""

from __future__ import annotations

import json

from src.reports.export_types import RenderResult
from src.reports.report_models import VideoMindReport


def report_to_dict(report: VideoMindReport) -> dict:
    """Return the report as JSON-safe primitives (datetimes become ISO strings)."""
    return report.model_dump(mode="json")


def render_json(report: VideoMindReport) -> RenderResult:
    """Render the report as UTF-8 JSON."""
    text = json.dumps(
        report_to_dict(report),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
        sort_keys=False,
    )
    return RenderResult(data=(text + "\n").encode("utf-8"))


__all__ = ["render_json", "report_to_dict"]
