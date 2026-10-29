"""Typed data models shared by the UI, the services and later processing steps."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class SourceType(str, Enum):
    """Where a piece of media came from."""

    URL = "url"
    LOCAL_VIDEO = "local_video"
    LOCAL_AUDIO = "local_audio"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    """Lifecycle of a media processing job."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class FeatureStatus(str, Enum):
    """Availability of a feature in the current development step."""

    AVAILABLE = "available"
    PLANNED = "planned"


class HealthStatus(str, Enum):
    """Outcome of a single diagnostic check."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    PENDING = "pending"


class VideoMetadata(BaseModel):
    """Description of a video/audio source (created in Step 2, defined here early)."""

    video_id: str = Field(description="Stable identifier used by later steps and retrieval.")
    source_type: SourceType = SourceType.UNKNOWN
    source_title: str | None = None
    source_reference: str | None = Field(
        default=None,
        description="Original filename or source URL - never a secret.",
    )
    duration_seconds: float | None = Field(default=None, ge=0)
    status: ProcessingStatus = ProcessingStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)


class TranscriptSegment(BaseModel):
    """One timestamped chunk of a transcript.

    Timestamps are optional on purpose: when a backend cannot supply them they are
    stored as ``None`` instead of inventing a value.
    """

    segment_index: int = Field(default=0, ge=0)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    text: str

    @model_validator(mode="after")
    def _timestamps_must_be_ordered(self) -> "TranscriptSegment":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must not be earlier than start_seconds")
        return self

    @property
    def has_timestamps(self) -> bool:
        """True when both ends of the segment are known."""
        return self.start_seconds is not None and self.end_seconds is not None


class TranscriptMetadata(BaseModel):
    """Metadata/index view of a transcript - never contains segments or full text."""

    transcript_id: str | None = None
    video_id: str
    source_type: SourceType = SourceType.UNKNOWN
    original_filename: str | None = None
    source_reference: str | None = Field(
        default=None,
        description="Source URL or original filename - never a secret or an internal path.",
    )
    title: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    language: str | None = None
    transcription_model: str | None = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    segment_count: int = Field(default=0, ge=0)
    text_reference: str | None = Field(
        default=None,
        description="Identifier of the stored transcript document instead of the text itself.",
    )
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class TranscriptRecord(TranscriptMetadata):
    """Full persisted transcript: metadata plus timestamped segments and full text."""

    segments: list[TranscriptSegment] = Field(default_factory=list)
    full_text: str = ""

    @property
    def has_timestamps(self) -> bool:
        """True when at least one segment carries timestamps."""
        return any(segment.has_timestamps for segment in self.segments)


# The Step 3 summary models live in src/models/summary_schemas.py so this module
# keeps describing the foundation and Step 2 only. Import them from there (or from
# src.models, which re-exports them).


class ChatSourceReference(BaseModel):
    """A transcript excerpt used to answer a question (Step 4)."""

    quote: str
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)


class ChatResponse(BaseModel):
    """Answer to a user question about a video (Step 4)."""

    question: str
    answer: str
    sources: list[ChatSourceReference] = Field(default_factory=list)
    model_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class LLMResponse(BaseModel):
    """Normalised result of a chat completion request."""

    text: str
    model: str
    finish_reason: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ConnectionTestResult(BaseModel):
    """Outcome of an explicit "Test NVIDIA Connection" request."""

    ok: bool
    message: str
    model: str | None = None
    base_url: str | None = None
    latency_ms: float | None = None
    response_preview: str | None = None
    error_type: str | None = Field(
        default=None,
        description="'configuration' when local setup is incomplete, 'request' on provider failure.",
    )
    tested_at: datetime = Field(default_factory=utc_now)


class HealthCheck(BaseModel):
    """One application diagnostic entry."""

    identifier: str
    label: str
    status: HealthStatus
    detail: str
    category: str = "foundation"
    stage: str | None = None


class IngestionKind(str, Enum):
    """How the media for a job was obtained."""

    UPLOAD = "upload"
    URL = "url"


class ValidatedSource(BaseModel):
    """A media source that passed input validation and is ready for processing."""

    kind: IngestionKind
    source_type: SourceType = SourceType.UNKNOWN
    original_filename: str | None = None
    title: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    upload_path: str | None = Field(
        default=None,
        description="Internal path of a stored upload - never shown to the user.",
    )
    url: str | None = None
    download_strategy: str | None = Field(
        default=None, description="'direct' or 'platform' for URL sources."
    )

    @property
    def reference(self) -> str:
        """Human readable reference of the source."""
        return self.url or self.original_filename or "unknown source"


class MediaInfo(BaseModel):
    """Result of inspecting a media file with ffprobe."""

    name: str | None = Field(default=None, description="Basename only, never a full path.")
    duration_seconds: float | None = Field(default=None, ge=0)
    container_format: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    has_audio: bool = False
    has_video: bool = False
    audio_codec: str | None = None
    sample_rate_hz: int | None = Field(default=None, ge=0)
    channels: int | None = Field(default=None, ge=0)


class TranscriptionOptions(BaseModel):
    """Per-request transcription overrides; ``None`` means "use the configuration"."""

    model_name: str | None = None
    device: str | None = None
    language: str | None = None


class TranscriptionResult(BaseModel):
    """Validated output of the transcription service."""

    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    language: str | None = None
    model_name: str
    device: str = "cpu"
    duration_seconds: float | None = Field(default=None, ge=0)
    processing_seconds: float | None = Field(default=None, ge=0)


class PipelineOutcome(str, Enum):
    """Why a processing job ended the way it did."""

    SUCCESS = "success"
    INVALID_INPUT = "invalid_input"
    TOOLING_MISSING = "tooling_missing"
    DOWNLOAD_FAILED = "download_failed"
    MEDIA_INSPECTION_FAILED = "media_inspection_failed"
    AUDIO_EXTRACTION_FAILED = "audio_extraction_failed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    TRANSCRIPT_VALIDATION_FAILED = "transcript_validation_failed"
    STORAGE_FAILED = "storage_failed"


class StageStatus(str, Enum):
    """Result of one processing stage."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStageReport(BaseModel):
    """One stage of a processing job, reported honestly to the interface."""

    stage: str
    status: StageStatus
    detail: str | None = None


