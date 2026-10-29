"""Typed report model shared by every Step 5 exporter.

One canonical representation, built once from the real Step 2/3/4 data and then
rendered by each exporter. Keeping it separate from rendering means PDF, DOCX,
JSON and CSV always describe the same content; a format only changes *how* the
same report is presented.

Honesty rules encoded in the model:

* timestamps are optional and copied from real transcript segments;
* a section that the user selected but that has no data is listed in
  ``omitted_sections`` with a reason instead of being filled with placeholder
  text;
* chatbot citations keep their provenance (transcript or summary passage) and the
  timestamp label produced by the RAG pipeline - the exporter never invents one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.schemas import SourceType, utc_now
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_timestamp

#: Version of the report document schema. Bump only for a breaking change; the
#: JSON export writes it so a future reader can tell documents apart.
REPORT_SCHEMA_VERSION = "1.0"


class ReportSection(str, Enum):
    """The content blocks a report can contain."""

    METADATA = "metadata"
    SUMMARY = "summary"
    KEY_TAKEAWAYS = "key_takeaways"
    TOPICS = "topics"
    CHAPTERS = "chapters"
    ACTION_ITEMS = "action_items"
    TRANSCRIPT = "transcript"
    CHAT_QA = "chat_qa"
    SOURCES = "sources"
    PROCESSING = "processing"


REPORT_SECTION_LABELS: dict[ReportSection, str] = {
    ReportSection.METADATA: "Video information",
    ReportSection.SUMMARY: "AI summary",
    ReportSection.KEY_TAKEAWAYS: "Key takeaways",
    ReportSection.TOPICS: "Topic breakdown",
    ReportSection.CHAPTERS: "Chapter breakdown",
    ReportSection.ACTION_ITEMS: "Action items and decisions",
    ReportSection.TRANSCRIPT: "Transcript",
    ReportSection.CHAT_QA: "Chatbot questions and answers",
    ReportSection.SOURCES: "Sources and citations",
    ReportSection.PROCESSING: "Processing information",
}

#: Section order used by every renderer, so exports read consistently.
REPORT_SECTION_ORDER: tuple[ReportSection, ...] = tuple(ReportSection)

#: A useful default that does not automatically embed a potentially huge transcript.
DEFAULT_REPORT_SECTIONS: tuple[ReportSection, ...] = (
    ReportSection.METADATA,
    ReportSection.SUMMARY,
    ReportSection.KEY_TAKEAWAYS,
    ReportSection.TOPICS,
    ReportSection.SOURCES,
    ReportSection.PROCESSING,
)

#: Where a cited passage came from. ``unknown`` is used for chat history recorded
#: before the citation payload included its document type - never guessed.
SourceKind = Literal["transcript", "summary", "unknown"]

SOURCE_KIND_LABELS: dict[str, str] = {
    "transcript": "transcript",
    "summary": "AI summary",
    "unknown": "source type not recorded",
}


def new_report_id() -> str:
    """Return a unique, filesystem-safe report identifier."""
    return f"rpt-{utc_now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def time_span(start: float | None, end: float | None) -> str:
    """Format a real segment span, or report that no timestamp is available."""
    if start is None:
        return UNAVAILABLE_TIMESTAMP
    if end is None or end <= start:
        return format_timestamp(start)
    return f"{format_timestamp(start)} - {format_timestamp(end)}"


class ReportSectionChoice(BaseModel):
    """One section the user asked for, and whether it could be included."""

    model_config = ConfigDict(extra="ignore")

    section: ReportSection
    included: bool = False
    reason: str | None = Field(
        default=None, description="Why the section is missing, when it is not included."
    )


class ReportOptions(BaseModel):
    """Everything the user chose on the export screen."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=200)
    organisation: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    sections: list[ReportSection] = Field(min_length=1)
    include_timestamps: bool = True

    @field_validator("author", "organisation", "notes", mode="before")
    @classmethod
    def _clean_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = " ".join(str(value).split())
        return text or None

    @field_validator("title", mode="before")
    @classmethod
    def _clean_title(cls, value: object) -> object:
        text = " ".join(str(value or "").split())
        if not text:
            raise ValueError("A report title is required.")
        return text

    @field_validator("sections")
    @classmethod
    def _dedupe_sections(cls, value: list[ReportSection]) -> list[ReportSection]:
        seen: list[ReportSection] = []
        for section in value:
            if section not in seen:
                seen.append(section)
        if not seen:
            raise ValueError("Select at least one report section.")
        return seen


