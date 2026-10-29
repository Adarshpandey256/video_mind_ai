"""Typed models for the Step 4 retrieval augmented chat.

These models are the contract between the retrieval pipeline, the Streamlit
interface and (later) Step 5 exports:

* :class:`RagChunk` - one indexed slice of a transcript or summary.
* :class:`RetrievedSource` - a chunk returned for a question, with its location.
* :class:`RagCitation` - a source the generated answer actually pointed at.
* :class:`RagAnswer` / :class:`RagResult` - a grounded answer and how the run ended.
* :class:`IndexInfo` / :class:`IndexResult` - the state of a video's vector index.

Timestamps are optional everywhere and are only ever copied from Step 2
transcript segments; the chat layer never invents a position in a recording.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.schemas import SourceType, utc_now
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_timestamp


class DocumentType(str, Enum):
    """What a stored chunk came from."""

    TRANSCRIPT = "transcript"
    SUMMARY = "summary"


class IndexStatus(str, Enum):
    """State of one video's vector index, as reported to the interface."""

    NOT_INDEXED = "not_indexed"
    READY = "ready"
    STALE = "stale"
    EMBEDDING_MISMATCH = "embedding_mismatch"
    UNAVAILABLE = "unavailable"


class IndexOutcome(str, Enum):
    """Why an indexing run ended the way it did."""

    SUCCESS = "success"
    REUSED = "reused"
    NO_TRANSCRIPT = "no_transcript"
    EMPTY_TRANSCRIPT = "empty_transcript"
    CHUNK_LIMIT_EXCEEDED = "chunk_limit_exceeded"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    STORE_UNAVAILABLE = "store_unavailable"
    STORE_FAILED = "store_failed"


class RagOutcome(str, Enum):
    """Why a question could not be answered, or ``success``."""

    SUCCESS = "success"
    INVALID_QUESTION = "invalid_question"
    NO_TRANSCRIPT = "no_transcript"
    NO_INDEX = "no_index"
    INDEX_UNAVAILABLE = "index_unavailable"
    MISSING_CREDENTIALS = "missing_credentials"
    PROVIDER_FAILED = "provider_failed"
    INVALID_OUTPUT = "invalid_output"
    NO_RESULTS = "no_results"


class RagChunk(BaseModel):
    """One slice of a source document, ready to be embedded and stored.

    ``chunk_id`` is deterministic (video + document type + position + text hash),
    so rebuilding an unchanged transcript cannot create uncontrolled duplicates.
    """

    model_config = ConfigDict(extra="ignore")

    video_id: str
    chunk_id: str
    chunk_index: int = Field(default=0, ge=0)
    document_type: DocumentType = DocumentType.TRANSCRIPT
    text: str
    text_hash: str = ""
    source_name: str | None = None
    source_type: SourceType = SourceType.UNKNOWN
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    segment_start_index: int | None = Field(default=None, ge=0)
    segment_end_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _interval_must_be_possible(self) -> "RagChunk":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must not be earlier than start_seconds")
        return self

    @property
    def has_timestamps(self) -> bool:
        """True when this chunk carries a usable source position."""
        return self.start_seconds is not None

    @property
    def time_label(self) -> str:
        """Human readable source position, honest when unknown."""
        if self.start_seconds is None:
            return UNAVAILABLE_TIMESTAMP
        if self.end_seconds is None or self.end_seconds <= self.start_seconds:
            return format_timestamp(self.start_seconds)
        return f"{format_timestamp(self.start_seconds)} - {format_timestamp(self.end_seconds)}"

    def metadata(self) -> dict[str, str | int | float | bool]:
        """ChromaDB-safe metadata.

        ChromaDB rejects ``None`` and nested values, so unknown timestamps are
        *omitted* rather than stored as ``0`` (which would look like a real
        position at the start of the recording).
        """
        values: dict[str, str | int | float | bool] = {
            "video_id": self.video_id,
            "chunk_id": self.chunk_id,
            "chunk_index": int(self.chunk_index),
            "document_type": self.document_type.value,
            "source_type": self.source_type.value,
            "text_hash": self.text_hash,
            "has_timestamps": self.has_timestamps,
        }
        if self.source_name:
            values["source_name"] = self.source_name
        if self.start_seconds is not None:
            values["start_seconds"] = float(self.start_seconds)
        if self.end_seconds is not None:
            values["end_seconds"] = float(self.end_seconds)
        if self.segment_start_index is not None:
            values["segment_start_index"] = int(self.segment_start_index)
        if self.segment_end_index is not None:
            values["segment_end_index"] = int(self.segment_end_index)
        return values


