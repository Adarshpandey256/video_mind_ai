"""Helpers for reading structured output from a language model.

The model is asked to return a JSON object, but providers sometimes wrap it in
markdown fences or surround it with prose. :func:`extract_json_object` recovers the
object using :mod:`json` only - arbitrary text is never evaluated.

The coercion helpers are deliberately forgiving: a wrong type degrades one field
instead of failing a whole summarisation run, and nothing is ever invented.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ModelOutputError",
    "coerce_float",
    "coerce_optional_text",
    "coerce_seconds",
    "coerce_str_list",
    "coerce_text",
    "extract_json_object",
]

#: A fenced code block, optionally tagged as json.
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_ZERO_LIMIT = 8


class ModelOutputError(ValueError):
    """Raised when a model response cannot be read as a JSON object."""


def _balanced_objects(text: str) -> Iterator[str]:
    """Yield every top-level ``{...}`` substring, ignoring braces inside strings."""
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start : index + 1]


def extract_json_object(text: str, *, limit: int = _ZERO_LIMIT) -> dict[str, Any]:
    """Return the first JSON object found in a model response.

    Tries the raw text, then any fenced block, then every balanced ``{...}`` region.
    Raises :class:`ModelOutputError` when nothing parses into a JSON object.
    """
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputError("the response was empty")

    candidates: list[str] = [text.strip()]
    candidates.extend(match.strip() for match in _FENCE.findall(text))
    candidates.extend(_balanced_objects(text))

    seen: set[str] = set()
    for candidate in candidates[:limit]:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            # Some models wrap the object in a single element array.
            return parsed[0]

    raise ModelOutputError("no JSON object could be found in the response")


# --------------------------------------------------------------------------- coercion
def coerce_text(value: Any) -> str:
    """Return a trimmed string; lists and dicts are flattened rather than dropped."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return " ".join(part for part in (coerce_text(item) for item in value) if part).strip()
    if isinstance(value, dict):
        parts = [coerce_text(item) for item in value.values()]
        return " ".join(part for part in parts if part).strip()
    return ""


def coerce_optional_text(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` for empty/placeholder answers.

    Treats the strings a model uses for "nothing" as missing, so an invented owner
    is never represented as the literal text ``"unknown"``.
    """
    text = coerce_text(value)
    if not text:
        return None
    if text.lower().strip(" .:;-_") in {"unknown", "n/a", "na", "none", "null", "not stated", "not specified", "unspecified", "-"}:
        return None
    return text


def coerce_str_list(value: Any, *, max_items: int = 40, max_chars: int = 500) -> list[str]:
    """Return a list of trimmed, non-empty, deduplicated strings."""
    if value is None:
        return []
    if isinstance(value, str):
        raw = [line.strip(" -•\t") for line in value.splitlines()]
    elif isinstance(value, (list, tuple, set)):
        raw = [coerce_text(item) for item in value]
    else:
        raw = [coerce_text(value)]

    items: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = item.strip()
        if not text or text.lower() in {"none", "n/a", "null"}:
            continue
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def coerce_float(value: Any) -> float | None:
    """Return a float from a number or numeric string, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        number = float(match.group(0))
    else:
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def coerce_seconds(value: Any) -> float | None:
    """Return a non-negative number of seconds, else ``None``.

    Accepts ``"1:30"``/``"01:02:03"`` as well as plain seconds, because models
    sometimes echo a formatted clock position instead of a number.
    """
    if isinstance(value, str):
        text = value.strip()
        if ":" in text:
            parts = text.split(":")
            if 2 <= len(parts) <= 3 and all(part.strip().isdigit() for part in parts):
                numbers = [int(part) for part in parts]
                seconds = 0
                for number in numbers:
                    seconds = seconds * 60 + number
                return float(seconds)
    number = coerce_float(value)
    if number is None or number < 0:
        return None
    return number
