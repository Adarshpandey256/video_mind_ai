"""Typed models for NVIDIA-powered summarisation (Step 3).

These models are the contract between the summarisation service, the Streamlit
interface and the later stages (Step 4 retrieval, Step 5 export):

* :class:`SummaryOptions` - what the user asked for.
* :class:`StructuredSummary` - the validated result, persisted as-is.
* :class:`SummaryMetadata` - the lightweight index entry (no long text).
* :class:`SummaryResult` - the service outcome, including honest failure states.

Timestamps are optional everywhere: when a timestamp cannot be validated against
the source transcript it is dropped instead of being displayed as a false
location.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.schemas import SourceType, StageStatus, utc_now


class SummaryMode(str, Enum):
    """The summary styles the application offers."""

    QUICK = "quick"
    DETAILED = "detailed"
    BULLETS = "bullets"
    TAKEAWAYS = "takeaways"
    TOPICS = "topics"
    MEETING = "meeting"
    CHAPTERS = "chapters"
    CUSTOM = "custom"


#: Every mode value, in the order the interface offers them.
SUMMARY_MODE_CHOICES: tuple[str, ...] = tuple(mode.value for mode in SummaryMode)
DEFAULT_SUMMARY_MODE = SummaryMode.QUICK.value

#: Human readable labels for the interface.
SUMMARY_MODE_LABELS: dict[str, str] = {
    SummaryMode.QUICK.value: "Quick summary",
    SummaryMode.DETAILED.value: "Detailed summary",
    SummaryMode.BULLETS.value: "Bullet-point summary",
    SummaryMode.TAKEAWAYS.value: "Key takeaways",
    SummaryMode.TOPICS.value: "Topic breakdown",
    SummaryMode.MEETING.value: "Meeting summary",
    SummaryMode.CHAPTERS.value: "Chapter-style summary",
    SummaryMode.CUSTOM.value: "Custom summary",
}

#: Short explanation of what each mode is for, shown next to the selector.
SUMMARY_MODE_DESCRIPTIONS: dict[str, str] = {
    SummaryMode.QUICK.value: "The main idea and the most important conclusion, in a few lines.",
    SummaryMode.DETAILED.value: "Context, concepts, supporting facts and conclusions in depth.",
    SummaryMode.BULLETS.value: "A readable structure of headings and hierarchical bullets.",
    SummaryMode.TAKEAWAYS.value: "The highest-value insights, each with a source location.",
    SummaryMode.TOPICS.value: "The major themes and concepts, with their key points.",
    SummaryMode.MEETING.value: "Discussion topics, decisions, action items, owners and follow-ups.",
    SummaryMode.CHAPTERS.value: "A chapter breakdown with source time boundaries.",
    SummaryMode.CUSTOM.value: "Your own instruction, applied to the transcript.",
}


class SummaryLength(str, Enum):
    """How much detail the model should produce."""

    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"


SUMMARY_LENGTH_CHOICES: tuple[str, ...] = tuple(item.value for item in SummaryLength)
SUMMARY_LENGTH_LABELS: dict[str, str] = {
    SummaryLength.BRIEF.value: "Brief - the essentials only",
    SummaryLength.STANDARD.value: "Standard - balanced detail",
    SummaryLength.DETAILED.value: "Detailed - thorough coverage",
}
DEFAULT_SUMMARY_LENGTH = SummaryLength.STANDARD.value


class SummaryStatus(str, Enum):
    """Lifecycle of a summary document."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SummaryOutcome(str, Enum):
    """Why a summarisation run ended the way it did.

    The interface and later steps must branch on this instead of assuming that a
    returned object means success.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    INVALID_TRANSCRIPT = "invalid_transcript"
    TRANSCRIPT_TOO_LONG = "transcript_too_long"
    MISSING_CREDENTIALS = "missing_credentials"
    PROVIDER_FAILED = "provider_failed"
    VALIDATION_FAILED = "validation_failed"
    STORAGE_FAILED = "storage_failed"


class TimestampedItem(BaseModel):
    """An item that may reference a validated location in the source media.

    ``start_seconds``/``end_seconds`` are only ever populated from timestamps that
    were checked against the transcript. An impossible interval is rejected here
    as a last line of defence; the timestamp service removes or repairs bad values
    before a model is built.
    """

    model_config = ConfigDict(extra="ignore")

    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _interval_must_be_possible(self) -> "TimestampedItem":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("end_seconds must not be earlier than start_seconds")
        return self

    @property
    def has_timestamps(self) -> bool:
        """True when a validated start time is available."""
        return self.start_seconds is not None


class KeyTakeaway(TimestampedItem):
    """One high-value insight from the recording."""

    title: str
    explanation: str = ""
    excerpt: str | None = Field(
        default=None, description="Short quote from the transcript that supports the takeaway."
    )


class Topic(TimestampedItem):
    """A major theme discussed in the recording."""

    title: str
    explanation: str = ""
    key_points: list[str] = Field(default_factory=list)


class Chapter(TimestampedItem):
    """A logical section of the recording."""

    title: str
    description: str = ""
    major_points: list[str] = Field(default_factory=list)


class ActionItem(TimestampedItem):
    """A task mentioned in the recording.

    ``owner`` and ``deadline`` are only kept when the transcript states them; the
    application never invents either.
    """

    task: str
    owner: str | None = None
    deadline: str | None = None
    status: str | None = Field(
        default=None, description="Certainty as stated in the source, e.g. 'agreed'."
    )


class Decision(TimestampedItem):
    """An explicit decision recorded in the source."""

    decision: str
    context: str | None = None


class ImportantPoint(TimestampedItem):
    """A factual or explanatory point grounded in the transcript."""

    point: str
    explanation: str | None = None


class SummaryCoverage(BaseModel):
    """How much of the transcript the run actually processed.

    This is a *coverage* indicator, not a quality or accuracy score: it only says
    which chunks of the source reached the model successfully.
    """

    chunks_total: int = Field(default=0, ge=0)
    chunks_completed: int = Field(default=0, ge=0)
    chunks_failed: int = Field(default=0, ge=0)
    failed_chunk_indexes: list[int] = Field(default_factory=list)
    source_segments: int = Field(default=0, ge=0)
    segments_in_failed_chunks: int = Field(default=0, ge=0)

    @property
    def is_partial(self) -> bool:
        """True when at least one chunk of the source could not be summarised."""
        return self.chunks_failed > 0

    @property
    def ratio(self) -> float:
        """Fraction of chunks processed successfully (1.0 for single-pass runs)."""
        if self.chunks_total <= 0:
            return 1.0
        return self.chunks_completed / self.chunks_total

    def describe(self) -> str:
        """One-line, honest description of the processed portion."""
        if self.chunks_total <= 1:
            return "The whole transcript was processed in a single request."
        percent = round(self.ratio * 100)
        if not self.is_partial:
            return (
                f"All {self.chunks_total} transcript chunks were processed "
                f"in {self.chunks_total} requests."
            )
        return (
            f"{self.chunks_completed} of {self.chunks_total} transcript chunks were processed "
            f"({percent}% of the source). Chunks {self.failed_chunk_indexes} could not be "
            "summarised, so this summary does not cover the complete recording."
        )


class ChunkSummary(BaseModel):
    """Intermediate summary of one transcript chunk.

    This is never shown to the user as a result: it is the input to the merging
    stage, and it keeps its source interval so the merged summary can still be
    traced back to the recording.
    """

    model_config = ConfigDict(extra="ignore")

    index: int = Field(default=0, ge=0)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    segment_count: int = Field(default=0, ge=0)
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    takeaways: list[KeyTakeaway] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when the chunk produced nothing usable at all."""
        return not any(
            [
                self.summary.strip(),
                self.key_points,
                self.takeaways,
                self.topics,
                self.action_items,
                self.decisions,
            ]
        )

    def render_for_merge(self) -> str:
        """Render this chunk summary as a labelled block for the merging prompt."""
        from src.utils.time_utils import format_timestamp

        span = "position unavailable"
        if self.start_seconds is not None:
            end = self.end_seconds if self.end_seconds is not None else self.start_seconds
            span = f"{format_timestamp(self.start_seconds)} - {format_timestamp(end)}"

        lines = [f"### Section {self.index + 1} ({span})", self.summary.strip()]
        if self.key_points:
            lines.append("Key points:")
            lines.extend(f"- {point}" for point in self.key_points)
        if self.takeaways:
            lines.append("Takeaways:")
            for item in self.takeaways:
                stamp = (
                    f" @{format_timestamp(item.start_seconds)}"
                    if item.start_seconds is not None
                    else ""
                )
                lines.append(f"- {item.title}{stamp}: {item.explanation}".rstrip(": "))
        if self.topics:
            lines.append("Topics:")
            for topic in self.topics:
                stamp = (
                    f" @{format_timestamp(topic.start_seconds)}"
                    if topic.start_seconds is not None
                    else ""
                )
                lines.append(f"- {topic.title}{stamp}: {topic.explanation}".rstrip(": "))
        if self.action_items:
            lines.append("Action items as stated:")
            for item in self.action_items:
                details = [part for part in (item.owner, item.deadline) if part]
                suffix = f" ({', '.join(details)})" if details else ""
                lines.append(f"- {item.task}{suffix}")
        if self.decisions:
            lines.append("Decisions as stated:")
            lines.extend(f"- {item.decision}" for item in self.decisions)
        if self.limitations:
            lines.append("Unclear or missing in this section:")
            lines.extend(f"- {item}" for item in self.limitations)
        return "\n".join(line for line in lines if line is not None)


