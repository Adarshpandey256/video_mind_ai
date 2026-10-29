"""Grounded answer generation for the video chatbot (Step 4).

The model is asked for a small JSON object so citations can be checked *in
Python* before the user sees anything:

* the answer text comes from the model;
* every citation must point at a source id that was actually retrieved;
* every quotation must occur in that passage, otherwise it is removed;
* inline references to ids that were never supplied are removed and reported.

A response that cannot be parsed into that shape is retried once and then
rejected, rather than shown without citations. Rejecting an answer is the honest
behaviour: a confident paragraph with invented sources is worse than a retry.

The chain is composed with LangChain's current LCEL interface
(``prompt | model | parser``) - the model step is a thin adapter around the
existing Step 1 :class:`~src.services.llm_service.LLMService`, so there is still
exactly one NVIDIA client and one configuration.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from config.settings import Settings, get_settings
from src.models.rag_schemas import ChatTurn, RagCitation, RetrievedSource
from src.rag.prompts import (
    SYSTEM_PROMPT,
    build_answer_user_prompt,
    build_repair_prompt,
)
from src.services.llm_service import LLMService
from src.services.nvidia_client import NvidiaError, classify_error_type
from src.utils.json_utils import ModelOutputError, coerce_text, extract_json_object

logger = logging.getLogger(__name__)

#: One original attempt plus one "reply with JSON only" repair attempt.
MAX_ANSWER_ATTEMPTS = 2
#: Inline citation marker, e.g. ``[S1]``.
INLINE_REFERENCE = re.compile(r"\[(S\d+)\]", re.IGNORECASE)
_QUOTE_CLEANUP = re.compile(r"[^0-9a-z\u0900-\u097F]+")


class AnswerGenerationError(RuntimeError):
    """A grounded answer that could not be produced, with a stable error type."""

    def __init__(self, message: str, *, error_type: str = "provider") -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass
class GeneratedAnswer:
    """A validated answer and how it was checked."""

    answer: str
    citations: list[RagCitation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    insufficient_evidence: bool = False
    model: str | None = None
    latency_ms: float | None = None
    total_tokens: int | None = None


def normalize_reference(value: str) -> str:
    """Normalise ``s1``/``[S1]``/``S 1`` to ``S1``; unknown shapes stay as-is."""
    text = (value or "").strip().strip("[]").replace(" ", "").upper()
    return text


def quote_appears_in(quote: str, passage: str) -> bool:
    """True when ``quote`` really occurs in the passage (whitespace/punctuation tolerant)."""
    cleaned_quote = _QUOTE_CLEANUP.sub(" ", (quote or "").lower()).strip()
    if len(cleaned_quote) < 8:
        return False
    cleaned_passage = _QUOTE_CLEANUP.sub(" ", (passage or "").lower())
    return cleaned_quote in cleaned_passage


class GroundedAnswerGenerator:
    """Turn a question plus retrieved passages into a cited answer."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm: LLMService | None = None,
        chain: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm or LLMService(self._settings)
        self._chain = chain or self._build_chain()
        self._last_response: Any | None = None
        self._temperature: float | None = None
        self._max_tokens: int | None = None

    # ------------------------------------------------------------------ public API
    @property
    def settings(self) -> Settings:
        """Return the settings used for answer requests."""
        return self._settings

    def is_configured(self) -> bool:
        """True when an API key exists, so an answer can be attempted."""
        return self._llm.is_configured()

    def generate(
        self,
        question: str,
        sources: Sequence[RetrievedSource],
        *,
        history: Sequence[ChatTurn] = (),
        video_title: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> GeneratedAnswer:
        """Answer ``question`` from ``sources``, validating every citation.

        Raises :class:`AnswerGenerationError` when no usable, citable answer could
        be produced. It never returns a fabricated answer.
        """
        if not self.is_configured():
            raise AnswerGenerationError(
                "No NVIDIA API key is configured, so no answer can be generated. Add "
                "NVIDIA_API_KEY to your local .env file and restart the application.",
                error_type="configuration",
            )
        if not sources:
            raise ValueError("At least one retrieved passage is required to answer.")

        self._temperature = (
            self._settings.rag_temperature if temperature is None else float(temperature)
        )
        self._max_tokens = int(max_tokens or self._settings.rag_answer_max_tokens)

        prompt = build_answer_user_prompt(
            question=question,
            sources=sources,
            history=history,
            history_limit=self._settings.rag_max_history_turns,
            video_title=video_title,
        )

        last_problem = ""
        for attempt in range(1, MAX_ANSWER_ATTEMPTS + 1):
            text = self._invoke(prompt)
            if not (text or "").strip():
                raise AnswerGenerationError(
                    "The model returned an empty answer. Try asking again.",
                    error_type="empty_response",
                )
            try:
                data = extract_json_object(text)
            except ModelOutputError as error:
                last_problem = str(error)
                logger.warning(
                    "Answer was not usable JSON (attempt=%s of %s).",
                    attempt,
                    MAX_ANSWER_ATTEMPTS,
                )
                if attempt >= MAX_ANSWER_ATTEMPTS:
                    raise AnswerGenerationError(
                        "The model's answer could not be matched to its sources, so it was "
                        "discarded instead of showing uncited text. Try asking again.",
                        error_type="invalid_output",
                    ) from error
                prompt = build_repair_prompt(text, last_problem)
                continue
            return self._validate(data, sources)

        raise AnswerGenerationError(  # pragma: no cover - loop always returns or raises
            "The answer could not be produced.", error_type="invalid_output"
        )

    # ------------------------------------------------------------------ internals
    def _build_chain(self) -> Any:
        """Compose the prompt, the model adapter and the text parser with LCEL."""
        prompt = ChatPromptTemplate.from_messages(
            [("system", SYSTEM_PROMPT), ("human", "{user_prompt}")]
        )
        return prompt | RunnableLambda(self._call_model) | StrOutputParser()

    def _call_model(self, messages: Any) -> str:
        """Adapter: render the LangChain messages and call the Step 1 service."""
        rendered = messages.to_messages() if hasattr(messages, "to_messages") else list(messages)
        system_parts = [
            getattr(message, "content", "") for message in rendered if getattr(message, "type", "") == "system"
        ]
        human_parts = [
            getattr(message, "content", "") for message in rendered if getattr(message, "type", "") == "human"
        ]
        system_prompt = "\n\n".join(part for part in system_parts if part)
        user_prompt = "\n\n".join(part for part in human_parts if part)

        self._last_response = None
        try:
            response = self._llm.generate(
                user_prompt,
                system_prompt=system_prompt,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                timeout=self._settings.rag_timeout_seconds,
            )
        except NvidiaError as error:
            error_type = classify_error_type(error)
            logger.warning("Answer request failed (type=%s).", error_type)
            raise AnswerGenerationError(
                _friendly_message(error_type), error_type=error_type
            ) from error
        except Exception as error:  # noqa: BLE001 - translated, never leaked
            logger.warning("Answer request failed unexpectedly: %s", type(error).__name__)
            raise AnswerGenerationError(
                "The AI provider could not be reached. Check your connection and try again.",
                error_type="provider",
            ) from error

        if response.finish_reason == "length":
            raise AnswerGenerationError(
                "The model ran out of its output budget before finishing the answer. Raise "
                "RAG_ANSWER_MAX_TOKENS in your local .env file and try again.",
                error_type="truncated",
            )

        self._last_response = response
        return response.text

    def _validate(self, data: dict[str, Any], sources: Sequence[RetrievedSource]) -> GeneratedAnswer:
        """Resolve citations to real passages and strip anything unverifiable."""
        answer_text = coerce_text(
            data.get("answer") or data.get("response") or data.get("text") or ""
        )
        if not answer_text:
            raise AnswerGenerationError(
                "The model returned an empty answer. Try asking again.",
                error_type="invalid_output",
            )

        by_ref = {source.source_ref: source for source in sources}
        notes: list[str] = []
        citations: list[RagCitation] = []
        cited_refs: set[str] = set()
        unknown_refs: set[str] = set()
        unverified_quotes: list[str] = []

        raw_citations = data.get("citations") or []
        if isinstance(raw_citations, dict):
            raw_citations = [raw_citations]
        if not isinstance(raw_citations, list):
            raw_citations = []

        for raw in raw_citations:
            if isinstance(raw, str):
                reference, quote = normalize_reference(raw), None
            elif isinstance(raw, dict):
                reference = normalize_reference(
                    coerce_text(raw.get("source") or raw.get("id") or raw.get("ref"))
                )
                quote = coerce_text(raw.get("quote") or raw.get("excerpt")) or None
            else:
                continue
            source = by_ref.get(reference)
            if source is None:
                if reference:
                    unknown_refs.add(reference)
                continue
            if reference in cited_refs:
                continue
            verified_quote = quote
            if quote and not quote_appears_in(quote, source.text):
                verified_quote = None
                unverified_quotes.append(reference)
            citations.append(RagCitation.from_source(source, quote=verified_quote))
            cited_refs.add(reference)

        if unknown_refs:
            notes.append(
                "The answer cited source(s) that were not supplied to the model "
                f"({', '.join(sorted(unknown_refs))}); those citations were removed."
            )
            logger.info("Dropped %s unknown citation(s).", len(unknown_refs))

        if unverified_quotes:
            notes.append(
                "A quotation for "
                f"{', '.join(sorted(set(unverified_quotes)))} did not match the retrieved "
                "passage word for word, so it was not shown."
            )

        answer_text, stripped_refs = _strip_unknown_inline_references(answer_text, set(by_ref))
        if stripped_refs:
            notes.append(
                "Inline references to source(s) that were not supplied "
                f"({', '.join(sorted(stripped_refs))}) were removed from the answer."
            )

        response = self._last_response
        return GeneratedAnswer(
            answer=answer_text,
            citations=citations,
            notes=notes,
            insufficient_evidence=bool(data.get("insufficient_evidence", False)),
            model=getattr(response, "model", None),
            latency_ms=getattr(response, "latency_ms", None),
            total_tokens=getattr(response, "total_tokens", None),
        )

    def _invoke(self, user_prompt: str) -> str:
        """Run the chain once and translate unexpected failures."""
        try:
            return self._chain.invoke({"user_prompt": user_prompt})
        except AnswerGenerationError:
            raise
        except Exception as error:  # noqa: BLE001 - never expose a raw traceback
            logger.warning("Answer chain failed: %s", type(error).__name__)
            raise AnswerGenerationError(
                "The answer could not be generated. Check logs/app.log for the technical "
                "details and try again.",
                error_type="provider",
            ) from error


def _strip_unknown_inline_references(
    answer: str, valid_refs: set[str]
) -> tuple[str, set[str]]:
    """Remove ``[S9]``-style markers that do not resolve to a supplied passage."""
    removed: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        reference = normalize_reference(match.group(1))
        if reference in valid_refs:
            return f"[{reference}]"
        removed.add(reference)
        return ""

    cleaned = INLINE_REFERENCE.sub(replace, answer)
    if removed:
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, removed


def _friendly_message(error_type: str) -> str:
    """Translate a provider error type into an actionable sentence."""
    if error_type in {"configuration", "authentication"}:
        return (
            "The NVIDIA provider rejected the configured credentials. Check NVIDIA_API_KEY "
            "in your local .env file, then restart the application."
        )
    if error_type == "rate_limit":
        return "The NVIDIA service is rate limiting this key. Wait a moment and try again."
    if error_type == "timeout":
        return (
            "The NVIDIA request timed out before an answer was ready. Try a shorter question "
            "or raise RAG_TIMEOUT_SECONDS."
        )
    if error_type == "connection":
        return (
            "The NVIDIA endpoint could not be reached. Check your internet connection and "
            "NVIDIA_BASE_URL, then try again."
        )
    if error_type == "truncated":
        return (
            "The model ran out of its output budget before finishing the answer. Raise "
            "RAG_ANSWER_MAX_TOKENS in your local .env file and try again."
        )
    if error_type == "empty_response":
        return "The model returned an empty answer. Try asking again."
    return (
        "The NVIDIA request failed while generating the answer. Check logs/app.log for the "
        "technical details and try again."
    )
