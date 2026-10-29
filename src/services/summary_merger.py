"""Merge intermediate chunk summaries into one final summary (Step 3).

The merge is kept deliberately simple and bounded:

* one merge pass whenever the intermediate summaries fit a single request,
* one extra hierarchical level only when there are enough sections to justify it,
* the original ordering is preserved, so chronological flow survives,
* conflicting statements are kept and reported instead of being flattened into
  false certainty.
"""

from __future__ import annotations

import logging

from config.settings import Settings, get_settings
from src.models.summary_schemas import ChunkSummary, SummaryOptions
from src.prompts.summary_prompts import build_merge_messages
from src.services.chunk_summarizer import build_chunk_summary
from src.services.summary_generation import SummaryGenerationError, SummaryJsonClient
from src.services.timestamp_service import TimestampValidator

logger = logging.getLogger(__name__)

#: Above this many sections, one extra merge level is inserted before the final pass.
MERGE_GROUP_SIZE = 10

__all__ = ["SummaryMerger", "SummaryGenerationError"]


class SummaryMerger:
    """Combine chunk summaries into a single validated summary object."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: SummaryJsonClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or SummaryJsonClient(self._settings)

    @property
    def settings(self) -> Settings:
        """Return the settings that control merge requests."""
        return self._settings

    def merge(
        self,
        sections: list[ChunkSummary],
        options: SummaryOptions,
        validator: TimestampValidator,
        *,
        source_title: str | None = None,
    ) -> tuple[dict, int]:
        """Merge sections into the final summary JSON.

        Returns ``(merged_data, model_calls)``. Raises
        :class:`SummaryGenerationError` when merging cannot produce a result.
        """
        if not sections:
            raise SummaryGenerationError(
                "There was nothing to merge because no transcript section could be "
                "summarised.",
                error_type="empty_response",
            )

        calls = 0
        current = sections
        # At most one hierarchical level: only add a pass when the section count really
        # is too large for a single merge request.
        while len(current) > MERGE_GROUP_SIZE:
            logger.info(
                "Merging %s sections hierarchically in groups of %s.",
                len(current),
                MERGE_GROUP_SIZE,
            )
            reduced: list[ChunkSummary] = []
            for start in range(0, len(current), MERGE_GROUP_SIZE):
                group = current[start : start + MERGE_GROUP_SIZE]
                data, group_calls = self._merge_group(
                    group, options, source_title=source_title, is_final=False
                )
                calls += group_calls
                reduced.append(
                    build_chunk_summary(
                        data,
                        index=len(reduced),
                        start_seconds=group[0].start_seconds,
                        end_seconds=group[-1].end_seconds,
                        segment_count=sum(item.segment_count for item in group),
                        options=options,
                        validator=validator,
                        source_text="\n".join(item.render_for_merge() for item in group),
                    )
                )
            if len(reduced) >= len(current):  # pragma: no cover - defensive
                break
            current = reduced

        merged, merge_calls = self._merge_group(
            current, options, source_title=source_title, is_final=True
        )
        calls += merge_calls

        limitations = list(merged.get("limitations") or [])
        if not isinstance(limitations, list):
            limitations = [str(limitations)]
        merged["limitations"] = limitations
        logger.info("Merged %s section summaries in %s model call(s).", len(sections), calls)
        return merged, calls

    # ------------------------------------------------------------------ internals
    def _merge_group(
        self,
        group: list[ChunkSummary],
        options: SummaryOptions,
        *,
        source_title: str | None,
        is_final: bool,
    ) -> tuple[dict, int]:
        """Run one merge request over a group of sections."""
        body = "\n\n".join(section.render_for_merge() for section in group)
        system_prompt, user_prompt = build_merge_messages(
            section_summaries=body,
            options=options,
            source_title=source_title,
            section_count=len(group),
            is_final=is_final,
        )
        response = self._client.request_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stage="merge",
            max_tokens=(
                self._settings.summary_merge_max_tokens
                if is_final
                else self._settings.summary_intermediate_max_tokens
            ),
        )
        return response.data, response.attempts
