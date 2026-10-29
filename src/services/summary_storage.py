"""Safe JSON persistence for validated summaries (Step 3).

One document per summary, written atomically (temporary file + ``os.replace``), the
same convention Step 2 uses for transcripts. The documents hold metadata plus the
structured summary only - never the transcript text, never a media file, and never a
credential - so a summary record stays small and safe to keep next to the transcript
it refers to.
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

from src.models.summary_schemas import StructuredSummary, SummaryMetadata

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SUMMARY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class SummaryStorageError(RuntimeError):
    """Raised when a summary cannot be read or written."""


class SummaryNotFoundError(SummaryStorageError):
    """Raised when a summary id does not exist."""


def new_summary_id() -> str:
    """Return a unique, filesystem-safe summary identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"sm-{stamp}-{uuid.uuid4().hex[:8]}"


class SummaryStorage:
    """Store and retrieve summaries as one JSON document per summary."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        """Directory holding the summary documents."""
        return self._directory

    def ensure_ready(self) -> Path:
        """Create the storage directory when needed."""
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory

    @staticmethod
    def new_summary_id() -> str:
        """Return a unique summary identifier."""
        return new_summary_id()

    def path_for(self, summary_id: str) -> Path:
        """Return the document path for an id, refusing unsafe identifiers."""
        if not SUMMARY_ID_PATTERN.match(str(summary_id or "")):
            raise SummaryStorageError("The summary identifier is not valid.")
        return self._directory / f"{summary_id}.json"

    def exists(self, summary_id: str) -> bool:
        """True when a summary document is present."""
        try:
            return self.path_for(summary_id).is_file()
        except SummaryStorageError:
            return False

    # ------------------------------------------------------------------ writes
    def save(self, summary: StructuredSummary) -> StructuredSummary:
        """Persist a summary atomically and return it with its identifier set."""
        if not summary.summary_id:
            summary = summary.model_copy(update={"summary_id": new_summary_id()})
        summary_id = str(summary.summary_id)
        if not summary.source_transcript_reference and summary.source_transcript_id:
            summary = summary.model_copy(
                update={"source_transcript_reference": summary.source_transcript_id}
            )

        self.ensure_ready()
        target = self.path_for(summary_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "summary": summary.model_dump(mode="json"),
        }

        temporary: str | None = None
        try:
            handle_fd, temporary = tempfile.mkstemp(
                dir=str(self._directory), prefix=f".{summary_id}-", suffix=".tmp"
            )
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        except OSError as error:
            logger.warning("Summary write failed: %s", type(error).__name__)
            raise SummaryStorageError(
                "The summary could not be saved to disk. Check that the data directory is "
                "writable and has free space."
            ) from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

        logger.info(
            "Stored summary %s (mode=%s, status=%s)",
            summary_id,
            summary.summary_type.value,
            summary.processing_status.value,
        )
        return summary

    # ------------------------------------------------------------------ reads
    def load(self, summary_id: str) -> StructuredSummary:
        """Load a summary document by id."""
        path = self.path_for(summary_id)
        if not path.is_file():
            raise SummaryNotFoundError("That summary is no longer available.")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            logger.warning("Summary %s is not valid JSON.", summary_id)
            raise SummaryStorageError(
                "This summary file is damaged and cannot be read."
            ) from error
        except OSError as error:
            raise SummaryStorageError("This summary file could not be opened.") from error

        document = payload.get("summary", payload) if isinstance(payload, dict) else payload
        try:
            return StructuredSummary.model_validate(document)
        except ValidationError as error:
            logger.warning("Summary %s failed validation: %s", summary_id, error.error_count())
            raise SummaryStorageError(
                "This summary is incomplete or was written by an incompatible version."
            ) from error

    def list_metadata(
        self,
        *,
        transcript_id: str | None = None,
        source_id: str | None = None,
    ) -> list[SummaryMetadata]:
        """List metadata for every readable summary, newest first.

        Passing ``transcript_id`` or ``source_id`` narrows the result, so the
        interface can show the saved summaries for the transcript it is displaying.
        """
        self.ensure_ready()
        entries: list[SummaryMetadata] = []
        for path in sorted(self._directory.glob("*.json")):
            try:
                summary = self.load(path.stem)
            except SummaryStorageError:
                logger.info("Skipping unreadable summary file: %s", path.name)
                continue
            metadata = SummaryMetadata.from_summary(summary)
            if transcript_id and metadata.transcript_id != transcript_id:
                continue
            if source_id and metadata.source_id != source_id:
                continue
            entries.append(metadata)
        entries.sort(key=lambda item: item.created_at, reverse=True)
        return entries

    def latest_for_transcript(
        self,
        transcript_id: str,
        *,
        options_fingerprint: str | None = None,
    ) -> StructuredSummary | None:
        """Return the newest valid summary for a transcript, or ``None``.

        When ``options_fingerprint`` is given we also try to match the summary
        settings, but a newer summary generated with different settings is still
        returned so stored work is never hidden from the user.
        """
        candidates: list[StructuredSummary] = []
        for metadata in self.list_metadata(transcript_id=transcript_id):
            if not metadata.summary_id:
                continue
            try:
                candidates.append(self.load(metadata.summary_id))
            except SummaryStorageError:
                continue
        if not candidates:
            return None
        if options_fingerprint:
            for summary in candidates:
                if summary.options_fingerprint == options_fingerprint:
                    return summary
        return candidates[0]

    def delete(self, summary_id: str) -> bool:
        """Delete a summary document; returns True when it existed."""
        path = self.path_for(summary_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise SummaryStorageError(
                "The summary could not be deleted. Close any program using the file and retry."
            ) from error
        logger.info("Deleted summary %s", summary_id)
        return True
