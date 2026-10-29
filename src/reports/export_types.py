"""Small value types shared by the report exporters and their registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.reports.report_models import VideoMindReport


@dataclass
class RenderResult:
    """The bytes one exporter produced, plus anything the user must know."""

    data: bytes
    warnings: list[str] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        """Size of the generated file in bytes."""
        return len(self.data)


@dataclass(frozen=True)
class ExportFormat:
    """One selectable download format."""

    key: str
    label: str
    extension: str
    mime: str
    description: str
    filename_suffix: str
    build: Callable[[VideoMindReport], RenderResult]


@dataclass(frozen=True)
class ReportExport:
    """A rendered report, ready for ``st.download_button``."""

    format_key: str
    format_label: str
    filename: str
    mime: str
    data: bytes
    warnings: tuple[str, ...] = ()

    @property
    def size_bytes(self) -> int:
        """Size of the generated file in bytes."""
        return len(self.data)
