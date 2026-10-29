#!/usr/bin/env python
"""Regenerate ``docs/VideoMind_AI_Complete_Project_Guide.pdf`` from its Markdown source.

This is **documentation tooling**, not part of the application runtime: it imports
nothing from ``src/`` and is never executed by the Streamlit app or by pytest.

It renders the Markdown handbook (``docs/VideoMind_AI_Complete_Project_Guide.md``)
with ReportLab Platypus:

* cover page with document facts,
* front matter,
* a real table of contents with page numbers and clickable entries,
* page numbers, running header (document title + current chapter) and footer,
* PDF bookmarks / outline for chapters and sections,
* fenced code blocks in a monospaced box, wrapped so nothing overflows the page,
* Markdown pipe tables re-flowed to the printable width,
* block quotes, bullets, numbered lists and horizontal rules.

Only the Markdown subset actually used by the handbook is supported (headings,
paragraphs, fenced code, pipe tables, lists, quotes, rules, inline bold / italic /
code / links).  Unknown syntax degrades to plain text rather than raising.

Usage
-----
    python scripts/generate_project_guide_pdf.py
    python scripts/generate_project_guide_pdf.py --md docs/guide.md --out docs/guide.pdf

Requirements: ``reportlab`` (PDF) and optionally ``pypdf`` (page count report).
Neither is needed to run the application.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFontFile
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

# --------------------------------------------------------------------------- #
# Paths and layout constants
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MD = PROJECT_ROOT / "docs" / "VideoMind_AI_Complete_Project_Guide.md"
DEFAULT_PDF = PROJECT_ROOT / "docs" / "VideoMind_AI_Complete_Project_Guide.pdf"

DOC_TITLE = "VideoMind AI - Complete Project Guide"
DOC_SUBTITLE = "A beginner-to-advanced walkthrough of the actual source code"

MARGIN_L = 46.0
MARGIN_R = 42.0
MARGIN_T = 60.0
MARGIN_B = 48.0

PAGE_W, PAGE_H = A4
FRAME_W = PAGE_W - MARGIN_L - MARGIN_R
FRAME_H = PAGE_H - MARGIN_T - MARGIN_B

ACCENT = colors.HexColor("#1F3864")
ACCENT_SOFT = colors.HexColor("#2E5496")
RULE = colors.HexColor("#B7C3D9")
CODE_BG = colors.HexColor("#F4F5F7")
CODE_BORDER = colors.HexColor("#D3D8E0")
CODE_TEXT = colors.HexColor("#1A1A1A")
INLINE_CODE = colors.HexColor("#9C2A1F")
TABLE_GRID = colors.HexColor("#C2CBDC")
TABLE_HEAD_BG = colors.HexColor("#1F3864")
TABLE_ALT_BG = colors.HexColor("#EFF3FA")
QUOTE_BG = colors.HexColor("#F6F7FB")
MUTED = colors.HexColor("#5A6472")

#: characters that only the symbol fallback font provides (emoji and friends)
SYMBOL_CHARS: set[str] = set()


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

_BODY_FAMILY = [
    (
        "VM-Body",
        [
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
        ],
    ),
    (
        "VM-Body-Bold",
        [
            r"C:\Windows\Fonts\arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ],
    ),
    (
        "VM-Body-Italic",
        [
            r"C:\Windows\Fonts\ariali.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/Library/Fonts/Arial Italic.ttf",
        ],
    ),
    (
        "VM-Body-BoldItalic",
        [
            r"C:\Windows\Fonts\arialbi.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
            "/Library/Fonts/Arial Bold Italic.ttf",
        ],
    ),
]

_MONO_FAMILY = [
    (
        "VM-Mono",
        [
            r"C:\Windows\Fonts\consola.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/Library/Fonts/Courier New.ttf",
        ],
    ),
    (
        "VM-Mono-Bold",
        [
            r"C:\Windows\Fonts\consolab.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/Library/Fonts/Courier New Bold.ttf",
        ],
    ),
]

_SYMBOL_FONT = [
    r"C:\Windows\Fonts\seguisym.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
]


def _first_existing(paths: list[str]) -> str | None:
    for candidate in paths:
        if os.path.exists(candidate):
            return candidate
    return None


def register_fonts() -> dict[str, bool]:
    """Register TrueType fonts when available, otherwise fall back to base-14.

    Returns a report of what was used so the CLI can print it honestly.
    """
    report: dict[str, bool] = {}
    for name, candidates in _BODY_FAMILY + _MONO_FAMILY + [("VM-Symbol", _SYMBOL_FONT)]:
        path = _first_existing(candidates)
        if path:
            pdfmetrics.registerFont(TTFont(name, path))
            report[name] = True
        else:
            report[name] = False

    if report["VM-Body"]:
        pdfmetrics.registerFontFamily(
            "VM-Body",
            normal="VM-Body",
            bold="VM-Body-Bold" if report["VM-Body-Bold"] else "VM-Body",
            italic="VM-Body-Italic" if report["VM-Body-Italic"] else "VM-Body",
            boldItalic=(
                "VM-Body-BoldItalic" if report["VM-Body-BoldItalic"] else "VM-Body"
            ),
        )
    report["VM-Mono-Bold"] = report["VM-Mono-Bold"] and report["VM-Mono"]
    return report


def font_name(role: str, report: dict[str, bool]) -> str:
    """Map a logical role to a registered font name (TTF or base-14 fallback)."""
    if role == "body":
        return "VM-Body" if report.get("VM-Body") else "Helvetica"
    if role == "body-bold":
        return "VM-Body-Bold" if report.get("VM-Body-Bold") else "Helvetica-Bold"
    if role == "body-italic":
        return "VM-Body-Italic" if report.get("VM-Body-Italic") else "Helvetica-Oblique"
    if role == "body-bolditalic":
        return (
            "VM-Body-BoldItalic" if report.get("VM-Body-BoldItalic") else "Helvetica-BoldOblique"
        )
    if role == "mono":
        return "VM-Mono" if report.get("VM-Mono") else "Courier"
    if role == "mono-bold":
        return "VM-Mono-Bold" if report.get("VM-Mono-Bold") else "Courier-Bold"
    if role == "symbol":
        return "VM-Symbol" if report.get("VM-Symbol") else ""
    raise KeyError(role)


def glyph_coverage(text: str, report: dict[str, bool]) -> set[str]:
    """Characters in ``text`` that the chosen fonts cannot draw."""
    sources = {
        "body": _first_existing(_BODY_FAMILY[0][1]),
        "bold": _first_existing(_BODY_FAMILY[1][1]),
        "italic": _first_existing(_BODY_FAMILY[2][1]),
        "mono": _first_existing(_MONO_FAMILY[0][1]),
        "symbol": _first_existing(_SYMBOL_FONT),
    }
    maps: dict[str, set[int]] = {}
    for role, path in sources.items():
        if not path:
            maps[role] = set()
            continue
        try:
            maps[role] = set(TTFontFile(path).charToGlyph)
        except Exception:  # pragma: no cover - defensive, fonts are local files
            maps[role] = set()

    missing: set[str] = set()
    for ch in set(text):
        if ord(ch) < 128 or ch == "\ufe0f":  # variation selector: drop it later
            continue
        code = ord(ch)
        if code in maps["body"] or code in maps["mono"]:
            continue
        if code in maps["symbol"]:
            SYMBOL_CHARS.add(ch)
            continue
        missing.add(ch)
    return missing


# --------------------------------------------------------------------------- #
# Markdown parsing
# --------------------------------------------------------------------------- #

RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
#: an opening fence carries an optional language token and nothing else, so prose that
#: happens to start with three backticks is not mistaken for the start of a listing
RE_FENCE = re.compile(r"^```([A-Za-z0-9_+.\-]*)\s*$")
RE_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")
RE_UL = re.compile(r"^(\s*)[-*+]\s+(.*)$")
RE_OL = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
RE_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
RE_CODE_SPAN = re.compile(r"`([^`]+)`")
RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
RE_ITALIC = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)\*(?!\*)")
RE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
RE_BARE_URL = re.compile(r"(?<![\"'>=])\bhttps?://[^\s<>()\]]+")


class Block:
    """One parsed Markdown block."""

    __slots__ = ("kind", "level", "text", "rows", "items", "info")

    def __init__(self, kind: str, **kw) -> None:
        self.kind = kind
        self.level: int = kw.get("level", 0)
        self.text: str = kw.get("text", "")
        self.rows: list[list[str]] = kw.get("rows", [])
        self.items: list[tuple[str, str]] = kw.get("items", [])
        self.info: str = kw.get("info", "")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        preview = (self.text or str(self.rows) or str(self.items))[:60]
        return f"Block({self.kind!r}, {preview!r})"


def _is_table_row(line: str) -> bool:
    return line.startswith("|") and line.count("|") >= 2


def _split_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def parse_markdown(text: str) -> list[Block]:
    """Parse the handbook Markdown into a flat list of blocks."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        fence = RE_FENCE.match(stripped)
        if fence:
            info = fence.group(1).strip()
            body: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            blocks.append(Block("code", text="\n".join(body), info=info))
            continue

        heading = RE_HEADING.match(stripped)
        if heading:
            blocks.append(
                Block("heading", level=len(heading.group(1)), text=heading.group(2).strip())
            )
            i += 1
            continue

        if RE_HR.match(stripped):
            blocks.append(Block("hr"))
            i += 1
            continue

        if _is_table_row(line):
            rows: list[list[str]] = []
            while i < n and _is_table_row(lines[i]):
                cells = _split_row(lines[i])
                if not RE_TABLE_SEP.match(lines[i].strip()):
                    rows.append(cells)
                i += 1
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                blocks.append(Block("table", rows=rows))
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(Block("quote", text=" ".join(q for q in quote if q)))
            continue

        marker = RE_OL.match(line) or RE_UL.match(line)
        if marker:
            ordered = marker.re is RE_OL
            items: list[tuple[str, str]] = []
            while i < n:
                current = lines[i]
                if not current.strip():
                    # a blank line only ends the list when the next line is not a marker
                    nxt = lines[i + 1] if i + 1 < n else ""
                    m = RE_OL.match(nxt) or RE_UL.match(nxt)
                    if m is None:
                        break
                    i += 1
                    continue
                m = RE_OL.match(current) or RE_UL.match(current)
                if m:
                    if RE_OL.match(current):
                        items.append(("ol", m.group(3)))
                    else:
                        items.append(("ul", m.group(2)))
                    i += 1
                    continue
                if items and (current.startswith("  ") or current.startswith("\t")):
                    kind, text_so_far = items[-1]
                    items[-1] = (kind, f"{text_so_far} {current.strip()}")
                    i += 1
                    continue
                break
            blocks.append(Block("list", items=items, info="ol" if ordered else "ul"))
            continue

        para: list[str] = []
        while i < n:
            current = lines[i]
            if not current.strip():
                break
            if (
                RE_HEADING.match(current.strip())
                or RE_FENCE.match(current.strip())
                or RE_HR.match(current.strip())
                or _is_table_row(current)
                or current.strip().startswith(">")
                or RE_UL.match(current)
                or RE_OL.match(current)
            ):
                break
            para.append(current.strip())
            i += 1
        if para:
            blocks.append(Block("para", text=" ".join(para)))
        continue

    return blocks