class SummaryOptions(BaseModel):
    """User-selected summarisation preferences for one run."""

    model_config = ConfigDict(extra="ignore")

    mode: SummaryMode = SummaryMode(DEFAULT_SUMMARY_MODE)
    length: SummaryLength = SummaryLength(DEFAULT_SUMMARY_LENGTH)
    language: str | None = Field(default=None, description="Preferred output language, or None.")
    include_timestamps: bool = True
    extract_action_items: bool = True
    custom_instruction: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=128, le=32768)

    @model_validator(mode="after")
    def _custom_mode_needs_an_instruction(self) -> "SummaryOptions":
        instruction = (self.custom_instruction or "").strip()
        if self.mode is SummaryMode.CUSTOM and not instruction:
            raise ValueError("A custom summary needs an instruction to follow.")
        if instruction:
            self.custom_instruction = instruction
        return self

    @property
    def fingerprint(self) -> str:
        """Stable description of the settings that produced a summary.

        Used to decide whether an existing summary can be reused: a changed mode,
        length, language or instruction must not silently reuse another result.
        """
        parts = [
            self.mode.value,
            self.length.value,
            (self.language or "auto").lower(),
            "ts" if self.include_timestamps else "nots",
            "actions" if self.extract_action_items else "noactions",
            (self.custom_instruction or "").strip().lower(),
        ]
        return "|".join(parts)


