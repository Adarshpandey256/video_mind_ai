"""Small filesystem helpers used by the UI and by later processing steps."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from config.settings import (
    AUDIO_EXTENSIONS,
    DEFAULT_MEDIA_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from src.models.schemas import SourceType

_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._ -]")
_MAX_FILENAME_LENGTH = 120


def ensure_directory(path: Path) -> Path:
    """Create a directory (including parents) and return it."""
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def human_readable_size(num_bytes: float) -> str:
    """Format a byte count as a short human readable string."""
    size = float(num_bytes)
    if size < 0:
        raise ValueError("size must not be negative")
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover - unreachable


def normalise_extension(filename: str | None) -> str:
    """Return the lower-case extension of a filename (with leading dot)."""
    if not filename:
        return ""
    return Path(str(filename)).suffix.lower()


def is_supported_media_file(
    filename: str | None,
    allowed_extensions: tuple[str, ...] = DEFAULT_MEDIA_EXTENSIONS,
) -> bool:
    """Check whether a filename has a supported video/audio extension."""
    extension = normalise_extension(filename)
    if not extension:
        return False
    allowed = {item.lower() for item in allowed_extensions}
    return extension in allowed


def validate_upload_size(size_bytes: int, max_upload_size_mb: int) -> tuple[bool, str]:
    """Validate an upload size against the configured limit.

    Returns ``(is_valid, message)`` where the message is ready for the UI.
    """
    limit_bytes = int(max_upload_size_mb) * 1024 * 1024
    if size_bytes <= 0:
        return False, "The selected file is empty."
    if size_bytes > limit_bytes:
        return (
            False,
            "The selected file is "
            f"{human_readable_size(size_bytes)}, which exceeds the configured limit of "
            f"{max_upload_size_mb} MB.",
        )
    return True, f"{human_readable_size(size_bytes)} is within the {max_upload_size_mb} MB limit."


def sanitize_filename(filename: str | None) -> str:
    """Return a filesystem-safe filename without directories or unsafe characters."""
    if not filename:
        return "unnamed"
    base = Path(str(filename).replace("\\", "/")).name
    normalised = unicodedata.normalize("NFKD", base)
    cleaned = _UNSAFE_CHARACTERS.sub("_", normalised).strip(" ._")
    if not cleaned:
        return "unnamed"
    if len(cleaned) > _MAX_FILENAME_LENGTH:
        suffix = Path(cleaned).suffix
        stem = Path(cleaned).stem[: _MAX_FILENAME_LENGTH - len(suffix)]
        cleaned = f"{stem}{suffix}"
    return cleaned


def guess_source_type(filename: str | None) -> SourceType:
    """Infer the media kind of a local file from its extension."""
    extension = normalise_extension(filename)
    if extension in AUDIO_EXTENSIONS:
        return SourceType.LOCAL_AUDIO
    if extension in VIDEO_EXTENSIONS:
        return SourceType.LOCAL_VIDEO
    return SourceType.UNKNOWN


def looks_like_url(value: str | None) -> bool:
    """Cheap validation for user supplied URLs."""
    if not value:
        return False
    text = value.strip()
    return text.startswith(("http://", "https://")) and " " not in text and len(text) > 11