# --------------------------------------------------------------------------- #
# Inline markup
# --------------------------------------------------------------------------- #


def _looks_like_path(target: str) -> bool:
    return not re.match(r"^(?:https?|mailto|file):", target)


def inline_markup(
    text: str,
    report: dict[str, bool],
    *,
    code_size: float = 8.4,
    body_size: float = 9.6,
) -> str:
    """Convert inline Markdown to ReportLab paragraph markup (safe XML)."""
    text = text.replace("\ufe0f", "")

    placeholders: list[str] = []

    def stash(payload: str) -> str:
        placeholders.append(payload)
        return f"\x00{len(placeholders) - 1}\x00"

    def code_repl(match: re.Match[str]) -> str:
        body = xml_escape(match.group(1))
        return stash(
            f'<font face="{font_name("mono", report)}" size="{code_size}"'
            f' color="#{INLINE_CODE.hexval()[2:]}">{body}</font>'
        )

    text = RE_CODE_SPAN.sub(code_repl, text)

    def link_repl(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2).strip()
        if target.startswith(("http://", "https://")):
            return stash(
                f'<link href="{xml_escape(target)}" color="#1F3864">{label}</link>'
            )
        if _looks_like_path(target):
            if label.strip() == target:
                return stash(
                    f'<font face="{font_name("mono", report)}" size="{code_size}"'
                    f' color="#28313F">{xml_escape(label)}</font>'
                )
            return stash(
                f'{label} <font face="{font_name("mono", report)}" size="{code_size}"'
                f' color="#28313F">({xml_escape(target)})</font>'
            )
        return stash(label)

    text = RE_LINK.sub(link_repl, text)
    text = xml_escape(text)

    # symbol-only characters (emoji in prose) need the fallback font
    symbol_font = font_name("symbol", report)
    if SYMBOL_CHARS and symbol_font:
        for ch in sorted(SYMBOL_CHARS):
            if ch in text:
                text = text.replace(
                    xml_escape(ch), f'<font face="{symbol_font}" size="{body_size}">{xml_escape(ch)}</font>'
                )
    for ch in sorted(SYMBOL_CHARS):
        if symbol_font:
            break
        text = text.replace(xml_escape(ch), "")

    text = RE_BOLD.sub(r"<b>\1</b>", text)
    text = RE_ITALIC.sub(r"<i>\1</i>", text)

    def bare_url(match: re.Match[str]) -> str:
        url = match.group(0)
        return stash(f'<link href="{xml_escape(url)}" color="#1F3864">{xml_escape(url)}</link>')

    text = RE_BARE_URL.sub(bare_url, text)

    def restore(match: re.Match[str]) -> str:
        return placeholders[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, text)


