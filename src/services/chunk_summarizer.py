"""Summarise one transcript chunk into an intermediate, traceable result (Step 3).

Chunk summaries deliberately stay compact: if they are allowed to grow, the merging
stage recreates exactly the context problem chunking was introduced to solve. Each
one keeps the source interval and the segment count of the chunk it came from, so
the merged summary can still be traced back to a position in the recording.
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import Settings, get_settings
from src.models.summary_schemas import ChunkSummary, SummaryOptions
from src.prompts.summary_prompts import build_chunk_messages
from src.services.summary_generation import SummaryGenerationError, SummaryJsonClient
from src.services.summary_validation import (
    build_action_items,
    build_decisions,
    build_takeaways,
    build_topics,
)
from src.services.timestamp_service import TimestampValidator
from src.services.transcript_preprocessor import TranscriptChunk
from src.utils.json_utils import coerce_str_list, coerce_text

logger = logging.getLogger(__name__)

__all__ = ["ChunkSummarizer", "build_chunk_summary", "SummaryGenerationError"]


def build_chunk_summary(
    data: dict[str, Any],
    *,
    index: int,
    start_seconds: float | None,
    end_seconds: float | None,
    segment_count: int,
    options: SummaryOptions,
    validator: TimestampValidator,
    source_text: str = "",
) -> ChunkSummary:
    """Validate raw chunk JSON into a :class:`ChunkSummary`."""
    actions, _ = build_action_items(
        data.get("action_items"), validator, source_text=source_text
    )
    return ChunkSummary(
        index=index,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        segment_count=segment_count,
        summary=coerce_text(data.get("summary") or data.get("overall_summary")),
        key_points=coerce_str_list(data.get("key_points") or data.get("points")),
        takeaways=build_takeaways(
            data.get("takeaways") or data.get("key_takeaways"),
            validator,
            source_text=source_text,
        ),
        topics=build_topics(data.get("topics") or data.get("main_topics"), validator),
        action_items=actions,
        decisions=build_decisions(data.get("decisions"), validator),
        limitations=coerce_str_list(data.get("limitations") or data.get("unclear")),
    )


class ChunkSummarizer:
    """Summarise transcript chunks through the configured NVIDIA model."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: SummaryJsonClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or SummaryJsonClient(self._settings)

    @property
    def settings(self) -> Settings:
        """Return the settings that control chunk requests."""
        return self._settings

    def summarize_chunk(
        self,
        chunk: TranscriptChunk,
        options: SummaryOptions,
        validator: TimestampValidator,
        *,
        chunk_total: int,
    ) -> tuple[ChunkSummary, int]:
        """Summarise one chunk; returns ``(chunk_summary, model_calls)``.

        Raises :class:`SummaryGenerationError` when the chunk cannot be summarised;
        the caller records the failure instead of dropping the chunk silently.
        """
        system_prompt, user_prompt = build_chunk_messages(
            chunk_text=chunk.text,
            options=options,
            chunk_index=chunk.index,
            chunk_total=chunk_total,
            start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds,
        )
        response = self._client.request_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stage="chunk",
            max_tokens=self._settings.summary_intermediate_max_tokens,
        )
        summary = build_chunk_summary(
            response.data,
            index=chunk.index,
            start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds,
            segment_count=chunk.segment_count,
            options=options,
            validator=validator,
            source_text=chunk.text,
        )
        if summary.is_empty:
            raise SummaryGenerationError(
                "The model returned an empty summary for one section of the transcript.",
                error_type="empty_response",
            )
        logger.info(
            "Chunk %s/%s summarised (segments=%s, span=%s)",
            chunk.index + 1,
            chunk_total,
            chunk.segment_count,
            chunk.segment_range,
        )
        return summary, response.attempts
