"""A concise PowerPoint deck summarising the report.

A presentation is not a document: the deck carries the title, a short summary,
key takeaways, topics and the most useful Q&A, and it explicitly says when the
full transcript and the remaining Q&A are available in the document exports
instead of trying to fit hours of text onto slides.

``python-pptx`` is imported lazily so the application still starts when the
optional presentation library is not installed.
"""

from __future__ import annotations

import io
from typing import Any

from src.reports.export_types import RenderResult
from src.reports.report_models import ReportSection, VideoMindReport

SLIDE_WIDTH = 13.333
SLIDE_HEIGHT = 7.5
MAX_TAKEAWAYS_PER_SLIDE = 5
MAX_TOPICS_PER_SLIDE = 3
MAX_QA_SLIDES = 8
MAX_SOURCES_PER_SLIDE = 7
BODY_CHARS_PER_SLIDE = 900


class PptxError(RuntimeError):
    """Raised when a presentation cannot be produced."""


def _pptx():
    try:
        from pptx import Presentation  # noqa: PLC0415 - optional dependency
        from pptx.dml.color import RGBColor  # noqa: PLC0415
        from pptx.util import Inches, Pt  # noqa: PLC0415
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise PptxError(
            "PowerPoint export needs the python-pptx package. Install it with "
            "`pip install python-pptx` and restart the application."
        ) from error
    return Presentation, RGBColor, Inches, Pt


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    """Split a list into chunks of at most ``size`` items."""
    return [items[index : index + size] for index in range(0, len(items), size)] or [[]]


