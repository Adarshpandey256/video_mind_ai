"""Validate model output and rebuild it into typed summary objects (Step 3).

Separating validation from generation keeps both reviewable on their own: the
generation layer only has to obtain JSON, and this module is the single place that
decides what is allowed into a summary.

Two kinds of correction happen here, and both are reported rather than hidden:

* **Timestamp correction** - delegated to :class:`TimestampValidator`, which snaps a
  time to a real transcript position or drops it.
* **Grounding** - an owner or deadline is kept only when the transcript actually
  says it, so the application-side fallback can never introduce a claim the source
  does not support.

Nothing is invented: a field that cannot be validated is emptied, and the reason is
recorded in ``limitations`` so the user sees the honest picture.
"""

from __future__ import annotations

import re
from typing import Any

from src.models.summary_schemas import (
    ActionItem,
    Chapter,
    Decision,
    ImportantPoint,
    KeyTakeaway,
    StructuredSummary,
    SummaryCoverage,
    SummaryMode,
    SummaryOptions,
    SummaryStatus,
    Topic,
)
from src.services.timestamp_service import TimestampValidator, is_grounded
from src.utils.json_utils import (
    coerce_optional_text,
    coerce_seconds,
    coerce_str_list,
    coerce_text,
)

#: Jaccard similarity above which two points are treated as the same point.
DUPLICATE_THRESHOLD = 0.82
_WORD = re.compile(r"[a-z0-9]+")

MAX_ITEMS_PER_SECTION = 40


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    """Normalise a model list into a list of dictionaries."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        return [{"title": value}]
    if not isinstance(value, (list, tuple)):
        return []
    items: list[dict[str, Any]] = []
    for entry in value[:MAX_ITEMS_PER_SECTION]:
        if isinstance(entry, dict):
            items.append(entry)
        elif isinstance(entry, str) and entry.strip():
            items.append({"title": entry.strip()})
    return items


def _first_text(item: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty value among ``keys`` as text."""
    for key in keys:
        text = coerce_text(item.get(key))
        if text:
            return text
    return ""


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _similar(left: str, right: str) -> bool:
    """True when two short texts say essentially the same thing."""
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return left.strip().lower() == right.strip().lower()
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return union > 0 and overlap / union >= DUPLICATE_THRESHOLD


def deduplicate(items: list[Any], key, *, limit: int = MAX_ITEMS_PER_SECTION) -> tuple[list[Any], int]:
    """Drop later items that repeat an earlier one; returns ``(kept, dropped)``."""
    kept: list[Any] = []
    dropped = 0
    for item in items:
        value = key(item)
        if any(_similar(value, key(existing)) for existing in kept):
            dropped += 1
            continue
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept, dropped


# --------------------------------------------------------------------------- items
def build_takeaways(
    value: Any,
    validator: TimestampValidator,
    *,
    source_text: str = "",
    max_items: int = 12,
) -> list[KeyTakeaway]:
    """Build validated key takeaways from raw model output.

    A supporting excerpt is kept only when it genuinely appears in the source text;
    an unverifiable quotation is dropped rather than shown as if the speaker said it.
    """
    items: list[KeyTakeaway] = []
    for raw in _as_dicts(value):
        title = _first_text(raw, "title", "takeaway", "point", "heading")
        explanation = _first_text(raw, "explanation", "detail", "description")
        if not title and not explanation:
            continue
        start, end = validator.sanitize(
            coerce_seconds(raw.get("start_seconds")), coerce_seconds(raw.get("end_seconds"))
        )
        excerpt = coerce_optional_text(raw.get("excerpt") or raw.get("quote"))
        if excerpt and source_text and not is_grounded(excerpt, source_text):
            excerpt = None
        items.append(
            KeyTakeaway(
                title=title or explanation,
                explanation=explanation if title else "",
                start_seconds=start,
                end_seconds=end,
                excerpt=excerpt,
            )
        )
    kept, _ = deduplicate(items, lambda item: item.title, limit=max_items)
    return kept


def build_topics(value: Any, validator: TimestampValidator) -> list[Topic]:
    """Build validated topics from raw model output."""
    items: list[Topic] = []
    for raw in _as_dicts(value):
        title = _first_text(raw, "title", "topic", "name", "heading")
        explanation = _first_text(raw, "explanation", "summary", "description")
        if not title and not explanation:
            continue
        start, end = validator.sanitize(
            coerce_seconds(raw.get("start_seconds")), coerce_seconds(raw.get("end_seconds"))
        )
        items.append(
            Topic(
                title=title or explanation,
                explanation=explanation if title else "",
                start_seconds=start,
                end_seconds=end,
                key_points=coerce_str_list(
                    raw.get("key_points") or raw.get("subpoints") or raw.get("points")
                ),
            )
        )
    kept, _ = deduplicate(items, lambda item: item.title)
    return kept


