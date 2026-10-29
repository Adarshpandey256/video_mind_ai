"""Prepare a Step 2 transcript for the model (Step 3).

Two jobs live here:

* **Rendering** - turn the timestamped segments into the single text block that is
  placed inside the transcript markers of a prompt.
* **Bounded input size** - estimate how many tokens that block costs and, when it
  is too large for one safe request, split it into ordered chunks that keep their
  original segment references and a small overlap.

Token estimation uses ``tiktoken``'s ``cl100k_base`` encoding when it is importable
(it ships with ``openai-whisper``) and falls back to a conservative word/character
heuristic otherwise. Character count alone is never treated as a token count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from config.settings import Settings, get_settings
from src.models.schemas import TranscriptRecord, TranscriptSegment
from src.utils.time_utils import format_timestamp

logger = logging.getLogger(__name__)

#: Used only when no tokenizer is available. Chosen to over-count rather than
#: under-count, so a request never exceeds the budget it was planned for.
FALLBACK_CHARS_PER_TOKEN = 3.2
FALLBACK_WORDS_TO_TOKENS = 1.5


@lru_cache(maxsize=1)
def _encoder():
    """Return a tiktoken encoder, or ``None`` when tiktoken is unavailable."""
    try:  # pragma: no cover - exercised indirectly when tiktoken is present
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - any failure means "estimate instead"
        logger.info("tiktoken is unavailable; using a conservative token estimate.")
        return None


def tokenizer_is_exact() -> bool:
    """True when a real tokenizer is available for the token estimate."""
    return _encoder() is not None


def estimate_tokens(text: str | None) -> int:
    """Estimate the token count of a string.

    Real when a tokenizer is available, otherwise a deliberately conservative
    heuristic. Empty input costs nothing.
    """
    if not text:
        return 0
    encoder = _encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001 - fall through to the heuristic
            logger.debug("Token encoding failed; using the conservative estimate.")
    by_chars = len(text) / FALLBACK_CHARS_PER_TOKEN
    by_words = len(text.split()) * FALLBACK_WORDS_TO_TOKENS
    return int(max(by_chars, by_words)) + 1


def render_segment(segment: TranscriptSegment, *, include_timestamps: bool = True) -> str:
    """Render one segment, prefixing it with its source position when known."""
    text = (segment.text or "").strip()
    if not text:
        return ""
    if not include_timestamps or not segment.has_timestamps:
        return text
    return f"[{format_timestamp(segment.start_seconds)}] {text}"


def render_transcript(
    transcript: TranscriptRecord,
    *,
    include_timestamps: bool = True,
    segments: list[TranscriptSegment] | None = None,
) -> str:
    """Render a transcript as the prompt text block.

    Falls back to ``full_text`` when a transcript has no usable segments, so a
    valid but segment-less transcript is still summarisable.
    """
    source = segments if segments is not None else list(transcript.segments)
    lines = [line for line in (render_segment(item, include_timestamps=include_timestamps) for item in source) if line]
    if lines:
        return "\n".join(lines)
    return (transcript.full_text or "").strip()


@dataclass
class TranscriptChunk:
    """One ordered slice of a transcript, with its source references intact."""

    index: int
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    token_estimate: int = 0
    start_seconds: float | None = None
    end_seconds: float | None = None

    @property
    def segment_count(self) -> int:
        """Number of transcript segments in this chunk."""
        return len(self.segments)

    @property
    def segment_range(self) -> str:
        """Readable source interval, honest when the transcript has no timeline."""
        if self.start_seconds is None:
            return "position unavailable"
        if self.end_seconds is None or self.end_seconds <= self.start_seconds:
            return format_timestamp(self.start_seconds)
        return f"{format_timestamp(self.start_seconds)} - {format_timestamp(self.end_seconds)}"


@dataclass
class PreparedTranscript:
    """Everything the summarisation service needs about the input size."""

    transcript_text: str
    tokens: int
    chunks: list[TranscriptChunk] = field(default_factory=list)
    segments_without_timestamps: int = 0

    @property
    def needs_chunking(self) -> bool:
        """True when the rendered transcript exceeded the single-request budget."""
        return len(self.chunks) > 1

    @property
    def chunk_count(self) -> int:
        """Number of chunks produced (1 for a single-pass run)."""
        return max(1, len(self.chunks))

    @property
    def has_timeline(self) -> bool:
        """True when at least one chunk carries a usable source interval."""
        return any(chunk.start_seconds is not None for chunk in self.chunks)


class TranscriptPreprocessor:
    """Render and, when necessary, chunk a transcript for the provider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def settings(self) -> Settings:
        """Return the settings that control the budgets."""
        return self._settings

    def measure(self, text: str) -> int:
        """Estimate the token cost of a prompt body."""
        return estimate_tokens(text)

    def prepare(
        self,
        transcript: TranscriptRecord,
        *,
        include_timestamps: bool = True,
        reserved_tokens: int = 0,
    ) -> PreparedTranscript:
        """Render the transcript and chunk it when it does not fit one request.

        ``reserved_tokens`` is the space already taken by the system prompt, the
        user requirements and the expected output, so it is subtracted from the
        per-request budget before the transcript is apportioned.
        """
        text = render_transcript(transcript, include_timestamps=include_timestamps)
        segments = list(transcript.segments)
        without_timestamps = sum(1 for item in segments if not item.has_timestamps)

        budget = max(500, self._settings.summary_chunk_max_tokens - max(0, reserved_tokens))
        total = estimate_tokens(text)
        if total <= budget or not segments:
            chunk = TranscriptChunk(
                index=0,
                text=text,
                segments=segments,
                token_estimate=total,
                start_seconds=self._first_start(segments),
                end_seconds=self._last_end(segments),
            )
            return PreparedTranscript(
                transcript_text=text,
                tokens=total,
                chunks=[chunk],
                segments_without_timestamps=without_timestamps,
            )

        chunks = self._chunk_segments(segments, budget, include_timestamps=include_timestamps)
        return PreparedTranscript(
            transcript_text=text,
            tokens=total,
            chunks=chunks,
            segments_without_timestamps=without_timestamps,
        )

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _first_start(segments: list[TranscriptSegment]) -> float | None:
        starts = [item.start_seconds for item in segments if item.start_seconds is not None]
        return min(starts) if starts else None

    @staticmethod
    def _last_end(segments: list[TranscriptSegment]) -> float | None:
        ends = [item.end_seconds for item in segments if item.end_seconds is not None]
        if ends:
            return max(ends)
        starts = [item.start_seconds for item in segments if item.start_seconds is not None]
        return max(starts) if starts else None

    def _chunk_segments(
        self,
        segments: list[TranscriptSegment],
        budget: int,
        *,
        include_timestamps: bool,
    ) -> list[TranscriptChunk]:
        """Split segments on natural boundaries, keeping a small overlap."""
        overlap_budget = min(self._settings.summary_chunk_overlap_tokens, budget // 4)

        chunks: list[TranscriptChunk] = []
        current: list[TranscriptSegment] = []
        current_tokens = 0

        for segment in segments:
            rendered = render_segment(segment, include_timestamps=include_timestamps)
            cost = estimate_tokens(rendered)
            if current and current_tokens + cost > budget:
                chunks.append(self._build_chunk(len(chunks), current, include_timestamps))
                current = self._overlap_tail(current, overlap_budget, include_timestamps)
                current_tokens = sum(
                    estimate_tokens(render_segment(item, include_timestamps=include_timestamps))
                    for item in current
                )
            current.append(segment)
            current_tokens += cost

        if current:
            chunks.append(self._build_chunk(len(chunks), current, include_timestamps))
        logger.info("Split transcript into %s chunk(s).", len(chunks))
        return chunks

    @staticmethod
    def _overlap_tail(
        segments: list[TranscriptSegment], overlap_budget: int, include_timestamps: bool
    ) -> list[TranscriptSegment]:
        """Return the trailing segments worth repeating for context."""
        if overlap_budget <= 0:
            return []
        tail: list[TranscriptSegment] = []
        used = 0
        for segment in reversed(segments):
            cost = estimate_tokens(render_segment(segment, include_timestamps=include_timestamps))
            if used + cost > overlap_budget:
                break
            tail.append(segment)
            used += cost
        tail.reverse()
        return tail

    @staticmethod
    def _build_chunk(
        index: int, segments: list[TranscriptSegment], include_timestamps: bool
    ) -> TranscriptChunk:
        lines = [
            line
            for line in (
                render_segment(item, include_timestamps=include_timestamps) for item in segments
            )
            if line
        ]
        text = "\n".join(lines)
        return TranscriptChunk(
            index=index,
            text=text,
            segments=list(segments),
            token_estimate=estimate_tokens(text),
            start_seconds=TranscriptPreprocessor._first_start(segments),
            end_seconds=TranscriptPreprocessor._last_end(segments),
        )