class ReportVideo(BaseModel):
    """Metadata of the recording the report describes."""

    model_config = ConfigDict(extra="ignore")

    video_id: str
    transcript_id: str | None = None
    title: str
    original_filename: str | None = None
    source_type: SourceType = SourceType.UNKNOWN
    source_url: str | None = Field(
        default=None, description="Display-safe http(s) URL, credentials removed."
    )
    duration_seconds: float | None = Field(default=None, ge=0)
    language: str | None = None
    transcription_model: str | None = None
    processing_status: str | None = None
    created_at: datetime | None = None
    segment_count: int = Field(default=0, ge=0)
    has_timestamps: bool = False

    @property
    def duration_label(self) -> str:
        """Human readable duration, or an honest 'unknown'."""
        if self.duration_seconds is None:
            return "unknown"
        total = int(round(self.duration_seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours} h {minutes:02d} min"
        if minutes:
            return f"{minutes} min {seconds:02d} s"
        return f"{seconds} s"


class ReportSummary(BaseModel):
    """The stored Step 3 summary, as included in the report."""

    model_config = ConfigDict(extra="ignore")

    summary_id: str | None = None
    style: str
    heading: str | None = None
    text: str = ""
    short_text: str | None = None
    language: str | None = None
    model: str | None = None
    generated_at: datetime | None = None
    processing_status: str | None = None
    partial_note: str | None = Field(
        default=None, description="Explains when the summary does not cover everything."
    )

    @property
    def has_text(self) -> bool:
        """True when there is prose to show."""
        return bool((self.text or "").strip() or (self.short_text or "").strip())


class ReportTakeaway(BaseModel):
    """One key takeaway with its real source location when available."""

    model_config = ConfigDict(extra="ignore")

    title: str
    explanation: str = ""
    excerpt: str | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @property
    def time_label(self) -> str:
        """Formatted source span, or the honest unavailable label."""
        return time_span(self.start_seconds, self.end_seconds)


class ReportTopic(BaseModel):
    """One topic and the points the summary attributed to it."""

    model_config = ConfigDict(extra="ignore")

    title: str
    explanation: str = ""
    key_points: list[str] = Field(default_factory=list)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @property
    def time_label(self) -> str:
        """Formatted source span, or the honest unavailable label."""
        return time_span(self.start_seconds, self.end_seconds)


class ReportChapter(BaseModel):
    """One chapter-style section of the recording."""

    model_config = ConfigDict(extra="ignore")

    title: str
    description: str = ""
    major_points: list[str] = Field(default_factory=list)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @property
    def time_label(self) -> str:
        """Formatted source span, or the honest unavailable label."""
        return time_span(self.start_seconds, self.end_seconds)


class ReportActionItem(BaseModel):
    """A task or decision stated in the recording."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["action", "decision"] = "action"
    text: str
    owner: str | None = None
    deadline: str | None = None
    context: str | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    @property
    def time_label(self) -> str:
        """Formatted source span, or the honest unavailable label."""
        return time_span(self.start_seconds, self.end_seconds)


class ReportTranscriptEntry(BaseModel):
    """One transcript segment (or the whole plain-text transcript as one entry)."""

    model_config = ConfigDict(extra="ignore")

    segment_id: str
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    text: str

    @property
    def time_label(self) -> str:
        """Formatted source span, or the honest unavailable label."""
        return time_span(self.start_seconds, self.end_seconds)


class ReportCitation(BaseModel):
    """A citation shown with a chatbot answer, validated by the RAG pipeline."""

    model_config = ConfigDict(extra="ignore")

    source_ref: str
    source_name: str | None = None
    source_kind: SourceKind = "unknown"
    time_label: str = UNAVAILABLE_TIMESTAMP
    quote: str | None = None
    chunk_id: str | None = None

    @property
    def kind_label(self) -> str:
        """Readable provenance label (transcript vs summary-derived)."""
        return SOURCE_KIND_LABELS.get(self.source_kind, SOURCE_KIND_LABELS["unknown"])


class ReportChatEntry(BaseModel):
    """One question and its grounded answer."""

    model_config = ConfigDict(extra="ignore")

    question: str
    answer: str
    citations: list[ReportCitation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    sources_available: bool = False


class ReportSource(BaseModel):
    """A passage the chatbot retrieved, with its real location when known."""

    model_config = ConfigDict(extra="ignore")

    source_ref: str
    label: str
    source_kind: SourceKind = "unknown"
    time_label: str = UNAVAILABLE_TIMESTAMP
    excerpt: str
    chunk_id: str | None = None

    @property
    def kind_label(self) -> str:
        """Readable provenance label (transcript vs summary-derived)."""
        return SOURCE_KIND_LABELS.get(self.source_kind, SOURCE_KIND_LABELS["unknown"])


class ReportProcessing(BaseModel):
    """Where the report content came from, for the reader."""

    model_config = ConfigDict(extra="ignore")

    app_name: str = "VideoMind AI"
    app_version: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    transcript_model: str | None = None
    transcript_language: str | None = None
    transcript_segments: int = Field(default=0, ge=0)
    summary_model: str | None = None
    summary_style: str | None = None
    chat_answers: int = Field(default=0, ge=0)
    timestamps_available: bool = False
    notes: list[str] = Field(
        default_factory=list, description="Honest notes about the source data."
    )


class VideoMindReport(BaseModel):
    """The canonical report that every exporter renders."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = REPORT_SCHEMA_VERSION
    report_id: str = Field(default_factory=new_report_id)
    generated_at: datetime = Field(default_factory=utc_now)
    title: str
    author: str | None = None
    organisation: str | None = None
    notes: str | None = None

    video: ReportVideo
    sections: list[ReportSection] = Field(default_factory=list)
    omitted_sections: list[ReportSectionChoice] = Field(default_factory=list)

    summary: ReportSummary | None = None
    key_takeaways: list[ReportTakeaway] = Field(default_factory=list)
    topics: list[ReportTopic] = Field(default_factory=list)
    chapters: list[ReportChapter] = Field(default_factory=list)
    action_items: list[ReportActionItem] = Field(default_factory=list)

    transcript: list[ReportTranscriptEntry] = Field(default_factory=list)
    transcript_available: bool = False
    timestamps_available: bool = False
    include_timestamps: bool = Field(
        default=True, description="User choice: print transcript timestamps in the report."
    )

    chat: list[ReportChatEntry] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)

    processing: ReportProcessing = Field(default_factory=ReportProcessing)

    def includes(self, section: ReportSection) -> bool:
        """True when the section was selected and actually populated."""
        return section in self.sections

    def omitted_reason(self, section: ReportSection) -> str | None:
        """Reason a selected section was omitted, if it was."""
        for entry in self.omitted_sections:
            if entry.section is section:
                return entry.reason
        return None

    @property
    def shows_timestamps(self) -> bool:
        """True when the report should print transcript timestamps."""
        return self.timestamps_available and self.include_timestamps

    @property
    def transcript_characters(self) -> int:
        """Total characters of the included transcript."""
        return sum(len(entry.text) for entry in self.transcript)

    @property
    def chat_answers(self) -> int:
        """Number of question/answer pairs included."""
        return len(self.chat)


class ReportBuildError(RuntimeError):
    """Raised when a report cannot be built from the available data."""
