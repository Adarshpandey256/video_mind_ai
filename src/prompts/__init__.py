"""Prompt construction for VideoMind AI.

Step 3 keeps its summarisation prompts in :mod:`src.prompts.summary_prompts` so the
service layer never builds instruction strings inline.
"""

from src.prompts.summary_prompts import (
    CHUNK_SYSTEM_PROMPT,
    JSON_CONTRACT,
    MERGE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_chunk_messages,
    build_merge_messages,
    build_repair_prompt,
    build_summary_messages,
    sanitize_custom_instruction,
)

__all__ = [
    "CHUNK_SYSTEM_PROMPT",
    "JSON_CONTRACT",
    "MERGE_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "build_chunk_messages",
    "build_merge_messages",
    "build_repair_prompt",
    "build_summary_messages",
    "sanitize_custom_instruction",
]
