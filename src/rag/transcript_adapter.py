"""Transcript normalisation for retrieval (Step 4).

Step 2 already produces a validated :class:`~src.models.schemas.TranscriptRecord`.
This module adapts it into the small, retrieval-specific shape the chunker needs,
without modifying or re-storing the original document:

* blank segments are dropped (a timestamp with no words is not evidence);
* timestamps are copied or left ``None`` - never estimated;
* a transcript that only has ``full_text`` is still usable, but its passages are
  marked as having no timeline;
* the video identifier must be present and safe, so a passage can never be
  attributed to a different recording.

The fingerprint is used to decide whether an existing vector index is still valid.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from src.models.schemas import SourceType, TranscriptRecord

logger = logging.getLogger(__name__)

#: Step 2 derives ids as ``vid-<hex>``; this keeps an id from becoming a path or a
#: collection-name injection.
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

_WHITESPACE = re.compile(r"\s+")


class TranscriptNormalizationError(RuntimeError):
    """Raised when a transcript cannot be normalised safely."""


def normalize_text(text: str | None) -> str:
    """Collapse whitespace without changing the words or their order."""
    return _WHITESPACE.sub(" ", (text or "").strip())


@dataclass(frozen=True)
class NormalizedSegment:
    """One usable transcript segment."""

    index: int
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None


@dataclass
class NormalizedTranscript:
    """The retrieval view of a Step 2 transcript."""

    video_id: str
    transcript_id: str | None
    title: str
    source_name: str | None
    source_type: SourceType
    duration_seconds: float | None
    segments: list[NormalizedSegment] = field(default_factory=list)
    used_full_text: bool = False
    skipped_segments: int = 0
    fingerprint: str = ""

    @property
    def is_empty(self) -> bool:
        """True when there is no text to index."""
        return not self.segments

    @property
    def has_timeline(self) -> bool:
        """True when at least one segment carries a real start time."""
        return any(segment.start_seconds is not None for segment in self.segments)

    @property
    def text_length(self) -> int:
        """Total number of characters across the usable segments."""
        return sum(len(segment.text) for segment in self.segments)


def _clean_timestamp(value: float | None) -> float | None:
    """Return a non-negative float, or ``None`` when the value is unusable."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def compute_fingerprint(video_id: str, segments: list[NormalizedSegment], full_text: str) -> str:
    """Stable content hash used to detect a changed transcript."""
    digest = hashlib.sha256()
    digest.update(video_id.encode("utf-8"))
    for segment in segments:
        start = f"{segment.start_seconds:.2f}" if segment.start_seconds is not None else "-"
        end = f"{segment.end_seconds:.2f}" if segment.end_seconds is not None else "-"
        digest.update(f"|{segment.index}|{start}|{end}|{segment.text}".encode("utf-8"))
    if not segments:
        digest.update(f"|text|{normalize_text(full_text)}".encode("utf-8"))
    return digest.hexdigest()


def normalize_transcript(record: TranscriptRecord | None) -> NormalizedTranscript:
    """Convert a stored transcript into the retrieval shape.

    Raises :class:`TranscriptNormalizationError` when the record cannot be
    attributed to a stable video safely; an empty transcript is returned as an
    empty :class:`NormalizedTranscript` so the caller can explain it.
    """
    if record is None:
        raise TranscriptNormalizationError("Select a transcript before indexing or asking.")

    video_id = str(record.video_id or "").strip()
    if not VIDEO_ID_PATTERN.match(video_id):
        raise TranscriptNormalizationError(
            "This transcript has no valid video identifier, so its passages cannot be kept "
            "separate from other recordings. Re-run transcription to rebuild it."
        )

    segments: list[NormalizedSegment] = []
    skipped = 0
    for position, segment in enumerate(record.segments or []):
        text = normalize_text(getattr(segment, "text", ""))
        if not text:
            skipped += 1
            continue
        start = _clean_timestamp(getattr(segment, "start_seconds", None))
        end = _clean_timestamp(getattr(segment, "end_seconds", None))
        if start is not None and end is not None and end < start:
            # An impossible interval is not repaired into a fake one: keep the start
            # (a real position) and drop the unusable end.
            end = None
        segments.append(
            NormalizedSegment(
                index=getattr(segment, "segment_index", position) or position,
                text=text,
                start_seconds=start,
                end_seconds=end,
            )
        )

    used_full_text = False
    if not segments:
        full_text = normalize_text(record.full_text)
        if full_text:
            used_full_text = True
            segments = [NormalizedSegment(index=0, text=full_text)]

    if skipped:
        logger.info("Dropped %s blank transcript segment(s) before indexing.", skipped)

    source_name = (
        (record.original_filename or "").strip()
        or (record.title or "").strip()
        or "Untitled recording"
    )

    return NormalizedTranscript(
        video_id=video_id,
        transcript_id=record.transcript_id,
        title=(record.title or "").strip() or "Untitled recording",
        source_name=source_name,
        source_type=record.source_type or SourceType.UNKNOWN,
        duration_seconds=record.duration_seconds,
        segments=segments,
        used_full_text=used_full_text,
        skipped_segments=skipped,
        fingerprint=compute_fingerprint(video_id, segments, record.full_text),
    )