def build_chapters(value: Any, validator: TimestampValidator) -> list[Chapter]:
    """Build validated chapters, sorted by their source position."""
    items: list[Chapter] = []
    for raw in _as_dicts(value):
        title = _first_text(raw, "title", "chapter", "name", "heading")
        description = _first_text(raw, "description", "summary", "explanation")
        if not title and not description:
            continue
        start, end = validator.sanitize(
            coerce_seconds(raw.get("start_seconds")), coerce_seconds(raw.get("end_seconds"))
        )
        items.append(
            Chapter(
                title=title or description,
                description=description if title else "",
                start_seconds=start,
                end_seconds=end,
                major_points=coerce_str_list(
                    raw.get("major_points") or raw.get("key_points") or raw.get("points")
                ),
            )
        )
    kept, _ = deduplicate(items, lambda item: item.title)
    kept.sort(key=lambda item: (item.start_seconds is None, item.start_seconds or 0.0))
    return kept


def build_action_items(
    value: Any, validator: TimestampValidator, *, source_text: str = ""
) -> tuple[list[ActionItem], list[str]]:
    """Build action items, dropping any owner or deadline the source does not state.

    Returns ``(items, notes)`` where ``notes`` records what was removed, so the
    reason reaches the user instead of being swallowed.
    """
    items: list[ActionItem] = []
    notes: list[str] = []
    dropped_owners = 0
    dropped_deadlines = 0

    for raw in _as_dicts(value):
        task = _first_text(raw, "task", "action", "description", "title", "item")
        if not task:
            continue
        owner = coerce_optional_text(raw.get("owner") or raw.get("assignee"))
        deadline = coerce_optional_text(raw.get("deadline") or raw.get("due"))
        if owner and source_text and not is_grounded(owner, source_text):
            owner = None
            dropped_owners += 1
        if deadline and source_text and not is_grounded(deadline, source_text):
            deadline = None
            dropped_deadlines += 1
        start, _ = validator.sanitize(coerce_seconds(raw.get("start_seconds")), None)
        items.append(
            ActionItem(
                task=task,
                owner=owner,
                deadline=deadline,
                status=coerce_optional_text(raw.get("status")),
                start_seconds=start,
            )
        )

    if dropped_owners:
        notes.append(
            f"{dropped_owners} action-item owner(s) were removed because the transcript does "
            "not state who is responsible."
        )
    if dropped_deadlines:
        notes.append(
            f"{dropped_deadlines} action-item deadline(s) were removed because the transcript "
            "does not state them."
        )
    kept, dropped = deduplicate(items, lambda item: item.task)
    if dropped:
        notes.append(f"{dropped} duplicated action item(s) were merged into one entry.")
    return kept, notes


def build_decisions(value: Any, validator: TimestampValidator) -> list[Decision]:
    """Build explicit decisions from raw model output."""
    items: list[Decision] = []
    for raw in _as_dicts(value):
        decision = _first_text(raw, "decision", "text", "title", "summary")
        if not decision:
            continue
        start, _ = validator.sanitize(coerce_seconds(raw.get("start_seconds")), None)
        items.append(
            Decision(
                decision=decision,
                context=coerce_optional_text(raw.get("context")),
                start_seconds=start,
            )
        )
    kept, _ = deduplicate(items, lambda item: item.decision)
    return kept


def build_points(value: Any, validator: TimestampValidator) -> list[ImportantPoint]:
    """Build validated important points from raw model output."""
    items: list[ImportantPoint] = []
    for raw in _as_dicts(value):
        point = _first_text(raw, "point", "text", "title", "fact")
        if not point:
            continue
        start, end = validator.sanitize(
            coerce_seconds(raw.get("start_seconds")), coerce_seconds(raw.get("end_seconds"))
        )
        items.append(
            ImportantPoint(
                point=point,
                explanation=coerce_optional_text(raw.get("explanation") or raw.get("detail")),
                start_seconds=start,
                end_seconds=end,
            )
        )
    kept, _ = deduplicate(items, lambda item: item.point)
    return kept


