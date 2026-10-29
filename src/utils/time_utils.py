"""Timestamp helpers shared by the interface and transcript exports."""

from __future__ import annotations

UNAVAILABLE_TIMESTAMP = "timestamp unavailable"


def format_timestamp(seconds: float | None) -> str:
    """Format a position in a recording as ``MM:SS`` or ``HH:MM:SS``.

    Unknown positions are reported as unavailable instead of being invented.
    """
    if seconds is None:
        return UNAVAILABLE_TIMESTAMP
    total = int(round(max(0.0, float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: float | None) -> str:
    """Format a duration for humans, e.g. ``12 min 34 s``."""
    if seconds is None:
        return "unknown"
    total = int(round(max(0.0, float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