class SummaryStageReport(BaseModel):
    """One stage of a summarisation run, reported honestly to the interface."""

    stage: str
    status: StageStatus
    detail: str | None = None


class StructuredSummary(BaseModel):
    """A validated summary document."""

    model_config = ConfigDict(extra="ignore")

    summary_id: str | None = None
    source_id: str = Field(description="Step 2 video_id, stable per source.")
    source_transcript_id: str | None = None
    source_title: str | None = None
    source_type: SourceType = SourceType.UNKNOWN
    summary_type: SummaryMode = SummaryMode.QUICK
    options_fingerprint: str = ""
    overall_summary: str = ""
    short_summary: str | None = None
    key_takeaways: list[KeyTakeaway] = Field(default_factory=list)
    main_topics: list[Topic] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    important_points: list[ImportantPoint] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=list, description="What the model or the pipeline could not cover."
    )
    language: str | None = None
    generation_model: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    source_transcript_reference: str | None = None
    source_duration_seconds: float | None = Field(default=None, ge=0)
    processing_status: SummaryStatus = SummaryStatus.COMPLETED
    coverage: SummaryCoverage = Field(default_factory=SummaryCoverage)
    model_calls: int = Field(default=0, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    used_chunking: bool = False

    @property
    def is_partial(self) -> bool:
        """True when part of the source was not summarised."""
        return self.processing_status is SummaryStatus.PARTIAL or self.coverage.is_partial

    def all_points(self) -> list[str]:
        """Every textual point, used for duplicate detection and export."""
        values: list[str] = [self.overall_summary, self.short_summary or ""]
        values.extend(item.title for item in self.key_takeaways)
        values.extend(item.title for item in self.main_topics)
        values.extend(chapter.title for chapter in self.chapters)
        values.extend(item.task for item in self.action_items)
        values.extend(item.decision for item in self.decisions)
        values.extend(point.point for point in self.important_points)
        return [value for value in values if value]


class SummaryMetadata(BaseModel):
    """Lightweight index entry for a stored summary (no long text)."""

    model_config = ConfigDict(extra="ignore")

    summary_id: str | None = None
    transcript_id: str | None = None
    source_id: str
    source_title: str | None = None
    source_type: SourceType = SourceType.UNKNOWN
    summary_type: SummaryMode = SummaryMode.QUICK
    generation_model: str | None = None
    processing_status: SummaryStatus = SummaryStatus.COMPLETED
    coverage: SummaryCoverage = Field(default_factory=SummaryCoverage)
    preview: str | None = Field(
        default=None, description="Short excerpt for the interface, never the whole summary."
    )
    created_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_summary(cls, summary: StructuredSummary, *, preview_chars: int = 160) -> "SummaryMetadata":
        """Build an index entry from a full summary document."""
        preview_source = (summary.short_summary or summary.overall_summary or "").strip()
        preview = preview_source[:preview_chars] or None
        return cls(
            summary_id=summary.summary_id,
            transcript_id=summary.source_transcript_id,
            source_id=summary.source_id,
            source_title=summary.source_title,
            source_type=summary.source_type,
            summary_type=summary.summary_type,
            generation_model=summary.generation_model,
            processing_status=summary.processing_status,
            coverage=summary.coverage,
            preview=preview,
            created_at=summary.generated_at,
        )


class SummaryResult(BaseModel):
    """Outcome of a summarisation run, returned to the interface."""

    outcome: SummaryOutcome
    message: str
    summary: StructuredSummary | None = None
    stages: list[SummaryStageReport] = Field(default_factory=list)
    failed_stage: str | None = None

    @property
    def succeeded(self) -> bool:
        """True when a validated summary exists, even if storing it failed.

        The ``outcome`` always describes how the run ended, so a caller that cares
        about persistence checks :attr:`outcome` rather than this flag.
        """
        return self.summary is not None

    @property
    def is_partial(self) -> bool:
        """True when a validated but incomplete summary was produced."""
        return self.outcome is SummaryOutcome.PARTIAL or bool(
            self.summary and self.summary.is_partial
        )
