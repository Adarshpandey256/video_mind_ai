"""Bounded, validated JSON requests to the configured NVIDIA model (Step 3).

Every summarisation request goes through :class:`SummaryJsonClient`, which:

* reuses the Step 1 :class:`LLMService` (one client factory, one configuration),
* asks for JSON explicitly and validates it locally, instead of assuming that the
  endpoint supports response formatting,
* retries a bounded number of times, first re-asking for the same content as pure
  JSON and then, for retryable provider failures, with exponential backoff,
* never puts an API key, header or configuration value into a prompt,
* never exposes private reasoning: only ``LLMService`` answer text is read.

Failures are raised as :class:`SummaryGenerationError` carrying a machine readable
``error_type`` so the caller can report the real cause instead of guessing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from config.settings import Settings, get_settings
from src.models.schemas import LLMResponse
from src.prompts.summary_prompts import build_repair_prompt
from src.services.llm_service import LLMService
from src.services.nvidia_client import (
    NvidiaAuthenticationError,
    NvidiaConfigurationError,
    NvidiaConnectionError,
    NvidiaError,
    NvidiaModelError,
    NvidiaOutputTruncatedError,
    NvidiaRateLimitError,
    NvidiaRequestError,
    NvidiaResponseError,
    NvidiaTimeoutError,
)
from src.utils.json_utils import ModelOutputError, extract_json_object

logger = logging.getLogger(__name__)

#: Never retry a parse/validation failure more than this many times beyond the first ask.
MAX_JSON_ATTEMPTS = 3
#: Backoff base for retryable provider failures; attempts are capped by settings.
BACKOFF_BASE_SECONDS = 2.0


def _error_type_for(error: Exception) -> str:
    """Map a provider exception onto a stable, non-sensitive error type."""
    if isinstance(error, NvidiaConfigurationError):
        return "configuration"
    if isinstance(error, NvidiaAuthenticationError):
        return "authentication"
    if isinstance(error, NvidiaRateLimitError):
        return "rate_limit"
    if isinstance(error, NvidiaTimeoutError):
        return "timeout"
    if isinstance(error, NvidiaConnectionError):
        return "connection"
    if isinstance(error, NvidiaModelError):
        return "model"
    # Checked before NvidiaResponseError: the budget-exhausted case is a truncation,
    # and only this caller can name the setting that sizes a summarisation request.
    if isinstance(error, NvidiaOutputTruncatedError):
        return "truncated"
    if isinstance(error, NvidiaResponseError):
        return "empty_response"
    if isinstance(error, NvidiaError):
        return "provider"
    return "provider"


def _retryable(error_type: str) -> bool:
    """True when waiting and trying again can plausibly succeed."""
    return error_type in {"rate_limit", "timeout", "connection", "provider"}


@dataclass
class JsonResponse:
    """One successfully parsed model answer."""

    data: dict[str, Any]
    response: LLMResponse
    attempts: int = 1


class SummaryGenerationError(RuntimeError):
    """A summarisation model request that could not produce usable JSON."""

    def __init__(self, message: str, *, error_type: str = "provider") -> None:
        super().__init__(message)
        self.error_type = error_type

    @property
    def retryable(self) -> bool:
        """True when the failure is likely to be transient."""
        return _retryable(self.error_type)


class SummaryJsonClient:
    """Ask the model for JSON and validate it locally."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm_service or LLMService(self._settings)

    @property
    def settings(self) -> Settings:
        """Return the settings used for requests."""
        return self._settings

    @property
    def llm(self) -> LLMService:
        """Return the underlying Step 1 LLM service."""
        return self._llm

    def is_configured(self) -> bool:
        """True when credentials exist, so a request can be attempted."""
        return self._llm.is_configured()

    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        stage: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> JsonResponse:
        """Ask for a JSON object, retrying within the configured bounds.

        Raises :class:`SummaryGenerationError` with an ``error_type`` on failure.
        """
        if not self.is_configured():
            raise SummaryGenerationError(
                "No NVIDIA API key is configured, so no summary can be generated.",
                error_type="configuration",
            )

        token_budget = max_tokens or self._settings.summary_max_tokens
        resolved_temperature = (
            self._settings.summary_temperature if temperature is None else float(temperature)
        )
        retries = max(0, min(self._settings.summary_max_retries, 3))

        prompt = user_prompt
        attempts = 0
        last_error: SummaryGenerationError | None = None
        provider_failures = 0

        while attempts < MAX_JSON_ATTEMPTS:
            attempts += 1
            started = time.perf_counter()
            try:
                response = self._llm.generate(
                    prompt,
                    system_prompt=system_prompt,
                    temperature=resolved_temperature,
                    max_tokens=token_budget,
                    timeout=self._settings.summary_timeout_seconds,
                )
            except NvidiaError as error:
                error_type = _error_type_for(error)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                logger.warning(
                    "Summarisation request failed (stage=%s, type=%s, elapsed_ms=%s)",
                    stage,
                    error_type,
                    elapsed_ms,
                )
                last_error = SummaryGenerationError(str(error), error_type=error_type)
                if not last_error.retryable or provider_failures >= retries:
                    raise last_error from error
                provider_failures += 1
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** (provider_failures - 1)))
                continue

            logger.info(
                "Summarisation request completed (stage=%s, attempt=%s, latency_ms=%s, finish_reason=%s)",
                stage,
                attempts,
                response.latency_ms,
                response.finish_reason,
            )

            if response.finish_reason == "length":
                raise SummaryGenerationError(
                    "The model ran out of its token budget before finishing this "
                    f"summary{_truncation_hint(stage)}. Raise SUMMARY_MAX_TOKENS and try again.",
                    error_type="truncated",
                )

            try:
                data = extract_json_object(response.text)
            except ModelOutputError as error:
                logger.warning(
                    "Model response was not usable JSON (stage=%s, attempt=%s)",
                    stage,
                    attempts,
                )
                if attempts >= MAX_JSON_ATTEMPTS:
                    raise SummaryGenerationError(
                        "The model did not return a structured summary that could be read. "
                        "Try again, or switch to a different summary style.",
                        error_type="invalid_output",
                    ) from error
                prompt = build_repair_prompt(response.text, str(error))
                continue

            return JsonResponse(data=data, response=response, attempts=attempts)

        raise last_error or SummaryGenerationError(
            "The summarisation request could not be completed.", error_type="provider"
        )


def _truncation_hint(stage: str) -> str:
    """Explain which budget was exhausted, using the stage name."""
    if stage == "chunk":
        return " for one transcript section (reduce SUMMARY_CHUNK_MAX_TOKENS so sections are smaller)"
    if stage == "merge":
        return " while merging the section summaries (raise SUMMARY_MERGE_MAX_TOKENS)"
    return ""
