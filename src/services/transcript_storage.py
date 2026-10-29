"""Safe JSON persistence for structured transcripts (Step 2).

One document per transcript, written atomically (temporary file + ``os.replace``)
so an interrupted write can never corrupt an existing record. Documents are small
metadata plus text - media files are never stored here, and neither are secrets.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from src.models.schemas import TranscriptMetadata, TranscriptRecord

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TRANSCRIPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class TranscriptStorageError(RuntimeError):
    """Raised when a transcript cannot be read or written."""


class TranscriptNotFoundError(TranscriptStorageError):
    """Raised when a transcript id does not exist."""


class TranscriptStorage:
    """Store and retrieve transcripts as one JSON document per transcript."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        """Directory holding the transcript documents."""
        return self._directory

    def ensure_ready(self) -> Path:
        """Create the storage directory when needed."""
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory

    @staticmethod
    def new_transcript_id() -> str:
        """Return a unique, filesystem-safe transcript identifier."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"tr-{stamp}-{uuid.uuid4().hex[:8]}"

    def path_for(self, transcript_id: str) -> Path:
        """Return the document path for an id, refusing unsafe identifiers."""
        if not TRANSCRIPT_ID_PATTERN.match(str(transcript_id or "")):
            raise TranscriptStorageError("The transcript identifier is not valid.")
        return self._directory / f"{transcript_id}.json"

    def exists(self, transcript_id: str) -> bool:
        """True when a transcript document is present."""
        try:
            return self.path_for(transcript_id).is_file()
        except TranscriptStorageError:
            return False

    def save(self, record: TranscriptRecord) -> TranscriptRecord:
        """Persist a transcript atomically and return it with its identifier set."""
        if not record.transcript_id:
            record = record.model_copy(
                update={"transcript_id": self.new_transcript_id()}
            )
        transcript_id = str(record.transcript_id)
        record = record.model_copy(update={"text_reference": transcript_id})

        self.ensure_ready()
        target = self.path_for(transcript_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "transcript": record.model_dump(mode="json"),
        }

        temporary: str | None = None
        try:
            handle_fd, temporary = tempfile.mkstemp(
                dir=str(self._directory), prefix=f".{transcript_id}-", suffix=".tmp"
            )
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        except OSError as error:
            logger.warning("Transcript write failed: %s", type(error).__name__)
            raise TranscriptStorageError(
                "The transcript could not be saved to disk. Check that the data directory is "
                "writable and has free space."
            ) from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

        logger.info("Stored transcript %s (%s segments)", transcript_id, record.segment_count)
        return record

    def load(self, transcript_id: str) -> TranscriptRecord:
        """Load a transcript document by id."""
        path = self.path_for(transcript_id)
        if not path.is_file():
            raise TranscriptNotFoundError("That transcript is no longer available.")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            logger.warning("Transcript %s is not valid JSON.", transcript_id)
            raise TranscriptStorageError(
                "This transcript file is damaged and cannot be read."
            ) from error
        except OSError as error:
            raise TranscriptStorageError("This transcript file could not be opened.") from error

        document = payload.get("transcript", payload) if isinstance(payload, dict) else payload
        try:
            return TranscriptRecord.model_validate(document)
        except ValidationError as error:
            logger.warning("Transcript %s failed validation: %s", transcript_id, error.error_count())
            raise TranscriptStorageError(
                "This transcript is incomplete or was written by an incompatible version."
            ) from error

    def list_metadata(self) -> list[TranscriptMetadata]:
        """List metadata for every readable transcript, newest first."""
        self.ensure_ready()
        entries: list[TranscriptMetadata] = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                record = self.load(path.stem)
            except TranscriptStorageError:
                logger.info("Skipping unreadable transcript file: %s", path.name)
                continue
            entries.append(TranscriptMetadata.model_validate(record.model_dump(mode="json")))
        entries.sort(key=lambda item: item.created_at, reverse=True)
        return entries

    def delete(self, transcript_id: str) -> bool:
        """Delete a transcript document; returns True when it existed."""
        path = self.path_for(transcript_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise TranscriptStorageError(
                "The transcript could not be deleted. Close any program using the file and retry."
            ) from error
        logger.info("Deleted transcript %s", transcript_id)
        return True