# --------------------------------------------------------------------------- summary
def build_structured_summary(
    data: dict[str, Any],
    *,
    source_id: str,
    source_title: str | None,
    source_type,
    options: SummaryOptions,
    validator: TimestampValidator,
    source_text: str = "",
    generation_model: str | None = None,
    coverage: SummaryCoverage | None = None,
    status: SummaryStatus = SummaryStatus.COMPLETED,
    used_chunking: bool = False,
    model_calls: int = 0,
    latency_ms: float | None = None,
    source_duration_seconds: float | None = None,
    transcript_id: str | None = None,
    generate_id: bool = True,
) -> tuple[StructuredSummary, list[str]]:
    """Validate raw model JSON and return ``(summary, quality_notes)``."""
    from src.services.summary_storage import new_summary_id

    notes: list[str] = []
    overall = coerce_text(
        data.get("overall_summary") or data.get("summary") or data.get("overview")
    )
    short = coerce_optional_text(data.get("short_summary") or data.get("tldr"))

    takeaways = build_takeaways(
        data.get("key_takeaways") or data.get("takeaways"),
        validator,
        source_text=source_text,
    )
    topics = build_topics(data.get("main_topics") or data.get("topics"), validator)
    chapters = build_chapters(data.get("chapters"), validator)
    actions, action_notes = build_action_items(
        data.get("action_items"), validator, source_text=source_text
    )
    notes.extend(action_notes)
    decisions = build_decisions(data.get("decisions"), validator)
    points = build_points(data.get("important_points") or data.get("key_points"), validator)

    # Cross-section duplication: a point already stated as a takeaway or a topic must
    # not be repeated as an important point as well.
    headlines = [item.title for item in takeaways] + [topic.title for topic in topics]
    unique_points = [
        point for point in points if not any(_similar(point.point, headline) for headline in headlines)
    ]
    if len(unique_points) != len(points):
        notes.append(
            f"{len(points) - len(unique_points)} important point(s) repeated a takeaway or topic "
            "and were removed."
        )
    points = unique_points

    if validator.repairs_made:
        notes.append(
            f"{validator.repairs_made} timestamp reference(s) were adjusted to a real transcript "
            "position or removed because they did not match the source."
        )

    # Everything validation dropped or corrected is part of the summary's own record,
    # so the user sees the honest picture wherever the object is displayed or exported.
    limitations = list(
        dict.fromkeys([*coerce_str_list(data.get("limitations") or data.get("unclear")), *notes])
    )
    if not overall and not short and not any([takeaways, topics, chapters, points]):
        raise ValueError("The model returned no summary content at all.")

    summary = StructuredSummary(
        summary_id=new_summary_id() if generate_id else None,
        source_id=source_id,
        source_transcript_id=transcript_id,
        source_title=source_title,
        source_type=source_type,
        summary_type=options.mode,
        options_fingerprint=options.fingerprint,
        overall_summary=overall or short or "",
        short_summary=short,
        key_takeaways=takeaways,
        main_topics=topics,
        chapters=chapters,
        action_items=actions,
        decisions=decisions,
        important_points=points,
        limitations=limitations,
        language=options.language,
        generation_model=generation_model,
        source_transcript_reference=transcript_id,
        source_duration_seconds=source_duration_seconds,
        processing_status=status,
        coverage=coverage or SummaryCoverage(),
        model_calls=model_calls,
        latency_ms=latency_ms,
        used_chunking=used_chunking,
    )
    return summary, notes


def quality_notes(
    summary: StructuredSummary,
    *,
    expected_mode: SummaryOptions,
    source_title: str | None = None,
) -> list[str]:
    """Deterministic checks that describe what the user should know about a result."""
    notes: list[str] = []

    if summary.summary_type is not expected_mode.mode:
        notes.append(
            f"The stored summary type ({summary.summary_type.value}) does not match the "
            f"requested style ({expected_mode.mode.value})."
        )
    if not summary.overall_summary.strip():
        notes.append("The model did not return an overall summary paragraph.")
    if not any([summary.key_takeaways, summary.main_topics, summary.chapters, summary.important_points]):
        notes.append("No structured sections were extracted from this recording.")

    if expected_mode.mode is SummaryMode.MEETING and not summary.action_items and not summary.decisions:
        notes.append(
            "The transcript contains no explicit action items or decisions, so those "
            "sections are empty rather than guessed."
        )

    if summary.coverage.is_partial:
        notes.append(
            "This summary covers only part of the recording; see the coverage information."
        )
    if source_title and summary.source_title and summary.source_title != source_title:
        notes.append(
            "The summary was generated from a different source title than the current selection."
        )
    return notes
