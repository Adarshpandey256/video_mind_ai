"""Validated media sources: local uploads (Step 2).

This module is the only place that turns a locally uploaded file into a trusted
:class:`StoredUpload`. It validates the filename, extension and size, writes the
stream to the application data directory in fixed-size chunks (never loading the
whole file into memory), generates a unique internal filename instead of trusting
the supplied path, and removes partial files when anything fails.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from config.settings import Settings, get_settings
from src.models.schemas import IngestionKind, SourceType, ValidatedSource
from src.utils.file_utils import (
    ensure_directory,
    guess_source_type,
    is_supported_media_file,
    normalise_extension,
    sanitize_filename,
    validate_upload_size,
)

logger = logging.getLogger(__name__)

#: Streaming chunk size - large media is never read into memory in one piece.
UPLOAD_CHUNK_SIZE = 1024 * 1024


class SourceValidationError(ValueError):
    """Raised when user supplied input cannot be used as a media source."""


@dataclass(frozen=True)
class StoredUpload:
    """A validated upload that has been written to the application data directory."""

    path: Path
    original_filename: str
    safe_filename: str
    source_type: SourceType
    size_bytes: int

    def as_source(self, title: str | None = None) -> ValidatedSource:
        """Convert the stored upload into the pipeline's source model."""
        return ValidatedSource(
            kind=IngestionKind.UPLOAD,
            source_type=self.source_type,
            original_filename=self.original_filename,
            title=title or Path(self.original_filename).stem,
            size_bytes=self.size_bytes,
            upload_path=str(self.path),
        )


def validate_upload(
    filename: str | None,
    size_bytes: int | None,
    settings: Settings | None = None,
) -> tuple[str, SourceType]:
    """Validate an upload's name and size.

    Returns the sanitised filename and the inferred source type, or raises
    :class:`SourceValidationError` with a message that is safe to display.
    """
    resolved = settings or get_settings()

    if not filename:
        raise SourceValidationError("Choose a file before starting.")

    if not is_supported_media_file(filename, resolved.allowed_media_extensions):
        extension = normalise_extension(filename) or "none"
        raise SourceValidationError(
            f"'{sanitize_filename(filename)}' is not a supported media format "
            f"(extension: {extension}). Supported formats: "
            f"{resolved.supported_extension_description()}."
        )

    if size_bytes is None or int(size_bytes) <= 0:
        raise SourceValidationError("The selected file is empty.")

    size_ok, size_message = validate_upload_size(int(size_bytes), resolved.max_upload_size_mb)
    if not size_ok:
        raise SourceValidationError(
            f"{size_message} Trim or compress the recording, or raise MAX_UPLOAD_SIZE_MB "
            "in your local .env file."
        )

    source_type = guess_source_type(filename)
    if source_type is SourceType.UNKNOWN:
        raise SourceValidationError(
            "The media type could not be determined from the file extension."
        )

    return sanitize_filename(filename), source_type


def unique_upload_path(directory: Path, filename: str) -> Path:
    """Build a collision-free, path-traversal-safe destination for an upload."""
    safe_name = sanitize_filename(filename)
    stem = Path(safe_name).stem or "media"
    suffix = Path(safe_name).suffix.lower()
    return Path(directory) / f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"


def save_upload(
    stream: BinaryIO,
    filename: str,
    settings: Settings | None = None,
    *,
    expected_size: int | None = None,
) -> StoredUpload:
    """Validate and store an upload, streaming it to disk.

    The configured limit is enforced while writing, so an oversized file is
    rejected even if the reported size is wrong. Partial files are always removed
    on failure.
    """
    resolved = settings or get_settings()
    safe_name, source_type = validate_upload(filename, expected_size, resolved)

    directory = ensure_directory(resolved.upload_dir)
    target = unique_upload_path(directory, safe_name)
    limit = resolved.max_upload_size_bytes
    written = 0

    try:
        with target.open("wb") as handle:
            while True:
                chunk = stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise SourceValidationError(
                        f"The upload exceeds the {resolved.max_upload_size_mb} MB limit. "
                        "Trim the recording or raise MAX_UPLOAD_SIZE_MB in your local .env file."
                    )
                handle.write(chunk)
    except BaseException:
        # Never leave a half-written upload behind.
        target.unlink(missing_ok=True)
        raise

    if written == 0:
        target.unlink(missing_ok=True)
        raise SourceValidationError("The uploaded file is empty.")

    logger.info(
        "Stored upload (name=%s, bytes=%s, type=%s)",
        safe_name,
        written,
        source_type.value,
    )
    return StoredUpload(
        path=target,
        original_filename=filename,
        safe_filename=safe_name,
        source_type=source_type,
        size_bytes=written,
    )


def is_inside(path: Path, directory: Path) -> bool:
    """True when ``path`` resolves inside ``directory``."""
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
    except (ValueError, OSError):
        return False
    return True


def cleanup_stored_file(path: Path | str | None, settings: Settings | None = None) -> bool:
    """Delete a stored upload, but only inside the managed upload directory.

    Refusing to delete anything outside that directory means a bug can never
    remove a user's original file.
    """
    if not path:
        return False
    resolved = settings or get_settings()
    candidate = Path(path)
    if not is_inside(candidate, resolved.upload_dir):
        logger.warning("Refused to delete a file outside the upload directory.")
        return False
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete a stored upload file.")
        return False
    return True