def plain_text(markup: str) -> str:
    """Strip ReportLab markup - used for width estimates and TOC labels."""
    text = re.sub(r"<[^>]+>", "", markup)
    return text.replace("\x00", "")


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #


def build_styles(report: dict[str, bool]) -> dict[str, ParagraphStyle]:
    body = font_name("body", report)
    bold = font_name("body-bold", report)
    italic = font_name("body-italic", report)
    bolditalic = font_name("body-bolditalic", report)
    mono = font_name("mono", report)

    styles = {
        "body": ParagraphStyle(
            name="Body",
            fontName=body,
            fontSize=9.6,
            leading=13.4,
            textColor=colors.HexColor("#191C22"),
            spaceAfter=5.5,
            alignment=TA_LEFT,
            splitLongWords=1,
        ),
        "body-just": ParagraphStyle(
            name="BodyJustified",
            parent=ParagraphStyle("body-base", fontName=body, fontSize=9.6, leading=13.4),
            alignment=TA_JUSTIFY,
            spaceAfter=5.5,
        ),
        "h1": ParagraphStyle(
            name="DocTitle",
            fontName=bold,
            fontSize=19,
            leading=23,
            textColor=ACCENT,
            spaceBefore=6,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            name="ChapterHeading",
            fontName=bold,
            fontSize=15,
            leading=18.5,
            textColor=ACCENT,
            spaceBefore=2,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "h3": ParagraphStyle(
            name="SectionHeading",
            fontName=bold,
            fontSize=11.6,
            leading=14.5,
            textColor=ACCENT_SOFT,
            spaceBefore=12,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "h4": ParagraphStyle(
            name="SubHeading",
            fontName=bolditalic,
            fontSize=10.2,
            leading=13,
            textColor=colors.HexColor("#333B49"),
            spaceBefore=9,
            spaceAfter=3,
            keepWithNext=1,
        ),
        "bullet": ParagraphStyle(
            name="Bullet",
            fontName=body,
            fontSize=9.6,
            leading=13.2,
            leftIndent=15,
            bulletIndent=4,
            spaceAfter=2.6,
            bulletFontName=body,
            bulletFontSize=9.6,
        ),
        "bullet2": ParagraphStyle(
            name="Bullet2",
            fontName=body,
            fontSize=9.6,
            leading=13.2,
            leftIndent=29,
            bulletIndent=17,
            spaceAfter=2.6,
            bulletFontName=body,
            bulletFontSize=9.6,
        ),
        "ordered": ParagraphStyle(
            name="Ordered",
            fontName=body,
            fontSize=9.6,
            leading=13.2,
            leftIndent=20,
            bulletIndent=5,
            spaceAfter=2.6,
            bulletFontName=bold,
            bulletFontSize=9.4,
        ),
        "quote": ParagraphStyle(
            name="Quote",
            fontName=italic,
            fontSize=9.3,
            leading=12.9,
            textColor=colors.HexColor("#2C3440"),
            leftIndent=14,
            rightIndent=6,
            spaceBefore=2,
            spaceAfter=2,
        ),
        "code": ParagraphStyle(
            name="Code",
            fontName=mono,
            fontSize=7.0,
            leading=9.0,
            textColor=CODE_TEXT,
            leftIndent=0,
            rightIndent=0,
        ),
        "table": ParagraphStyle(
            name="TableCell",
            fontName=body,
            fontSize=7.6,
            leading=9.6,
            textColor=colors.HexColor("#191C22"),
            wordWrap="CJK",
            splitLongWords=1,
            spaceAfter=0,
            spaceBefore=0,
        ),
        "table-head": ParagraphStyle(
            name="TableHead",
            fontName=bold,
            fontSize=7.6,
            leading=9.6,
            textColor=colors.white,
            wordWrap="CJK",
            splitLongWords=1,
        ),
        "cover-title": ParagraphStyle(
            name="CoverTitle",
            fontName=bold,
            fontSize=27,
            leading=32,
            alignment=TA_CENTER,
            textColor=ACCENT,
        ),
        "cover-title2": ParagraphStyle(
            name="CoverTitle2",
            fontName=bold,
            fontSize=20,
            leading=25,
            alignment=TA_CENTER,
            textColor=ACCENT_SOFT,
        ),
        "cover-sub": ParagraphStyle(
            name="CoverSub",
            fontName=italic,
            fontSize=12.5,
            leading=16,
            alignment=TA_CENTER,
            textColor=ACCENT_SOFT,
        ),
        "cover-line": ParagraphStyle(
            name="CoverLine",
            fontName=body,
            fontSize=10.5,
            leading=14,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#2C3440"),
        ),
        "cover-small": ParagraphStyle(
            name="CoverSmall",
            fontName=body,
            fontSize=8.6,
            leading=11.6,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
        "cover-cell": ParagraphStyle(
            name="CoverCell",
            fontName=body,
            fontSize=9.0,
            leading=11.8,
            wordWrap="CJK",
        ),
        "toc0": ParagraphStyle(
            name="TOC0",
            fontName=bold,
            fontSize=10,
            leading=16,
            textColor=ACCENT,
            spaceBefore=6,
        ),
        "toc1": ParagraphStyle(
            name="TOC1",
            fontName=body,
            fontSize=8.4,
            leading=11.6,
            leftIndent=16,
            firstLineIndent=-2,
            textColor=colors.HexColor("#2C3440"),
        ),
        "caption": ParagraphStyle(
            name="Caption",
            fontName=italic,
            fontSize=8.4,
            leading=11,
            textColor=MUTED,
            spaceAfter=6,
        ),
    }
    return styles


# --------------------------------------------------------------------------- #
# Table layout
# --------------------------------------------------------------------------- #


def _cell_flowables(rows: list[list[str]], styles: dict, report: dict) -> list[list]:
    out: list[list] = []
    for r_index, row in enumerate(rows):
        style = styles["table-head"] if r_index == 0 else styles["table"]
        out.append(
            [
                Paragraph(inline_markup(cell, report, code_size=7.2, body_size=7.6), style)
                for cell in row
            ]
        )
    return out


def _estimate_width(text: str, size: float) -> float:
    """Rough rendered width of a string at ``size`` in the body font."""
    return len(plain_text(text)) * size * 0.5


def compute_widths(rows: list[list[str]], avail: float) -> tuple[list[float], float]:
    """Return (column widths, cell font size) that fit ``avail`` points."""
    ncols = max(len(r) for r in rows)
    pad = 9.0

    for size in (7.6, 7.0, 6.4, 5.8, 5.2):
        minima: list[float] = []
        desired: list[float] = []
        for col in range(ncols):
            longest_word = 0.0
            total = 0.0
            for row in rows:
                cell = row[col] if col < len(row) else ""
                words = plain_text(cell).split() or [""]
                longest_word = max(longest_word, max(_estimate_width(w, size) for w in words))
                total = max(total, _estimate_width(cell, size))
                # a two-line cell is acceptable: cap the "needed" width
            minima.append(min(longest_word + pad * 2, avail * 0.45))
            desired.append(min(max(total * 0.55 + pad * 2, minima[-1]), avail * 0.6))
        if sum(minima) <= avail:
            leftover = avail - sum(minima)
            weights = [max(d - m, 0.0) for d, m in zip(desired, minima)]
            total_weight = sum(weights)
            if leftover <= 0 or total_weight <= 0:
                widths = [m * (avail / sum(minima)) for m in minima]
            else:
                widths = [m + leftover * w / total_weight for m, w in zip(minima, weights)]
            scale = avail / sum(widths)
            return [w * scale for w in widths], size
    # nothing fits: shrink proportionally (still readable thanks to CJK wrapping)
    widths = [avail / ncols] * ncols
    return widths, 5.0


def build_table(rows: list[list[str]], styles: dict, report: dict) -> Table:
    """Build a printable table from Markdown rows."""
    widths, size = compute_widths(rows, FRAME_W)
    if size != 7.6:
        styles["table"].fontSize = size
        styles["table-head"].fontSize = size
        styles["table"].leading = size + 2.0
        styles["table-head"].leading = size + 2.0

    data = _cell_flowables(rows, styles, report)
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, TABLE_GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
    ]
    for row_index in range(1, len(data)):
        if row_index % 2 == 0:
            style.append(("BACKGROUND", (0, row_index), (-1, row_index), TABLE_ALT_BG))
    table.setStyle(TableStyle(style))
    return table


# --------------------------------------------------------------------------- #
# Code blocks
# --------------------------------------------------------------------------- #


class CodeBox(Preformatted):
    """A ``Preformatted`` that paints its own background box and splits cleanly.

    A ``Preformatted`` inside a one-cell ``Table`` cannot split (a single table row
    taller than the frame raises ``LayoutError``), and ``Preformatted.split`` returns
    plain fragments that would lose the box.  Drawing the furniture here keeps long
    listings flowing across pages while every fragment still looks like code.
    """

    def __init__(
        self,
        text: str,
        style: ParagraphStyle,
        *,
        pad: float = 6.0,
        max_line_length: int | None = None,
        new_line_chars: str = "",
    ) -> None:
        super().__init__(
            text,
            style,
            dedent=0,
            maxLineLength=max_line_length,
            newLineChars=new_line_chars,
        )
        self._pad = pad

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        inner_w = max(availWidth - 2 * self._pad, 40.0)
        super().wrap(inner_w, max(availHeight - 2 * self._pad, self.style.leading))
        self.width = inner_w + 2 * self._pad
        self.height = self.style.leading * len(self.lines) + 2 * self._pad
        return self.width, self.height

    def split(self, availWidth: float, availHeight: float):  # noqa: D401 - Flowable API
        usable = availHeight - 2 * self._pad
        if usable < self.style.leading:
            return []
        fit = int(usable // self.style.leading)
        if fit >= len(self.lines):
            return []
        head = CodeBox(
            "\n".join(self.lines[:fit]), self.style, pad=self._pad
        )
        tail = CodeBox(
            "\n".join(self.lines[fit:]), self.style, pad=self._pad
        )
        return [head, tail]

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(CODE_BG)
        canvas.setStrokeColor(CODE_BORDER)
        canvas.setLineWidth(0.5)
        canvas.rect(0, 0, self.width, self.height, stroke=1, fill=1)
        canvas.translate(self._pad, self._pad)
        inner_h = self.height - 2 * self._pad
        if self.style.textColor:
            canvas.setFillColor(self.style.textColor)
        text_object = canvas.beginText(0, inner_h - self.style.fontSize)
        text_object.setFont(
            self.style.fontName, self.style.fontSize, self.style.leading
        )
        for line in self.lines:
            text_object.textLine(line)
        canvas.drawText(text_object)
        canvas.restoreState()


def build_code_block(
    code: str, styles: dict, report: dict, *, mono_marker: bool = True
) -> KeepTogether | CodeBox:
    """Render a fenced code block inside a bordered, wrapping box."""
    lines = code.expandtabs(4).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return Spacer(1, 4)

    longest = max(len(line) for line in lines)
    pad = 6.0
    available = FRAME_W - 2 * pad

    char_ratio = 0.6  # monospaced advance width in em
    for size in (7.0, 6.6, 6.2, 5.8, 5.4, 5.0):
        chars = int(available / (char_ratio * size))
        if longest <= chars:
            chosen_size, max_chars = size, chars
            break
    else:
        chosen_size = 5.0
        max_chars = int(available / (char_ratio * chosen_size))

    style = ParagraphStyle(
        name="CodeBlock",
        parent=styles["code"],
        fontSize=chosen_size,
        leading=chosen_size + 1.9,
    )
    marker = ""
    if mono_marker and font_has_glyph(report, "mono", "\u21aa"):
        marker = " \u21aa "

    box = CodeBox(
        "\n".join(lines),
        style,
        pad=pad,
        max_line_length=max_chars,
        new_line_chars=marker,
    )
    if len(lines) <= 18:
        # short excerpts should not be orphaned at the foot of a page
        return KeepTogether([box])
    return box


_GLYPH_CACHE: dict[tuple[str, str], bool] = {}


def font_has_glyph(report: dict[str, bool], role: str, ch: str) -> bool:
    """True when the font registered for ``role`` can draw ``ch``."""
    key = (role, ch)
    if key in _GLYPH_CACHE:
        return _GLYPH_CACHE[key]
    name = font_name(role, report)
    if not name:
        _GLYPH_CACHE[key] = False
        return False
    path = {"mono": _MONO_FAMILY[0][1], "mono-bold": _MONO_FAMILY[1][1],
            "body": _BODY_FAMILY[0][1]}.get(role)
    if path is None:
        _GLYPH_CACHE[key] = False
        return False
    try:
        font = TTFontFile(_first_existing(path) or "")
        ok = ord(ch) in font.charToGlyph
    except Exception:  # pragma: no cover
        ok = False
    _GLYPH_CACHE[key] = ok
    return ok


# --------------------------------------------------------------------------- #
# Document template
# --------------------------------------------------------------------------- #


def roman(number: int) -> str:
    """Lower-case roman numerals for the front matter."""
    numerals = [
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
        (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
        (5, "v"), (4, "iv"), (1, "i"),
    ]
    out = []
    for value, symbol in numerals:
        while number >= value:
            out.append(symbol)
            number -= value
    return "".join(out) or "i"


class GuideDocTemplate(BaseDocTemplate):
    """A4 document with a cover page, roman front matter and numbered body."""

    def __init__(self, filename: str, report: dict[str, bool], **kw) -> None:
        super().__init__(filename, **kw)
        self.report = report
        self.body_start_page: int | None = None
        self.front_start_page: int | None = None
        self.current_chapter = ""
        self._toc_counter = 0

        frame = Frame(
            MARGIN_L, MARGIN_B, FRAME_W, FRAME_H, id="page",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        cover_frame = Frame(
            MARGIN_L, MARGIN_B, FRAME_W, FRAME_H, id="cover",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[cover_frame], onPage=self._on_cover),
                PageTemplate(id="front", frames=[cover_frame], onPage=self._on_front),
                PageTemplate(
                    id="body",
                    frames=[frame],
                    onPage=self._on_body,
                    onPageEnd=self._on_body_end,
                ),
            ]
        )

    # -- per-pass state ----------------------------------------------------- #

    def beforeDocument(self) -> None:
        """Reset per-pass state.

        ``multiBuild`` re-runs the whole story until the table of contents stops
        changing.  Bookmark keys must therefore be identical on every pass, or the
        TOC entries never compare equal and the build never converges.
        """
        self.body_start_page = None
        self.front_start_page = None
        self.current_chapter = ""
        self._toc_counter = 0

    # -- page furniture ---------------------------------------------------- #

    def _fonts(self) -> tuple[str, str, str]:
        return (
            font_name("body", self.report),
            font_name("body-bold", self.report),
            font_name("mono", self.report),
        )

    def _on_cover(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(ACCENT)
        canvas.rect(0, PAGE_H - 16, PAGE_W, 16, stroke=0, fill=1)
        canvas.setFillColor(ACCENT_SOFT)
        canvas.rect(0, 0, PAGE_W, 10, stroke=0, fill=1)
        canvas.restoreState()

    def _write_footer(self, canvas, label: str) -> None:
        body, bold, mono = self._fonts()
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_L, MARGIN_B - 20, PAGE_W - MARGIN_R, MARGIN_B - 20)
        canvas.setFont(mono, 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PAGE_W / 2.0, MARGIN_B - 33, label)
        canvas.setFont(body, 7.4)
        canvas.drawString(MARGIN_L, MARGIN_B - 33, DOC_TITLE)
        canvas.drawRightString(
            PAGE_W - MARGIN_R, MARGIN_B - 33, "Generated from the real source tree"
        )
        canvas.restoreState()

    def _on_front(self, canvas, doc) -> None:
        if self.front_start_page is None:
            self.front_start_page = canvas.getPageNumber()
        number = canvas.getPageNumber() - self.front_start_page + 1
        self._write_footer(canvas, f"- {roman(number)} -")

    def _write_running_header(self, canvas) -> None:
        body, bold, mono = self._fonts()
        canvas.saveState()
        canvas.setFont(body, 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN_L, PAGE_H - MARGIN_T + 26, DOC_TITLE)
        chapter = self.current_chapter
        if len(chapter) > 78:
            chapter = chapter[:77] + "\u2026"
        canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - MARGIN_T + 26, chapter)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN_L, PAGE_H - MARGIN_T + 20, PAGE_W - MARGIN_R, PAGE_H - MARGIN_T + 20)
        canvas.restoreState()

    def _on_body(self, canvas, doc) -> None:
        if self.body_start_page is None:
            self.body_start_page = canvas.getPageNumber()
        number = canvas.getPageNumber() - self.body_start_page + 1
        self._write_footer(canvas, f"- {number} -")

    def _on_body_end(self, canvas, doc) -> None:
        # Drawn once the page is laid out, so the first page of a chapter shows
        # that chapter in the running header instead of the previous one.
        self._write_running_header(canvas)

    # -- structure --------------------------------------------------------- #

    def afterFlowable(self, flowable) -> None:
        style = getattr(flowable, "style", None)
        name = getattr(style, "name", "")
        text = getattr(flowable, "text", "")
        if not text:
            return
        if name not in ("ChapterHeading", "SectionHeading", "SubHeading", "DocTitle"):
            return

        label = plain_text(text)
        self._toc_counter += 1
        key = f"toc-{self._toc_counter}"

        if name == "ChapterHeading":
            self.current_chapter = label
        level = 0
        if name == "SectionHeading":
            level = 1
        elif name == "SubHeading":
            level = 2

        # the canvas page number is the one this flowable was drawn on
        page = self.canv.getPageNumber()
        self.canv.bookmarkPage(key)
        title = label[:120]
        try:
            self.canv.addOutlineEntry(title, key, level=level, closed=(level == 0))
        except Exception:  # pragma: no cover - outline levels must not skip
            pass

        # Front matter pages carry roman numerals, so only body headings enter the
        # table of contents; their numbers are relative to the first Arabic page.
        # Fourth-level headings stay in the PDF outline but are kept out of the
        # printed contents, which would otherwise run to seven pages.
        if name in ("DocTitle", "SubHeading") or self.body_start_page is None:
            return
        if page < self.body_start_page:
            return
        self.notify("TOCEntry", (level, label, page - self.body_start_page + 1, key))


# --------------------------------------------------------------------------- #
# Cover page
# --------------------------------------------------------------------------- #


def cover_flowables(facts: dict[str, str], styles: dict, report: dict) -> list:
    story: list = [Spacer(1, 92)]
    story.append(Paragraph("VideoMind AI", styles["cover-title"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Complete Project Guide", styles["cover-title2"]))
    story.append(Spacer(1, 10))
    story.append(
        HRFlowable(width="62%", thickness=1.1, color=ACCENT, spaceAfter=12, hAlign="CENTER")
    )
    story.append(Paragraph(DOC_SUBTITLE, styles["cover-sub"]))
    story.append(Spacer(1, 26))
    story.append(
        Paragraph(xml_escape(facts.get("Project", "AI video assistant")), styles["cover-line"])
    )
    story.append(Spacer(1, 34))

    rows = [
        ["Document version", facts.get("Document version", "1.0")],
        ["Document date", facts.get("Document date", "")],
        ["Entry point", facts.get("Entry point", "app.py")],
        ["Audience", facts.get("Audience", "Student project viva and demonstrations")],
    ]
    data = [
        [
            Paragraph(f"<b>{inline_markup(label, report, code_size=8.6, body_size=9.0)}</b>",
                      styles["cover-cell"]),
            Paragraph(inline_markup(value, report, code_size=8.6, body_size=9.0),
                      styles["cover-cell"]),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=[110, FRAME_W - 110], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ("LEFTPADDING", (0, 0), (0, -1), 0),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 30))
    story.append(
        Paragraph(
            "Generated from the real project source tree. Every file path, class and "
            "function named in this handbook exists in the workspace; anything that "
            "could not be verified is labelled as unverified.",
            styles["cover-small"],
        )
    )
    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Rebuild this PDF with <font face='%s'>python scripts/generate_project_guide_pdf.py</font>"
            % font_name("mono", report),
            styles["cover-small"],
        )
    )
    return story


def front_matter_heading(styles: dict) -> Paragraph:
    return Paragraph("About this handbook", styles["h1"])


# --------------------------------------------------------------------------- #
# Story building
# --------------------------------------------------------------------------- #


def split_document(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    """Split parsed blocks into front matter and the body (drop the Markdown TOC)."""
    toc_index = None
    for index, block in enumerate(blocks):
        if block.kind == "heading" and block.level == 2 and block.text.lower() == "table of contents":
            toc_index = index
            break
    if toc_index is None:
        return blocks, []

    front = blocks[:toc_index]
    rest: list[Block] = []
    skipping = True
    for block in blocks[toc_index + 1 :]:
        if skipping and block.kind == "hr":
            skipping = False
            continue
        if not skipping:
            rest.append(block)
    return front, rest


def extract_facts(blocks: list[Block]) -> dict[str, str]:
    """Read the front-matter metadata table into a dict."""
    facts: dict[str, str] = {}
    for block in blocks:
        if block.kind != "table":
            continue
        for row in block.rows:
            if len(row) >= 2:
                facts.setdefault(plain_text(row[0]), plain_text(row[1]))
        if facts:
            break
    return facts


def build_story(
    blocks: list[Block],
    styles: dict,
    report: dict,
    facts: dict[str, str],
    toc: TableOfContents,
) -> list:
    front_blocks, body_blocks = split_document(blocks)

    #: ``NextPageTemplate`` must precede ``PageBreak``: the break starts the next
    #: page, and the pending template decides which furniture that page gets.
    story: list = [NextPageTemplate("cover")]
    story.extend(cover_flowables(facts, styles, report))
    story.append(NextPageTemplate("front"))
    story.append(PageBreak())

    story.append(front_matter_heading(styles))
    # the document title already sits on the cover: do not repeat it here
    front_blocks = [
        b for b in front_blocks if not (b.kind == "heading" and b.level == 1)
    ]
    story.extend(render_blocks(front_blocks, styles, report, first_section=True))

    story.append(PageBreak())
    story.append(Paragraph("Table of contents", styles["h1"]))
    story.append(
        Paragraph(
            "Chapter and section entries below are clickable and carry the PDF page "
            "number (body numbering).",
            styles["caption"],
        )
    )
    story.append(toc)
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    story.extend(render_blocks(body_blocks, styles, report, first_section=True))
    return story


def render_blocks(
    blocks: list[Block], styles: dict, report: dict, *, first_section: bool = False
) -> list:
    """Render every block, keeping a little layout state."""
    out: list = []
    # a section that already starts a fresh page must not emit a second PageBreak
    prev_was_break = first_section
    in_code = False

    for index, block in enumerate(blocks):
        if block.kind == "heading":
            if block.level == 1:
                if not prev_was_break:
                    out.append(PageBreak())
                out.append(Paragraph(inline_markup(block.text, report, body_size=19), styles["h1"]))
                out.append(HRFlowable(width="100%", thickness=1.0, color=ACCENT, spaceAfter=10))
                prev_was_break = False
            elif block.level == 2:
                if not prev_was_break:
                    out.append(PageBreak())
                out.append(
                    Paragraph(inline_markup(block.text, report, body_size=15), styles["h2"])
                )
                out.append(HRFlowable(width="100%", thickness=0.8, color=ACCENT_SOFT, spaceAfter=9))
                prev_was_break = False
            elif block.level == 3:
                out.append(
                    Paragraph(inline_markup(block.text, report, body_size=11.6), styles["h3"])
                )
                prev_was_break = False
            else:
                out.append(
                    Paragraph(inline_markup(block.text, report, body_size=10.2), styles["h4"])
                )
                prev_was_break = False
            in_code = False
            continue

        if block.kind == "para":
            # left aligned throughout: justified text plus inline code produces
            # distracting rivers of white space in a technical handbook
            out.append(Paragraph(inline_markup(block.text, report), styles["body"]))
            prev_was_break = False
            continue

        if block.kind == "list":
            for item_kind, text in block.items:
                markup = inline_markup(text, report)
                if item_kind == "ol":
                    out.append(Paragraph(markup, styles["ordered"], bulletText="\u2022"))
                else:
                    out.append(Paragraph(markup, styles["bullet"], bulletText="\u2022"))
            out.append(Spacer(1, 4))
            prev_was_break = False
            continue

        if block.kind == "quote":
            markup = inline_markup(block.text, report)
            quote = Table(
                [[Paragraph(markup, styles["quote"])]],
                colWidths=[FRAME_W],
                hAlign="LEFT",
            )
            quote.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG),
                        ("LINEBEFORE", (0, 0), (0, -1), 2.0, ACCENT_SOFT),
                        ("LEFTPADDING", (0, 0), (-1, -1), 9),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            out.append(Spacer(1, 3))
            out.append(quote)
            out.append(Spacer(1, 6))
            prev_was_break = False
            continue

        if block.kind == "table":
            if in_code:
                continue
            out.append(build_table(block.rows, styles, report))
            out.append(Spacer(1, 8))
            prev_was_break = False
            continue

        if block.kind == "code":
            out.append(build_code_block(block.text, styles, report))
            out.append(Spacer(1, 7))
            prev_was_break = False
            in_code = True
            continue

        if block.kind == "hr":
            out.append(HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=4, spaceAfter=8))
            prev_was_break = False
            in_code = False
            continue

    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def make_toc(styles: dict) -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [styles["toc0"], styles["toc1"]]
    toc.dotsMinLevel = 0
    return toc


def generate(md_path: Path, pdf_path: Path, *, quiet: bool = False) -> dict:
    report = register_fonts()
    source = md_path.read_text(encoding="utf-8")

    missing = glyph_coverage(source, report)
    if missing and not quiet:
        names = ", ".join(
            f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}" for ch in sorted(missing)
        )
        print(f"  ! no font glyph for: {names}", file=sys.stderr)

    styles = build_styles(report)
    blocks = parse_markdown(source)
    facts = extract_facts(blocks)

    title_source = next(
        (b.text for b in blocks if b.kind == "heading" and b.level == 1), DOC_TITLE
    )
    toc = make_toc(styles)
    story = build_story(blocks, styles, report, facts, toc)

    doc = GuideDocTemplate(
        str(pdf_path),
        report,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=plain_text(title_source),
        author="VideoMind AI project documentation",
        subject="Complete project explanation, source walkthrough and viva preparation",
        creator="scripts/generate_project_guide_pdf.py",
    )
    doc.multiBuild(story)

    stats = {
        "blocks": len(blocks),
        "facts": facts,
        "tables": sum(1 for b in blocks if b.kind == "table"),
        "code_blocks": sum(1 for b in blocks if b.kind == "code"),
        "missing_glyphs": sorted(missing),
        "fonts": report,
    }

    try:  # optional: report the page count so the operator sees the result
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        stats["pages"] = len(reader.pages)
        stats["chars"] = sum(len(page.extract_text() or "") for page in reader.pages)
    except Exception:  # pragma: no cover - pypdf is optional
        stats["pages"] = None
        stats["chars"] = None

    if not quiet:
        print(f"  markdown : {md_path}")
        print(f"  pdf      : {pdf_path}")
        print(f"  blocks   : {stats['blocks']} "
              f"(tables={stats['tables']}, code={stats['code_blocks']})")
        print(f"  fonts    : body={report.get('VM-Body')} mono={report.get('VM-Mono')} "
              f"symbol={report.get('VM-Symbol')}")
        if stats["pages"]:
            size_kb = pdf_path.stat().st_size / 1024
            print(f"  pages    : {stats['pages']} ({size_kb:.0f} KiB, "
                  f"{stats['chars']} characters extracted)")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--md", type=Path, default=DEFAULT_MD, help="handbook Markdown")
    parser.add_argument("--out", type=Path, default=DEFAULT_PDF, help="output PDF")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = parser.parse_args(argv)

    if not args.md.exists():
        print(f"Markdown source not found: {args.md}", file=sys.stderr)
        return 2

    generate(args.md, args.out, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
