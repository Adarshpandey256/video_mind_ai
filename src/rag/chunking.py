"""Timestamp-aware chunking for retrieval (Step 4).

Why chunk at all? An embedding is a single vector, so it can only summarise a
small, focused passage. Long passages blur many ideas into one vector and
retrieval gets vague; very short passages lose the context around a statement.
Chunks of roughly one topic each solve both problems.

Design decisions
----------------
* Sizes are measured in **characters**, not tokens. Loading an embedding
  tokenizer just to measure text would add startup cost; characters are a good
  approximation for the sentence-shaped text Whisper produces. The defaults
  (1200 characters, 200 overlap) are configurable through
  ``RETRIEVAL_CHUNK_SIZE`` / ``RETRIEVAL_CHUNK_OVERLAP``.
* The splitter prefers sentence boundaries (``.``, ``!``, ``?`` and the Hindi
  ``।``), then segment boundaries, and only breaks mid-sentence when a single
  sentence is longer than a whole chunk.
* A chunk carries the union of its segments' timestamps. When the transcript has
  no timestamps the fields stay ``None`` - they are never estimated.
* Chunk ids are deterministic, and identical neighbouring chunks are dropped, so
  rebuilding an unchanged transcript is idempotent.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from config.settings import Settings, get_settings
from src.models.rag_schemas import DocumentType, RagChunk
from src.models.schemas import SourceType
from src.models.summary_schemas import StructuredSummary
from src.rag.transcript_adapter import (
    NormalizedSegment,
    NormalizedTranscript,
    normalize_text,
)

logger = logging.getLogger(__name__)

#: Sentence terminators for the languages Whisper is expected to produce.
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।])\s+")


class ChunkingError(RuntimeError):
    """Raised when a transcript cannot be split into usable chunks."""


def split_sentences(text: str) -> list[str]:
    """Split normalised text into sentences, never returning empty pieces."""
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    return [piece for piece in SENTENCE_BOUNDARY.split(cleaned) if piece.strip()]


@dataclass(frozen=True)
class _Unit:
    """The smallest piece the packer moves around."""

    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    segment_start_index: int | None = None
    segment_end_index: int | None = None


def _hard_split(text: str, size: int) -> list[str]:
    """Break an over-long sentence into pieces of at most ``size`` characters."""
    if size <= 0:
        return [text]
    return [text[index : index + size] for index in range(0, len(text), size)]


def _segment_units(segments: list[NormalizedSegment], size: int) -> list[_Unit]:
    """Turn segments into units, splitting only segments larger than a chunk."""
    units: list[_Unit] = []
    for segment in segments:
        text = normalize_text(segment.text)
        if not text:
            continue
        if len(text) <= size:
            units.append(
                _Unit(
                    text=text,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    segment_start_index=segment.index,
                    segment_end_index=segment.index,
                )
            )
            continue

        # A very long segment (common for machine-produced transcripts) is split on
        # sentences. Every piece keeps the segment's own interval: the words really do
        # occur inside that interval, so this is not an invented timestamp.
        pieces: list[str] = []
        buffer = ""
        for sentence in split_sentences(text):
            for piece in _hard_split(sentence, size):
                if buffer and len(buffer) + 1 + len(piece) > size:
                    pieces.append(buffer)
                    buffer = piece
                else:
                    buffer = f"{buffer} {piece}".strip()
        if buffer:
            pieces.append(buffer)

        for piece in pieces:
            units.append(
                _Unit(
                    text=piece,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    segment_start_index=segment.index,
                    segment_end_index=segment.index,
                )
            )
    return units


def _units_length(units: list[_Unit]) -> int:
    """Number of characters the units occupy once joined with spaces."""
    return sum(len(unit.text) for unit in units) + max(0, len(units) - 1)


def _overlap_tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """Return the trailing text worth repeating at the start of the next chunk.

    Whole units are preferred, but a transcript segment is often larger than the
    overlap budget. In that case the tail of the last segment is repeated instead,
    so consecutive chunks still share context. The repeated piece keeps the
    segment's own timestamps - the words do occur there.
    """
    if overlap <= 0 or not units:
        return []

    tail: list[_Unit] = []
    used = 0
    for unit in reversed(units):
        cost = len(unit.text) + (1 if tail else 0)
        if used + cost > overlap:
            break
        tail.append(unit)
        used += cost
    tail.reverse()
    if len(tail) == len(units):  # never repeat a whole chunk
        tail = tail[1:]
    if tail:
        return tail

    last = units[-1]
    snippet = last.text[-overlap:].lstrip()
    if " " in snippet:
        snippet = snippet.split(" ", 1)[1]
    if len(snippet) < 20:
        return []
    return [
        _Unit(
            text=snippet,
            start_seconds=last.start_seconds,
            end_seconds=last.end_seconds,
            segment_start_index=last.segment_start_index,
            segment_end_index=last.segment_end_index,
        )
    ]


def _pack(units: list[_Unit], *, size: int, overlap: int, minimum: int) -> list[list[_Unit]]:
    """Greedily pack units into ordered chunks with a small overlap."""
    if not units:
        return []

    chunks: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        if current and _units_length(current) + 1 + len(unit.text) > size:
            chunks.append(current)
            current = _overlap_tail(current, overlap)
        current.append(unit)
    if current:
        chunks.append(current)

    # A trailing fragment smaller than ``minimum`` is folded into the previous chunk
    # so we never store a nearly empty passage. Overlap units are not repeated.
    if len(chunks) >= 2 and _units_length(chunks[-1]) < minimum:
        tail = chunks.pop()
        already = {id(unit) for unit in chunks[-1]}
        chunks[-1].extend(unit for unit in tail if id(unit) not in already)

    return chunks


def _build_chunk(
    *,
    video_id: str,
    index: int,
    units: list[_Unit],
    document_type: DocumentType,
    source_name: str | None,
    source_type: SourceType,
) -> RagChunk:
    """Convert packed units into a validated :class:`RagChunk`."""
    text = " ".join(unit.text for unit in units).strip()
    text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()

    starts = [unit.start_seconds for unit in units if unit.start_seconds is not None]
    ends = [unit.end_seconds for unit in units if unit.end_seconds is not None]
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    if start is not None and end is not None and end < start:
        end = start

    segment_starts = [
        unit.segment_start_index for unit in units if unit.segment_start_index is not None
    ]
    segment_ends = [
        unit.segment_end_index for unit in units if unit.segment_end_index is not None
    ]

    return RagChunk(
        video_id=video_id,
        chunk_id=f"{video_id}:{document_type.value}:{index:04d}:{text_hash[:8]}",
        chunk_index=index,
        document_type=document_type,
        text=text,
        text_hash=text_hash,
        source_name=source_name,
        source_type=source_type,
        start_seconds=start,
        end_seconds=end,
        segment_start_index=min(segment_starts) if segment_starts else None,
        segment_end_index=max(segment_ends) if segment_ends else None,
    )


class TranscriptChunker:
    """Split a normalised transcript (or a summary) into retrievable chunks."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def settings(self) -> Settings:
        """Return the settings that control the chunk sizes."""
        return self._settings

    @property
    def _size(self) -> int:
        return self._settings.retrieval_chunk_size

    @property
    def _overlap(self) -> int:
        return min(
            self._settings.retrieval_chunk_overlap,
            max(0, self._settings.retrieval_chunk_size // 2),
        )

    @property
    def _minimum(self) -> int:
        return min(self._settings.retrieval_min_chunk_size, self._settings.retrieval_chunk_size)

    def chunk_transcript(self, transcript: NormalizedTranscript) -> list[RagChunk]:
        """Split a normalised transcript into ordered, timestamped chunks.

        Raises :class:`ChunkingError` when there is no text to index or when the
        chunk budget is exceeded (rather than silently truncating the video).
        """
        if transcript.is_empty:
            raise ChunkingError("This transcript is empty, so there is nothing to index.")

        units = _segment_units(transcript.segments, self._size)
        if not units:
            raise ChunkingError("This transcript is empty, so there is nothing to index.")

        packed = _pack(units, size=self._size, overlap=self._overlap, minimum=self._minimum)
        if len(packed) > self._settings.rag_max_chunks_per_video:
            raise ChunkingError(
                f"This recording needs {len(packed)} chunks, which exceeds the configured "
                f"limit of {self._settings.rag_max_chunks_per_video}. Raise "
                "RAG_MAX_CHUNKS_PER_VIDEO or RETRIEVAL_CHUNK_SIZE if you really want to index it."
            )

        chunks: list[RagChunk] = []
        for index, group in enumerate(packed):
            chunk = _build_chunk(
                video_id=transcript.video_id,
                index=len(chunks),
                units=group,
                document_type=DocumentType.TRANSCRIPT,
                source_name=transcript.source_name,
                source_type=transcript.source_type,
            )
            if chunks and chunks[-1].text == chunk.text:
                continue  # overlap or repetition produced a duplicate passage
            chunks.append(chunk)

        logger.info(
            "Chunked transcript %s into %s chunk(s) of at most %s characters.",
            transcript.transcript_id or transcript.video_id,
            len(chunks),
            self._size,
        )
        return chunks

    def chunk_summary(
        self, summary: StructuredSummary, *, video_id: str, source_name: str | None = None
    ) -> list[RagChunk]:
        """Index the summary as labelled context (never as the evidence of record).

        Summary chunks deliberately carry no timestamps: a summary sentence is not a
        verbatim location in the recording, so claiming one would be misleading.
        """
        text = summary_text(summary)
        if not text.strip():
            return []

        units = [_Unit(text=piece) for piece in split_sentences(text)]
        if not units:
            return []
        packed = _pack(units, size=self._size, overlap=self._overlap, minimum=self._minimum)

        chunks: list[RagChunk] = []
        for group in packed:
            chunk = _build_chunk(
                video_id=video_id,
                index=len(chunks),
                units=group,
                document_type=DocumentType.SUMMARY,
                source_name=source_name,
                source_type=summary.source_type,
            )
            if chunks and chunks[-1].text == chunk.text:
                continue
            chunks.append(chunk)
        return chunks


def summary_text(summary: StructuredSummary) -> str:
    """Render a structured summary as one readable document for retrieval.

    Returns an empty string when the summary contains no substance, so an empty
    document never becomes a retrievable "source".
    """
    body: list[str] = []
    if summary.short_summary:
        body.append(f"In short: {summary.short_summary}")
    if summary.overall_summary:
        body.append(summary.overall_summary)

    if summary.key_takeaways:
        body.append("Key takeaways:")
        body.extend(
            f"- {item.title}: {item.explanation}".rstrip(": ")
            for item in summary.key_takeaways
        )
    if summary.main_topics:
        body.append("Main topics:")
        body.extend(
            f"- {topic.title}: {topic.explanation}".rstrip(": ")
            for topic in summary.main_topics
        )
    if summary.important_points:
        body.append("Important points:")
        body.extend(f"- {point.point}" for point in summary.important_points)
    if summary.chapters:
        body.append("Chapters:")
        body.extend(
            f"- {chapter.title}: {chapter.description}".rstrip(": ")
            for chapter in summary.chapters
        )
    if summary.action_items:
        body.append("Action items:")
        body.extend(f"- {item.task}" for item in summary.action_items)
    if summary.decisions:
        body.append("Decisions:")
        body.extend(f"- {decision.decision}" for decision in summary.decisions)

    if not body:
        return ""
    lines = [f"Summary of {summary.source_title or 'this recording'}.", *body]
    return "\n".join(line for line in lines if line)
