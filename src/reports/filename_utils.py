"""Safe download filenames for generated reports.

The title comes from the user, so it is never used as a path. A stem is built
from a whitelist of characters, path separators and traversal sequences cannot
survive, and Windows device names are refused. The extension is validated
separately, so a caller cannot smuggle ``../x.exe`` in through either part.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

#: Combining marks (``Mn``/``Mc``) are part of a word in Devanagari and many other
#: scripts; ``\w`` alone would strip the vowel signs and corrupt the title.
_WHITESPACE = re.compile(r"\s+")
_DOTS_AND_DASHES = re.compile(r"^[.\-_]+|[.\-_]+$")
_EXTENSION = re.compile(r"^[a-z0-9]{1,6}$")

#: Windows refuses to create files with these names, even with an extension.
WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{digit}" for digit in range(1, 10)),
        *(f"lpt{digit}" for digit in range(1, 10)),
    }
)


def safe_extension(extension: str) -> str:
    """Return a validated lowercase extension without the dot."""
    cleaned = str(extension or "").strip().lstrip(".").lower()
    if not _EXTENSION.match(cleaned):
        raise ValueError(f"Unsupported file extension: {extension!r}")
    return cleaned


def _sanitise(text: str) -> str:
    """Keep letters, digits, combining marks and separators; replace the rest."""
    characters: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if character.isalnum() or character in "_- ." or category.startswith("M"):
            characters.append(character)
        else:
            characters.append("_")
    return "".join(characters)


def safe_stem(title: str | None, *, max_length: int = 80, fallback: str = "video") -> str:
    """Turn free text into a filename stem that stays inside one directory."""
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = _sanitise(text)
    text = _WHITESPACE.sub("_", text).strip("._-")
    text = _DOTS_AND_DASHES.sub("", text)
    text = re.sub(r"_{2,}", "_", text)
    if len(text) > max_length:
        text = text[:max_length].rstrip("._- ")
    if not text or text.lower() in WINDOWS_RESERVED_NAMES:
        return fallback
    return text


def report_filename(
    title: str | None,
    extension: str,
    *,
    suffix: str = "report",
    now: datetime | None = None,
    max_length: int = 120,
) -> str:
    """Build a unique download name such as ``lecture_report_20261021_1930.pdf``.

    ``suffix`` distinguishes exports of the same report (``report``,
    ``transcript``, ``chat``, ``sources``). A timestamp keeps repeated downloads
    from overwriting each other in the user's download folder.
    """
    ext = safe_extension(extension)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    tail = f"_{safe_stem(suffix, max_length=20, fallback='report')}_{stamp}.{ext}"
    stem_budget = max(10, max_length - len(tail))
    stem = safe_stem(title, max_length=stem_budget)
    return f"{stem}{tail}"
