"""Transcript domain service (Step 2).

Builds :class:`TranscriptRecord` documents from a validated source plus a
transcription result, and exposes the read/write interface that Steps 3-5 use:
``build_record``, ``save``, ``load``, ``list_transcripts``, ``delete`` and the TXT
and JSON export helpers.

The interface never touches the filesystem directly; it talks to this service.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from config.settings import Settings, get_settings
from src.models.schemas import (
    MediaInfo,
    ProcessingStatus,
    TranscriptMetadata,
    TranscriptRecord,
    TranscriptionResult,
    ValidatedSource,
)
from src.services.transcript_storage import TranscriptStorage
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_timestamp

logger = logging.getLogger(__name__)


def video_id_for(source_reference: str) -> str:
    """Derive a stable per-source identifier used by later retrieval steps."""
    digest = hashlib.sha256(source_reference.encode("utf-8")).hexdigest()[:16]
    return f"vid-{digest}"


class TranscriptService:
    """Create, persist and read structured transcripts."""

    def __init__(
        self,
        settings: Settings | None = None,
        storage: TranscriptStorage | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._storage = storage or TranscriptStorage(self._settings.transcript_dir)

    @property
    def settings(self) -> Settings:
        """Return the settings used by this service."""
        return self._settings

    @property
    def storage(self) -> TranscriptStorage:
        """Return the underlying storage layer."""
        return self._storage

    # ------------------------------------------------------------------ building
    def build_record(
        self,
        *,
        source: ValidatedSource,
        result: TranscriptionResult,
        media: MediaInfo | None = None,
        title: str | None = None,
    ) -> TranscriptRecord:
        """Combine a source, media information and a transcription into a record."""
        reference = source.reference
        duration = media.duration_seconds if media and media.duration_seconds else None
        if duration is None:
            duration = result.duration_seconds

        return TranscriptRecord(
            video_id=video_id_for(reference),
            source_type=source.source_type,
            original_filename=source.original_filename,
            source_reference=reference,
            title=(title or source.title or source.original_filename or "Untitled recording"),
            duration_seconds=duration,
            language=result.language,
            transcription_model=result.model_name,
            processing_status=ProcessingStatus.COMPLETED,
            segment_count=len(result.segments),
            segments=result.segments,
            full_text=result.text,
        )

    # ------------------------------------------------------------------ storage
    def save(self, record: TranscriptRecord) -> TranscriptRecord:
        """Persist a transcript and return it with identifiers applied."""
        return self._storage.save(record)

    def load(self, transcript_id: str) -> TranscriptRecord:
        """Load one transcript by id."""
        return self._storage.load(transcript_id)

    def list_transcripts(self) -> list[TranscriptMetadata]:
        """List metadata for all stored transcripts, newest first."""
        return self._storage.list_metadata()

    def delete(self, transcript_id: str) -> bool:
        """Delete a stored transcript."""
        return self._storage.delete(transcript_id)

    def prune_old_transcripts(self, *, now: datetime | None = None) -> int:
        """Delete transcripts older than the configured retention window.

        Retention is disabled by default (``TRANSCRIPT_RETENTION_DAYS=0``), so
        transcripts are kept until they are deleted explicitly.
        """
        days = self._settings.transcript_retention_days
        if days <= 0:
            return 0

        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        removed = 0
        for metadata in self.list_transcripts():
            if metadata.created_at and metadata.transcript_id:
                if metadata.created_at < cutoff:
                    try:
                        if self.delete(metadata.transcript_id):
                            removed += 1
                    except Exception:  # noqa: BLE001 - pruning must never break a job
                        logger.warning("Could not prune transcript %s", metadata.transcript_id)
        if removed:
            logger.info("Pruned %s transcript(s) older than %s days.", removed, days)
        return removed

    # ------------------------------------------------------------------ exports
    def to_txt(self, record: TranscriptRecord) -> str:
        """Render a readable plain-text transcript (Step 2 download)."""
        lines = [
            record.title or "Transcript",
            "=" * max(3, len(record.title or "Transcript")),
            f"Source: {record.source_reference or 'unknown'}",
            f"Language: {record.language or 'not reported'}",
            f"Whisper model: {record.transcription_model or 'unknown'}",
            f"Segments: {record.segment_count}",
            f"Created: {record.created_at.isoformat()}",
            "",
            "Timestamped transcript",
            "---------------------",
        ]
        for segment in record.segments:
            if segment.has_timestamps:
                stamp = f"[{format_timestamp(segment.start_seconds)} - {format_timestamp(segment.end_seconds)}]"
            else:
                stamp = f"[{UNAVAILABLE_TIMESTAMP}]"
            lines.append(f"{stamp} {segment.text}")
        if not record.segments:
            lines.append("(no timestamped segments were produced)")
        lines.extend(["", "Full transcript", "---------------", record.full_text, ""])
        return "\n".join(lines)

    def to_json(self, record: TranscriptRecord) -> str:
        """Render the structured transcript as JSON."""
        return record.model_dump_json(indent=2)

    def download_filename(self, record: TranscriptRecord, suffix: str) -> str:
        """Build a safe download filename for a transcript export."""
        stem = (record.transcript_id or record.video_id or "transcript").replace(" ", "_")
        return f"{stem}.{suffix.lstrip('.')}"
