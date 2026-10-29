"""Timestamp validation for Step 3 (and reuse by later steps).

Every timestamp that reaches a summary comes from a segment of the Step 2
transcript. The model may echo a time, round it, or invent one, so this module is
the single place that decides whether a returned time is credible:

* negative times are dropped,
* times beyond the recording (plus a small tolerance) are dropped,
* a time close to a real segment boundary is snapped to that boundary, so the user
  can actually find the moment,
* an interval whose end precedes its start is dropped,
* nothing is invented: when no validation is possible the reference becomes
  ``None`` and the reason is recorded for the quality report.

The module never fabricates precision: it only ever reuses values taken from the
transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models.schemas import TranscriptRecord, TranscriptSegment
from src.utils.time_utils import UNAVAILABLE_TIMESTAMP, format_timestamp

#: How close a model supplied time must be to a real segment boundary to be snapped.
DEFAULT_TOLERANCE_SECONDS = 3.0


@dataclass(frozen=True)
class TimestampRepair:
    """One repaired or rejected timestamp, for the quality report."""

    original_start: float | None
    original_end: float | None
    action: str  # "snapped" | "dropped_start" | "dropped_end" | "dropped_interval"
    reason: str


@dataclass
class TimestampValidator:
    """Validate model timestamps against the transcript they came from."""

    segments: list[TranscriptSegment]
    duration_seconds: float | None = None
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS
    repairs: list[TimestampRepair] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._ordered = sorted(
            (segment for segment in self.segments if segment.has_timestamps),
            key=lambda segment: segment.start_seconds or 0.0,
        )
        if self.duration_seconds is None and self._ordered:
            last = self._ordered[-1]
            self.duration_seconds = last.end_seconds

    # ------------------------------------------------------------------ properties
    @property
    def has_timeline(self) -> bool:
        """True when the transcript carries at least one usable timestamp."""
        return bool(self._ordered)

    @property
    def repairs_made(self) -> int:
        """Number of timestamps that were repaired or rejected."""
        return len(self.repairs)

    # ------------------------------------------------------------------ validation
    def sanitize(
        self,
        start: float | None,
        end: float | None = None,
    ) -> tuple[float | None, float | None]:
        """Return a credible ``(start, end)`` pair, repairing or dropping bad values.

        Mirrors the rule "remove it or mark the reference unavailable rather than
        displaying a false location".
        """
        if not self.has_timeline:
            if start is not None or end is not None:
                self.repairs.append(
                    TimestampRepair(start, end, "dropped_start", "transcript has no timestamps")
                )
            return None, None

        clean_start, start_action, start_reason = self._clean_value(start)
        clean_end, end_action, end_reason = self._clean_value(end)

        if clean_start is None and clean_end is not None:
            # An end without a start cannot be presented as a location.
            clean_end = None
            self.repairs.append(
                TimestampRepair(start, end, "dropped_end", "no valid start time")
            )

        if clean_start is not None and clean_end is not None and clean_end < clean_start:
            self.repairs.append(
                TimestampRepair(start, end, "dropped_interval", "end before start")
            )
            clean_end = None

        if start_action:
            self.repairs.append(TimestampRepair(start, end, start_action, start_reason))
        if end_action and clean_end is not None:
            self.repairs.append(TimestampRepair(start, end, end_action, end_reason))
        return clean_start, clean_end

    def _clean_value(self, value: float | None) -> tuple[float | None, str, str]:
        """Validate one value; returns ``(value, action, reason)``."""
        if value is None:
            return None, "", ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None, "dropped_start", "not a number"
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            return None, "dropped_start", "not a finite number"
        if number < 0:
            return None, "dropped_start", "negative time"
        if self.duration_seconds is not None and number > self.duration_seconds + self.tolerance_seconds:
            return None, "dropped_start", "beyond the end of the recording"

        snapped = self._snap(number)
        if snapped is None:
            return None, "dropped_start", "not aligned with any transcript segment"
        if abs(snapped - number) > 0.5:
            return snapped, "snapped", "aligned to the nearest transcript segment"
        return snapped, "", ""

    def _snap(self, value: float) -> float | None:
        """Return the transcript time that best matches ``value``.

        A time that already sits on a segment boundary keeps that exact boundary;
        otherwise the start of the segment containing it is used. Only when neither
        applies is the nearest segment considered.
        """
        for segment in self._ordered:
            start = segment.start_seconds or 0.0
            if abs(start - value) <= 0.5:
                return start

        for segment in self._ordered:
            start = segment.start_seconds or 0.0
            end = segment.end_seconds if segment.end_seconds is not None else start
            if start <= value <= end:
                return start
        nearest = min(
            self._ordered,
            key=lambda segment: abs((segment.start_seconds or 0.0) - value),
        )
        if abs((nearest.start_seconds or 0.0) - value) <= self.tolerance_seconds:
            return nearest.start_seconds
        return None

    # ------------------------------------------------------------------ helpers
    def excerpt(self, start: float | None, end: float | None, *, max_chars: int = 280) -> str | None:
        """Return the transcript text inside a validated interval, or ``None``."""
        if start is None:
            return None
        limit = end if end is not None and end > start else None
        parts = [
            segment.text.strip()
            for segment in self._ordered
            if (segment.start_seconds or 0.0) >= start - self.tolerance_seconds
            and (limit is None or (segment.start_seconds or 0.0) <= limit)
        ]
        text = " ".join(part for part in parts if part).strip()
        if not text:
            return None
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "..."
        return text

    def span_label(self, start: float | None, end: float | None) -> str:
        """Readable label for an interval, honest when it is unavailable."""
        if start is None:
            return UNAVAILABLE_TIMESTAMP
        if end is None or end <= start:
            return format_timestamp(start)
        return f"{format_timestamp(start)} - {format_timestamp(end)}"


def validator_for(
    transcript: TranscriptRecord | None,
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    duration_seconds: float | None = None,
) -> TimestampValidator:
    """Build a validator for a transcript (or an empty one when it has no timeline)."""
    segments = list(transcript.segments) if transcript is not None else []
    duration = duration_seconds
    if duration is None and transcript is not None:
        duration = transcript.duration_seconds
    return TimestampValidator(
        segments=segments,
        duration_seconds=duration,
        tolerance_seconds=tolerance_seconds,
    )


def is_grounded(value: str | None, source_text: str) -> bool:
    """True when a short value genuinely appears in the source transcript.

    Used to drop invented owners and deadlines: the application keeps such a value
    only when the transcript itself contains it.
    """
    if not value:
        return False
    needle = " ".join(str(value).lower().split())
    if not needle:
        return False
    haystack = " ".join(source_text.lower().split())
    if needle in haystack:
        return True
    # Only a single-word value may match loosely, so a multi-word claim such as
    # "March 12" is never accepted merely because the word "March" occurs.
    if len(needle.split()) > 1:
        return False
    return any(token in haystack for token in needle.split() if len(token) > 2)