class RetrievedSource(BaseModel):
    """A transcript or summary chunk retrieved for one question."""

    model_config = ConfigDict(extra="ignore")

    source_ref: str = Field(description="Stable citation id shown to the model, e.g. 'S1'.")
    chunk_id: str
    video_id: str
    chunk_index: int = Field(default=0, ge=0)
    document_type: DocumentType = DocumentType.TRANSCRIPT
    text: str
    source_name: str | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    distance: float | None = Field(
        default=None, description="Raw Chroma distance (lower is closer)."
    )
    similarity: float | None = Field(
        default=None, description="Cosine similarity derived from the distance (1.0 = identical)."
    )

    @property
    def has_timestamps(self) -> bool:
        """True when the source has a real location in the recording."""
        return self.start_seconds is not None

    @property
    def time_label(self) -> str:
        """Human readable source position, honest when unknown."""
        if self.start_seconds is None:
            return UNAVAILABLE_TIMESTAMP
        if self.end_seconds is None or self.end_seconds <= self.start_seconds:
            return format_timestamp(self.start_seconds)
        return f"{format_timestamp(self.start_seconds)} - {format_timestamp(self.end_seconds)}"

    @property
    def label(self) -> str:
        """Title + location, used in the interface."""
        title = self.source_name or "Untitled recording"
        return f"{title} · {self.time_label}"

    def excerpt(self, limit: int = 320) -> str:
        """Short excerpt for the interface (never the whole chunk in a caption)."""
        text = (self.text or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"


class RagCitation(BaseModel):
    """A source that the generated answer actually cited."""

    source_ref: str
    chunk_id: str
    quote: str | None = Field(
        default=None, description="Verbatim excerpt the model supplied, checked against the chunk."
    )
    source_name: str | None = None
    document_type: DocumentType = DocumentType.TRANSCRIPT
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @property
    def time_label(self) -> str:
        """Human readable source position, honest when unknown."""
        if self.start_seconds is None:
            return UNAVAILABLE_TIMESTAMP
        if self.end_seconds is None or self.end_seconds <= self.start_seconds:
            return format_timestamp(self.start_seconds)
        return f"{format_timestamp(self.start_seconds)} - {format_timestamp(self.end_seconds)}"

    @classmethod
    def from_source(
        cls, source: RetrievedSource, *, quote: str | None = None
    ) -> "RagCitation":
        """Resolve a citation from a retrieved source (the only trusted metadata)."""
        return cls(
            source_ref=source.source_ref,
            chunk_id=source.chunk_id,
            quote=quote,
            source_name=source.source_name,
            document_type=source.document_type,
            start_seconds=source.start_seconds,
            end_seconds=source.end_seconds,
        )


class RagAnswer(BaseModel):
    """A grounded answer with resolved citations."""

    question: str
    answer: str
    citations: list[RagCitation] = Field(default_factory=list)
    sources: list[RetrievedSource] = Field(
        default_factory=list, description="Every retrieved passage, cited or not."
    )
    model: str | None = None
    retrieval_query: str = ""
    history_turns: int = Field(default=0, ge=0)
    insufficient_evidence: bool = False
    notes: list[str] = Field(
        default_factory=list,
        description="Honest notes about dropped citations or unusable model output.",
    )
    latency_ms: float | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def cited_sources(self) -> list[RetrievedSource]:
        """The retrieved passages that were actually cited, in citation order."""
        by_id = {source.chunk_id: source for source in self.sources}
        return [
            by_id[citation.chunk_id]
            for citation in self.citations
            if citation.chunk_id in by_id
        ]


class RagResult(BaseModel):
    """Outcome of one question, returned to the interface."""

    outcome: RagOutcome
    message: str
    answer: RagAnswer | None = None
    sources: list[RetrievedSource] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """True only when a grounded answer with validated citations exists."""
        return self.outcome is RagOutcome.SUCCESS and self.answer is not None


class ChatTurn(BaseModel):
    """One completed question/answer pair, kept in the video-scoped history."""

    question: str
    answer: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class IndexInfo(BaseModel):
    """What the application knows about one video's vector index."""

    video_id: str
    status: IndexStatus = IndexStatus.NOT_INDEXED
    transcript_id: str | None = None
    title: str | None = None
    source_name: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    transcript_chunks: int = Field(default=0, ge=0)
    summary_chunks: int = Field(default=0, ge=0)
    embedding_model: str | None = None
    transcript_hash: str | None = None
    collection_name: str | None = None
    indexed_at: datetime | None = None
    message: str | None = None

    @property
    def is_ready(self) -> bool:
        """True when questions can be answered from this index right now."""
        return self.status is IndexStatus.READY

    def describe(self) -> str:
        """One-line, honest description of the index state."""
        if self.status is IndexStatus.READY:
            return (
                f"{self.chunk_count} indexed chunk(s): {self.transcript_chunks} transcript, "
                f"{self.summary_chunks} summary."
            )
        if self.status is IndexStatus.STALE:
            return (
                "The transcript has changed since this index was built, so the index is out "
                "of date. Rebuild it to ask questions about the current transcript."
            )
        if self.status is IndexStatus.EMBEDDING_MISMATCH:
            return (
                "This index was built with a different embedding model "
                f"({self.embedding_model or 'unknown'}). Rebuild it with the configured model "
                "before asking questions."
            )
        if self.status is IndexStatus.UNAVAILABLE:
            return self.message or "The vector store could not be opened."
        return "This video has not been indexed yet."


class IndexResult(BaseModel):
    """Outcome of an indexing (or rebuild) request."""

    outcome: IndexOutcome
    message: str
    info: IndexInfo | None = None
    chunks_indexed: int = Field(default=0, ge=0)

    @property
    def succeeded(self) -> bool:
        """True when the index is ready as a result of this call."""
        return self.outcome in {IndexOutcome.SUCCESS, IndexOutcome.REUSED} and (
            self.info is None or self.info.is_ready
        )


class ChatOptions(BaseModel):
    """Per-question overrides; ``None`` means "use the configuration"."""

    model_config = ConfigDict(extra="ignore")

    top_k: int | None = Field(default=None, ge=1, le=20)
    include_summary: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=256, le=32768)