def _split_text(text: str, budget: int = BODY_CHARS_PER_SLIDE) -> list[str]:
    """Split prose into slide-sized pieces on paragraph, sentence or word boundaries."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    pieces: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= budget:
            pieces.append(remaining)
            break
        window = remaining[:budget]
        cut = max(window.rfind(". "), window.rfind("; "), window.rfind(" "))
        if cut < budget // 2:
            cut = budget
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    return pieces


def _add_slide(prs: Any, layout_index: int, title: str | None = None) -> Any:
    layout = prs.slide_layouts[layout_index]
    slide = prs.slides.add_slide(layout)
    if title is not None and slide.shapes.title is not None:
        slide.shapes.title.text = title
    return slide


def _textbox(slide: Any, *, left: float, top: float, width: float, height: float) -> Any:
    _, _, Inches, _ = _pptx()
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _fill_paragraphs(
    frame: Any,
    paragraphs: list[tuple[str, int, bool]],
    *,
    first_clear: bool = True,
) -> None:
    """Write (text, font size, bold) paragraphs into a text frame."""
    _, RGBColor, _, Pt = _pptx()
    first = True
    for text, size, bold in paragraphs:
        if first and first_clear:
            paragraph = frame.paragraphs[0]
            first = False
        else:
            paragraph = frame.add_paragraph()
        paragraph.text = text
        paragraph.space_after = Pt(8)
        for run in paragraph.runs:
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = RGBColor(0x1C, 0x20, 0x24)


def _footer(slide: Any, report: VideoMindReport, number: int) -> None:
    _, RGBColor, _, Pt = _pptx()
    frame = _textbox(slide, left=0.6, top=SLIDE_HEIGHT - 0.55, width=12.1, height=0.35)
    frame.paragraphs[0].text = f"{report.processing.app_name} · {report.title} · {number}"
    for run in frame.paragraphs[0].runs:
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)


class _Deck:
    """Small wrapper that keeps slide numbering and footers consistent."""

    def __init__(self, report: VideoMindReport) -> None:
        Presentation, _, Inches, _ = _pptx()
        self.report = report
        self.prs = Presentation()
        self.prs.slide_width = Inches(SLIDE_WIDTH)
        self.prs.slide_height = Inches(SLIDE_HEIGHT)
        self.number = 0

    def add(self, layout_index: int, title: str | None = None) -> Any:
        slide = _add_slide(self.prs, layout_index, title)
        self.number += 1
        _footer(slide, self.report, self.number)
        return slide

    def bullets(self, title: str, items: list[str], *, note: str | None = None) -> None:
        """Add a bullet slide; callers split long lists across several slides."""
        slide = self.add(5, title)
        paragraphs = [(item, 18, False) for item in items]
        if note:
            paragraphs.append((note, 12, False))
        _fill_paragraphs(
            _textbox(slide, left=0.75, top=1.7, width=11.8, height=4.9), paragraphs
        )

    def section_title(self, title: str, body: str | None = None) -> None:
        slide = self.add(5, title)
        if body:
            _fill_paragraphs(
                _textbox(slide, left=0.75, top=1.8, width=11.8, height=4.6),
                [(body, 20, False)],
            )


def build_pptx(report: VideoMindReport) -> RenderResult:
    """Render a concise presentation deck for the report."""
    deck = _Deck(report)

    # --- title slide ---------------------------------------------------------
    slide = deck.add(0)
    if slide.shapes.title is not None:
        slide.shapes.title.text = report.title
    subtitle = slide.placeholders[1] if len(slide.placeholders) > 1 else None
    subtitle_lines = []
    if report.author:
        subtitle_lines.append(f"Prepared by {report.author}")
    if report.organisation:
        subtitle_lines.append(report.organisation)
    subtitle_lines.append(report.video.title)
    subtitle_lines.append(report.generated_at.strftime("%Y-%m-%d"))
    if subtitle is not None:
        subtitle.text = "\n".join(subtitle_lines)

    # --- video overview ------------------------------------------------------
    video = report.video
    overview_lines = [
        f"Source: {video.source_type.value}",
        f"Original file: {video.original_filename or 'not recorded'}",
        f"Duration: {video.duration_label}",
        f"Language: {video.language or 'not reported'}",
        f"Transcript segments: {video.segment_count}",
        "Timestamps: " + ("available" if report.timestamps_available else "not available"),
    ]
    deck.section_title("Recording overview", "\n".join(overview_lines))

    # --- summary -------------------------------------------------------------
    if report.includes(ReportSection.SUMMARY) and report.summary is not None:
        summary = report.summary
        pieces = _split_text(summary.text or summary.short_text or "")
        for index, piece in enumerate(pieces[:4], start=1):
            title = "Summary" if len(pieces) == 1 else f"Summary ({index} of {min(len(pieces), 4)})"
            slide = deck.add(5, title)
            _fill_paragraphs(
                _textbox(slide, left=0.75, top=1.7, width=11.8, height=4.9),
                [(piece, 20, False)],
            )

    # --- key takeaways -------------------------------------------------------
    if report.includes(ReportSection.KEY_TAKEAWAYS) and report.key_takeaways:
        chunks = _chunk(report.key_takeaways, MAX_TAKEAWAYS_PER_SLIDE)
        for index, chunk in enumerate(chunks, start=1):
            title = "Key takeaways" if len(chunks) == 1 else f"Key takeaways ({index}/{len(chunks)})"
            deck.bullets(
                title,
                [
                    f"{item.title} ({item.time_label})" + (f" — {item.explanation}" if item.explanation else "")
                    for item in chunk
                ],
            )

    # --- topics --------------------------------------------------------------
    if report.includes(ReportSection.TOPICS) and report.topics:
        chunks = _chunk(report.topics, MAX_TOPICS_PER_SLIDE)
        for index, chunk in enumerate(chunks, start=1):
            title = "Topics" if len(chunks) == 1 else f"Topics ({index}/{len(chunks)})"
            items = []
            for topic in chunk:
                items.append(f"{topic.title} ({topic.time_label})")
                items.extend(f"– {point}" for point in topic.key_points[:4])
            deck.bullets(title, items)

    # --- chapters ------------------------------------------------------------
    if report.includes(ReportSection.CHAPTERS) and report.chapters:
        chunks = _chunk(report.chapters, MAX_TOPICS_PER_SLIDE)
        for index, chunk in enumerate(chunks, start=1):
            title = "Chapters" if len(chunks) == 1 else f"Chapters ({index}/{len(chunks)})"
            deck.bullets(
                title,
                [f"{chapter.title} ({chapter.time_label})" for chapter in chunk],
            )

    # --- action items --------------------------------------------------------
    if report.includes(ReportSection.ACTION_ITEMS) and report.action_items:
        items = []
        for item in report.action_items[:10]:
            marker = "Action" if item.kind == "action" else "Decision"
            details = ", ".join(part for part in (item.owner, item.deadline) if part)
            items.append(
                f"{marker}: {item.text}" + (f" ({details})" if details else "")
            )
        deck.bullets("Actions and decisions", items)

    # --- chat Q&A ------------------------------------------------------------
    if report.includes(ReportSection.CHAT_QA) and report.chat:
        for entry in report.chat[:MAX_QA_SLIDES]:
            slide = deck.add(5, entry.question[:120])
            body = [(entry.answer, 18, False)]
            if entry.citations:
                body.append(("", 10, False))
                body.append(
                    (
                        "Sources: "
                        + ", ".join(
                            f"[{citation.source_ref}] {citation.source_name or 'video'} "
                            f"({citation.time_label})"
                            for citation in entry.citations
                        ),
                        12,
                        False,
                    )
                )
            else:
                body.append(("No resolved source citations for this answer.", 12, False))
            _fill_paragraphs(
                _textbox(slide, left=0.75, top=1.7, width=11.8, height=4.9), body
            )
        if len(report.chat) > MAX_QA_SLIDES:
            deck.section_title(
                "More questions",
                f"{len(report.chat) - MAX_QA_SLIDES} further Q&A pair(s) are in the PDF, DOCX "
                "and JSON exports. Presentations keep only the most recent "
                f"{MAX_QA_SLIDES}.",
            )

    # --- sources -------------------------------------------------------------
    if report.includes(ReportSection.SOURCES) and report.sources:
        chunks = _chunk(report.sources, MAX_SOURCES_PER_SLIDE)
        for index, chunk in enumerate(chunks[:3], start=1):
            title = "Sources" if len(chunks) == 1 else f"Sources ({index}/{min(len(chunks), 3)})"
            deck.bullets(
                title,
                [f"[{source.source_ref}] {source.label}" for source in chunk],
            )
        if len(chunks) > 3:
            deck.section_title(
                "More sources",
                "The complete list is in the document exports (PDF, DOCX, HTML, CSV).",
            )

    # --- transcript note -----------------------------------------------------
    if report.includes(ReportSection.TRANSCRIPT) and report.transcript:
        deck.section_title(
            "Full transcript",
            "A presentation is not the right place for the complete transcript "
            f"({len(report.transcript)} segments, {report.transcript_characters:,} characters). "
            "Export the PDF, DOCX or TXT report to read it in full.",
        )

    # --- closing -------------------------------------------------------------
    deck.section_title(
        "Generated by VideoMind AI",
        "Answers and summaries are derived from the stored transcript. "
        "Check the source timestamps in the document exports before quoting them.",
    )

    buffer = io.BytesIO()
    try:
        deck.prs.save(buffer)
    except Exception as error:  # noqa: BLE001 - translated into an export error
        raise PptxError(
            "The PowerPoint presentation could not be written. Try a smaller report or "
            "another format."
        ) from error
    return RenderResult(data=buffer.getvalue())


__all__ = ["PptxError", "build_pptx"]
