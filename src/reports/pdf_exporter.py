"""Paginated PDF reports built with ReportLab's Platypus layout engine.

Layout is done with paragraphs, tables and page templates rather than absolute
coordinates, so long text wraps, tables repeat correctly and headings do not get
stranded at the bottom of a page.

Font handling is explicit because PDF is the one format where a missing glyph is
invisible until the reader sees a blank box:

1. ``REPORT_PDF_FONT_PATH`` (a ``.ttf`` or ``.ttc``) is used when configured;
2. otherwise a system Unicode font is looked up (Nirmala UI / Segoe UI / Noto /
   DejaVu / Arial Unicode, depending on the platform);
3. if none can be registered, the built-in Helvetica font is used and the export
   returns a warning explaining how to configure a Unicode font.

ReportLab does not perform complex-script shaping: Devanagari text is stored and
extracted correctly with a Devanagari font, but conjuncts may not be rendered in
typographic order. DOCX and HTML are the better formats for Hindi reports; this
limitation is documented rather than hidden.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from src.reports.export_types import RenderResult
from src.reports.report_models import (
    REPORT_SECTION_LABELS,
    ReportSection,
    VideoMindReport,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = None  # resolved lazily to keep the import optional
MARGIN_CM = 2.0
BODY_FONT_SIZE = 10
ACCENT = "#1F4ED8"
MUTED = "#5B6472"
RULE = "#D7DCE5"

#: System fonts tried, in order, when no explicit font is configured.
_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("windows", r"C:\Windows\Fonts\Nirmala.ttc"),
    ("windows", r"C:\Windows\Fonts\NirmalaB.ttc"),
    ("windows", r"C:\Windows\Fonts\segoeui.ttf"),
    ("windows", r"C:\Windows\Fonts\arialuni.ttf"),
    ("linux", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
    ("linux", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    ("linux", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("darwin", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ("darwin", "/Library/Fonts/Arial Unicode.ttf"),
)


class PdfError(RuntimeError):
    """Raised when the PDF report cannot be produced."""


@dataclass(frozen=True)
class FontChoice:
    """The font family used for the body of the PDF."""

    regular: str
    bold: str
    unicode: bool
    source: str | None


def _reportlab():
    """Import ReportLab lazily with an actionable error message."""
    try:
        from reportlab.lib.pagesizes import A4  # noqa: PLC0415
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: PLC0415
        from reportlab.lib.units import cm  # noqa: PLC0415
        from reportlab.pdfbase import pdfmetrics  # noqa: PLC0415
        from reportlab.pdfbase.ttfonts import TTFont  # noqa: PLC0415
        from reportlab.platypus import (  # noqa: PLC0415
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise PdfError(
            "PDF export needs the reportlab package. Install it with "
            "`pip install reportlab` and restart the application."
        ) from error
    return {
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "getSampleStyleSheet": getSampleStyleSheet,
        "cm": cm,
        "pdfmetrics": pdfmetrics,
        "TTFont": TTFont,
        "PageBreak": PageBreak,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


def _register(path: Path, rl: dict, *, name: str) -> bool:
    """Register a TrueType/TrueType-collection font, returning success."""
    try:
        font = rl["TTFont"](name, str(path), subfontIndex=0)
        rl["pdfmetrics"].registerFont(font)
        return True
    except Exception as error:  # noqa: BLE001 - any font backend failure is non-fatal
        logger.warning("PDF font %s could not be registered: %s", path.name, type(error).__name__)
        return False


def resolve_font(configured_path: Path | str | None, *, rl: dict | None = None) -> FontChoice:
    """Pick the PDF body font, preferring an explicitly configured one."""
    rl = rl or _reportlab()
    if configured_path:
        path = Path(configured_path)
        if path.is_file() and _register(path, rl, name="VMReportBody"):
            return FontChoice("VMReportBody", "VMReportBody", True, str(path))
        logger.warning("Configured PDF font could not be used: %s", path)

    for _, candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file() and _register(path, rl, name="VMReportBody"):
            return FontChoice("VMReportBody", "VMReportBody", True, str(path))

    return FontChoice("Helvetica", "Helvetica-Bold", False, None)


def _styles(rl: dict, font: FontChoice, body_size: int) -> dict:
    base = rl["getSampleStyleSheet"]()["BodyText"]
    ParagraphStyle = rl["ParagraphStyle"]

    def make(name: str, **kwargs) -> object:
        defaults = {
            "fontName": font.regular,
            "fontSize": body_size,
            "leading": body_size * 1.45,
            "textColor": "#1C2024",
            "spaceAfter": 6,
        }
        defaults.update(kwargs)
        return ParagraphStyle(name, parent=base, **defaults)

    return {
        "title": make("VMTitle", fontSize=24, leading=28, textColor=ACCENT, spaceAfter=4),
        "eyebrow": make("VMEyebrow", fontSize=9, leading=13, textColor=MUTED, spaceAfter=10),
        "meta": make("VMMeta", fontSize=9.5, leading=14, textColor=MUTED, spaceAfter=2),
        "h1": make(
            "VMH1",
            fontSize=15,
            leading=19,
            textColor="#111827",
            spaceBefore=18,
            spaceAfter=8,
            keepWithNext=1,
        ),
        "h2": make(
            "VMH2",
            fontSize=11.5,
            leading=15,
            textColor="#111827",
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "body": make("VMBody", wordWrap="CJK"),
        "quote": make(
            "VMQuote",
            leftIndent=14,
            textColor=MUTED,
            fontSize=body_size - 0.5,
            leading=(body_size - 0.5) * 1.4,
        ),
        "bullet": make("VMBullet", leftIndent=12, bulletIndent=2, spaceAfter=3),
        "subbullet": make(
            "VMSubBullet", leftIndent=26, bulletIndent=16, spaceAfter=2, fontSize=body_size - 0.5
        ),
        "cell": make("VMCell", fontSize=body_size - 1, leading=(body_size - 1) * 1.35, spaceAfter=0),
        "cell_label": make(
            "VMCellLabel",
            fontSize=body_size - 1,
            leading=(body_size - 1) * 1.35,
            textColor=MUTED,
            spaceAfter=0,
        ),
        "note": make("VMNote", fontSize=body_size - 1.5, leading=(body_size - 1.5) * 1.4, textColor=MUTED),
    }


def _paragraphs(styles: dict, text: str, style: str = "body") -> list:
    rl = _reportlab()
    blocks = [block.strip() for block in str(text or "").split("\n\n") if block.strip()]
    return [rl["Paragraph"](escape(block), styles[style]) for block in blocks]


def _timestamped(styles: dict, stamp: str, text: str, *, style: str = "body") -> object:
    rl = _reportlab()
    return rl["Paragraph"](
        f'<font color="{ACCENT}">{escape(stamp)}</font> {escape(text)}', styles[style]
    )


class _PageDecorator:
    """Draw the repeating header and the page footer."""

    def __init__(self, report: VideoMindReport, styles: dict) -> None:
        self.report = report
        self.styles = styles

    def __call__(self, canvas, doc) -> None:  # noqa: ANN001 - ReportLab callback
        rl = _reportlab()
        canvas.saveState()
        width, height = rl["A4"]
        margin = MARGIN_CM * rl["cm"]
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        if doc.page > 1:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(MUTED)
            title = self.report.title[:110] + ("…" if len(self.report.title) > 110 else "")
            canvas.drawString(margin, height - margin + 14, self._latin(title))
            canvas.line(margin, height - margin + 8, width - margin, height - margin + 8)
        canvas.line(margin, margin - 4, width - margin, margin - 4)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        stamp = self.report.generated_at.strftime("%Y-%m-%d")
        canvas.drawString(
            margin,
            margin - 16,
            self._latin(f"{self.report.processing.app_name} report · {stamp}"),
        )
        canvas.drawRightString(width - margin, margin - 16, f"Page {doc.page}")
        canvas.restoreState()

    @staticmethod
    def _latin(text: str) -> str:
        """Header text is generated by the app, but keep it PDF-safe anyway."""
        return text.encode("latin-1", "replace").decode("latin-1")


def _metadata_table(rl: dict, styles: dict, report: VideoMindReport) -> object:
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
    data = [
        [rl["Paragraph"](escape(label), styles["cell_label"]), rl["Paragraph"](escape(value), styles["cell"])]
        for label, value in rows
    ]
    table = rl["Table"](data, colWidths=[4.2 * rl["cm"], 12.2 * rl["cm"]])
    table.setStyle(
        rl["TableStyle"](
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _section_story(rl: dict, styles: dict, report: VideoMindReport, section: ReportSection) -> list:
    story: list = []
    if section is ReportSection.METADATA:
        story.append(_metadata_table(rl, styles, report))

    elif section is ReportSection.SUMMARY and report.summary is not None:
        summary = report.summary
        details = [f"Style: {summary.style}"]
        if summary.generated_at:
            details.append(f"Generated: {summary.generated_at.isoformat()}")
        if summary.model:
            details.append(f"Model: {summary.model}")
        story.append(rl["Paragraph"](escape(" · ".join(details)), styles["meta"]))
        if summary.partial_note:
            story.append(rl["Paragraph"](escape(summary.partial_note), styles["note"]))
        if summary.short_text and summary.short_text != summary.text:
            story.append(rl["Paragraph"]("In brief", styles["h2"]))
            story.extend(_paragraphs(styles, summary.short_text))
        if summary.text:
            story.extend(_paragraphs(styles, summary.text))

    elif section is ReportSection.KEY_TAKEAWAYS:
        for item in report.key_takeaways:
            story.append(
                _timestamped(
                    styles,
                    f"({item.time_label})",
                    item.title,
                    style="bullet",
                )
            )
            if item.explanation:
                story.extend(_paragraphs(styles, item.explanation, style="quote"))
            if item.excerpt:
                story.append(rl["Paragraph"](escape(f'"{item.excerpt}"'), styles["quote"]))

    elif section is ReportSection.TOPICS:
        for item in report.topics:
            story.append(_timestamped(styles, f"({item.time_label})", item.title, style="bullet"))
            if item.explanation:
                story.extend(_paragraphs(styles, item.explanation, style="quote"))
            for point in item.key_points:
                story.append(rl["Paragraph"](escape(f"• {point}"), styles["subbullet"]))

    elif section is ReportSection.CHAPTERS:
        for item in report.chapters:
            story.append(_timestamped(styles, f"({item.time_label})", item.title, style="bullet"))
            if item.description:
                story.extend(_paragraphs(styles, item.description, style="quote"))
            for point in item.major_points:
                story.append(rl["Paragraph"](escape(f"• {point}"), styles["subbullet"]))

    elif section is ReportSection.ACTION_ITEMS:
        for item in report.action_items:
            marker = "Action" if item.kind == "action" else "Decision"
            details = ", ".join(part for part in (item.owner, item.deadline) if part)
            suffix = f" ({details})" if details else ""
            story.append(
                _timestamped(
                    styles,
                    f"[{item.time_label}]",
                    f"{marker}: {item.text}{suffix}",
                    style="bullet",
                )
            )
            if item.context:
                story.append(rl["Paragraph"](escape(item.context), styles["note"]))

    elif section is ReportSection.TRANSCRIPT:
        if not report.transcript:
            story.append(rl["Paragraph"]("No transcript was included.", styles["body"]))
        else:
            if not report.timestamps_available:
                story.append(
                    rl["Paragraph"](
                        "This transcript has no timestamps, so no time ranges are shown.",
                        styles["note"],
                    )
                )
            for entry in report.transcript:
                if report.shows_timestamps:
                    story.append(_timestamped(styles, f"[{entry.time_label}]", entry.text))
                else:
                    story.append(rl["Paragraph"](escape(entry.text), styles["body"]))

    elif section is ReportSection.CHAT_QA:
        if not report.chat:
            story.append(rl["Paragraph"]("No chatbot questions were included.", styles["body"]))
        for position, entry in enumerate(report.chat, start=1):
            story.append(rl["Paragraph"](escape(f"Question {position}"), styles["h2"]))
            story.append(rl["Paragraph"](escape(entry.question), styles["quote"]))
            story.extend(_paragraphs(styles, entry.answer))
            if entry.citations:
                story.append(rl["Paragraph"]("Sources", styles["meta"]))
                for citation in entry.citations:
                    name = citation.source_name or "Untitled recording"
                    story.append(
                        rl["Paragraph"](
                            f'<font color="{ACCENT}">[{escape(citation.source_ref)}]</font> '
                            f"{escape(name)} · {escape(citation.kind_label)} · "
                            f"{escape(citation.time_label)}",
                            styles["bullet"],
                        )
                    )
                    if citation.quote:
                        story.append(rl["Paragraph"](escape(f'"{citation.quote}"'), styles["quote"]))
            else:
                story.append(
                    rl["Paragraph"](
                        "No resolved source citations were recorded for this answer.",
                        styles["note"],
                    )
                )
            for note in entry.notes:
                story.append(rl["Paragraph"](escape(f"Note: {note}"), styles["note"]))

    elif section is ReportSection.SOURCES:
        if not report.sources:
            story.append(rl["Paragraph"]("No chatbot source passages are available.", styles["body"]))
        for source in report.sources:
            story.append(
                rl["Paragraph"](
                    f'<font color="{ACCENT}">[{escape(source.source_ref)}]</font> '
                    f"{escape(source.label)} · {escape(source.kind_label)} · "
                    f"{escape(source.time_label)}",
                    styles["bullet"],
                )
            )
            story.append(rl["Paragraph"](escape(f'"{source.excerpt}"'), styles["quote"]))

    elif section is ReportSection.PROCESSING:
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
        data = [
            [
                rl["Paragraph"](escape(label), styles["cell_label"]),
                rl["Paragraph"](escape(value), styles["cell"]),
            ]
            for label, value in rows
        ]
        table = rl["Table"](data, colWidths=[4.2 * rl["cm"], 12.2 * rl["cm"]])
        table.setStyle(
            rl["TableStyle"](
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(table)
        if processing.notes:
            story.append(rl["Paragraph"]("Notes", styles["h2"]))
            for note in processing.notes:
                story.append(rl["Paragraph"](escape(f"• {note}"), styles["bullet"]))

    return story


def build_pdf(report: VideoMindReport, *, font_path: Path | str | None = None) -> RenderResult:
    """Render the report as a paginated PDF, returning any font warning."""
    rl = _reportlab()
    font = resolve_font(font_path, rl=rl)
    warnings: list[str] = []
    if not font.unicode:
        warnings.append(
            "No Unicode TrueType font was found for the PDF, so Helvetica was used. Hindi "
            "or other non-Latin text may not render in this file. Set REPORT_PDF_FONT_PATH "
            "to a .ttf/.ttc file that contains those characters."
        )

    body_size = int(BODY_FONT_SIZE)
    styles = _styles(rl, font, body_size)
    story: list = []

    story.append(rl["Paragraph"](escape(report.processing.app_name.upper()), styles["eyebrow"]))
    story.append(rl["Paragraph"](escape(report.title), styles["title"]))
    if report.author:
        story.append(rl["Paragraph"](escape(f"Prepared by: {report.author}"), styles["meta"]))
    if report.organisation:
        story.append(rl["Paragraph"](escape(f"Organisation: {report.organisation}"), styles["meta"]))
    story.append(
        rl["Paragraph"](
            escape(
                f"Generated: {report.generated_at.isoformat()} · "
                f"Report schema {report.schema_version}"
            ),
            styles["meta"],
        )
    )
    story.append(rl["Spacer"](1, 6))

    if report.notes:
        story.append(rl["Paragraph"]("Notes", styles["h1"]))
        story.extend(_paragraphs(styles, report.notes))

    position = 0
    for section in (item for item in ReportSection if item in report.sections):
        position += 1
        if section in {ReportSection.TRANSCRIPT, ReportSection.CHAT_QA} and position > 1:
            story.append(rl["PageBreak"]())
        story.append(
            rl["Paragraph"](escape(f"{position}. {REPORT_SECTION_LABELS[section]}"), styles["h1"])
        )
        story.extend(_section_story(rl, styles, report, section))

    if report.omitted_sections:
        story.append(rl["Paragraph"]("Sections not included", styles["h1"]))
        for entry in report.omitted_sections:
            label = REPORT_SECTION_LABELS[entry.section]
            story.append(
                rl["Paragraph"](
                    escape(f"• {label}: {entry.reason or 'no data available'}"),
                    styles["bullet"],
                )
            )

    story.append(rl["Spacer"](1, 10))
    story.append(
        rl["Paragraph"](
            escape(f"Generated by {report.processing.app_name} from the stored transcript."),
            styles["note"],
        )
    )

    margin = MARGIN_CM * rl["cm"]
    buffer = io.BytesIO()
    try:
        document = rl["SimpleDocTemplate"](
            buffer,
            pagesize=rl["A4"],
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin + 0.6 * rl["cm"],
            bottomMargin=margin,
            title=report.title,
            author=report.author or report.processing.app_name,
            subject=f"Video report for {report.video.title}",
            creator=report.processing.app_name,
        )
        decorator = _PageDecorator(report, styles)
        document.build(story, onFirstPage=decorator, onLaterPages=decorator)
    except PdfError:
        raise
    except Exception as error:  # noqa: BLE001 - translated into an export error
        logger.warning("PDF build failed: %s", type(error).__name__)
        raise PdfError(
            "The PDF report could not be written. Check logs/app.log for the technical "
            "details, or export DOCX/HTML instead."
        ) from error

    data = buffer.getvalue()
    if not data.startswith(b"%PDF-"):
        raise PdfError("The PDF generator returned an invalid document.")

    logger.info("PDF report built (%s bytes, font=%s).", len(data), font.source or "Helvetica")
    return RenderResult(data=data, warnings=warnings)


__all__ = ["FontChoice", "PdfError", "build_pdf", "resolve_font"]