class PipelineResult(BaseModel):
    """Structured result of a processing job for the UI and later steps."""

    outcome: PipelineOutcome
    message: str
    stages: list[PipelineStageReport] = Field(default_factory=list)
    transcript: TranscriptRecord | None = None
    failed_stage: str | None = None

    @property
    def succeeded(self) -> bool:
        """True only when a validated transcript was stored."""
        return self.outcome is PipelineOutcome.SUCCESS and self.transcript is not None


class NavPage(BaseModel):
    """A sidebar navigation entry."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    icon: str
    description: str
    status: FeatureStatus = FeatureStatus.PLANNED


class RoadmapStage(BaseModel):
    """One development stage of the five-step roadmap."""

    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    summary: str
    status: FeatureStatus = FeatureStatus.PLANNED
    features: tuple[str, ...] = ()


NAV_PAGES: tuple[NavPage, ...] = (
    NavPage(
        key="home",
        label="Home",
        icon="🏠",
        description="Overview, media input and processing status.",
        status=FeatureStatus.AVAILABLE,
    ),
    NavPage(
        key="summarization",
        label="Summarization",
        icon="📝",
        description="NVIDIA Nemotron summaries, takeaways, topics and action items.",
        status=FeatureStatus.AVAILABLE,
    ),
    NavPage(
        key="chat",
        label="Ask Your Video",
        icon="💬",
        description="Ask questions about a video using retrieval augmented answers.",
        status=FeatureStatus.AVAILABLE,
    ),
    NavPage(
        key="reports",
        label="Reports",
        icon="📄",
        description="Build and download reports as PDF, DOCX, TXT, Markdown, JSON, HTML, CSV, Excel or PowerPoint.",
        status=FeatureStatus.AVAILABLE,
    ),
    NavPage(
        key="settings",
        label="Settings",
        icon="⚙️",
        description="Provider configuration, diagnostics and health checks.",
        status=FeatureStatus.AVAILABLE,
    ),
)

DEFAULT_PAGE = "home"

ROADMAP_STAGES: tuple[RoadmapStage, ...] = (
    RoadmapStage(
        number=1,
        title="Foundation and NVIDIA integration",
        summary="Secure configuration, application shell, NVIDIA Nemotron client, health checks and tests.",
        status=FeatureStatus.AVAILABLE,
        features=(
            "Streamlit application shell with navigation",
            "Central validated configuration",
            "NVIDIA client and LLM service",
            "Explicit connection test",
            "Health checks, logging and automated tests",
        ),
    ),
    RoadmapStage(
        number=2,
        title="Media ingestion and transcription",
        summary="Video/audio upload, safe URL acquisition, FFmpeg extraction and Whisper speech-to-text.",
        status=FeatureStatus.AVAILABLE,
        features=(
            "Local video and audio uploads with validation",
            "Direct media and supported public platform URLs",
            "FFmpeg audio extraction and normalisation",
            "Whisper transcription with timestamped segments",
            "Structured transcripts stored as JSON",
        ),
    ),
    RoadmapStage(
        number=3,
        title="Summarisation and insights",
        summary=(
            "NVIDIA Nemotron summaries, key takeaways, topics, chapters and meeting action "
            "items, with bounded processing for long recordings."
        ),
        status=FeatureStatus.AVAILABLE,
        features=(
            "Eight summary modes including meeting notes and chapters",
            "Timestamped takeaways validated against the transcript",
            "Chunked processing and merging for long recordings",
            "Structured summaries stored as JSON for chat and exports",
        ),
    ),
    RoadmapStage(
        number=4,
        title="RAG chat over transcripts",
        summary="Embeddings, ChromaDB retrieval and LangChain orchestration.",
        status=FeatureStatus.AVAILABLE,
        features=(
            "Local embeddings for timestamped transcript chunks",
            "Persistent ChromaDB index isolated per video",
            "Grounded answers with validated citations and timestamps",
            "Video-scoped follow-up questions and a clear-chat action",
        ),
    ),
    RoadmapStage(
        number=5,
        title="Reports and end-to-end flow",
        summary=(
            "Section-by-section report assembly and multi-format export from the stored "
            "transcript, summary and chatbot evidence."
        ),
        status=FeatureStatus.AVAILABLE,
        features=(
            "PDF, DOCX, TXT, Markdown, JSON, HTML and PowerPoint exports",
            "Transcript, chat and source CSV exports plus an Excel workbook",
            "Chosen sections, safe filenames and honest missing-data reporting",
            "Exports reuse stored data without re-running AI processing",
        ),
    ),
)
