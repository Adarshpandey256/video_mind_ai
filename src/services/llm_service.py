"""Language model service built on the NVIDIA-hosted Nemotron models.

All provider calls go through :class:`LLMService`, which validates
configuration, applies generation defaults, adapts to parameters the model does
not support and converts provider exceptions into safe, understandable errors.

Only the answer content returned by the provider is exposed. Reasoning traces
(``reasoning_content``) are deliberately ignored.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from config.settings import Settings, get_settings
from src.models.schemas import ConnectionTestResult, LLMResponse
from src.services.nvidia_client import (
    MISSING_CREDENTIALS_MESSAGE,
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
    get_nvidia_client,
)

logger = logging.getLogger(__name__)

CONNECTION_TEST_PROMPT = "Reply with exactly: ready"
CONNECTION_TEST_SYSTEM_PROMPT = (
    "You are a connectivity probe for VideoMind AI. Reply with the single word: ready"
)
#: The smoke test needs real headroom: a reasoning model spends part of the budget
#: on private reasoning before it emits any answer text, so a tiny limit made such
#: models look unreachable even though the connection was fine.
CONNECTION_TEST_MAX_TOKENS = 512
CONNECTION_TEST_TIMEOUT_SECONDS = 30.0
MAX_PREVIEW_CHARS = 200

_TOKEN_PARAMETER_HINTS = (
    "unsupported",
    "not supported",
    "does not support",
    "unknown",
    "invalid",
    "unrecognized",
)

_MAX_TOKENS_PATTERN = re.compile(r"\bmax_tokens\b")
_TEMPERATURE_PATTERN = re.compile(r"\btemperature\b")


class LLMService:
    """Thin, testable wrapper around the OpenAI-compatible chat completions API."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._token_parameter: str = self._settings.llm_token_parameter
        self._temperature_supported = True

    # ------------------------------------------------------------------ properties
    @property
    def settings(self) -> Settings:
        """Return the settings used by this service."""
        return self._settings

    @property
    def token_parameter(self) -> str:
        """Name of the chat-completion parameter currently used for the token limit."""
        return self._token_parameter

    @property
    def temperature_supported(self) -> bool:
        """False once the model reports that ``temperature`` is not supported."""
        return self._temperature_supported

    def is_configured(self) -> bool:
        """True when credentials are available for provider requests."""
        return self._settings.has_nvidia_credentials

    # ------------------------------------------------------------------ requests
    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Run a single non-streaming chat completion.

        Raises :class:`NvidiaConfigurationError` when credentials are missing and
        :class:`NvidiaRequestError` (or a subclass) when the provider fails.
        """
        self._ensure_configured()
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        resolved_model = model or self._settings.nvidia_model
        resolved_temperature = (
            self._settings.llm_temperature if temperature is None else float(temperature)
        )
        resolved_max_tokens = (
            self._settings.llm_max_tokens if max_tokens is None else int(max_tokens)
        )
        resolved_timeout = (
            self._settings.nvidia_timeout_seconds if timeout is None else float(timeout)
        )

        started = time.perf_counter()
        response = self._create_completion(
            model=resolved_model,
            messages=self._build_messages(prompt, system_prompt),
            temperature=resolved_temperature,
            max_tokens=resolved_max_tokens,
            timeout=resolved_timeout,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        text = self._extract_text(response)
        result = LLMResponse(
            text=text,
            model=getattr(response, "model", None) or resolved_model,
            finish_reason=self._finish_reason(response),
            latency_ms=latency_ms,
            **self._usage_from(response),
        )
        logger.info(
            "Chat completion succeeded (model=%s, latency_ms=%s, total_tokens=%s)",
            result.model,
            result.latency_ms,
            result.total_tokens,
        )
        return result

    def stream_generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        """Yield answer text chunks from a streaming chat completion.

        Optional and not required by the Step 1 interface; kept for later steps.
        Provider failures are raised as :class:`NvidiaRequestError` while iterating.
        """
        self._ensure_configured()
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")

        params: dict[str, Any] = {
            "model": model or self._settings.nvidia_model,
            "messages": self._build_messages(prompt, system_prompt),
            self._token_parameter: (
                self._settings.llm_max_tokens if max_tokens is None else int(max_tokens)
            ),
            "timeout": (
                self._settings.nvidia_timeout_seconds if timeout is None else float(timeout)
            ),
            "stream": True,
        }
        if self._temperature_supported:
            params["temperature"] = (
                self._settings.llm_temperature if temperature is None else float(temperature)
            )

        try:
            stream = self._get_client().chat.completions.create(**params)
            for chunk in stream:
                delta = self._content_to_text(self._choice_attr(chunk, "delta"))
                if delta:
                    yield delta
        except NvidiaError:
            raise
        except Exception as error:  # noqa: BLE001 - translated into a safe error below
            raise self._translate_error(error) from error

    def test_connection(self, *, timeout: float | None = None) -> ConnectionTestResult:
        """Send one short request to verify the provider connection.

        Never raises for provider or configuration problems: the outcome is
        reported as a :class:`ConnectionTestResult` so the UI can display it.
        """
        model = self._settings.nvidia_model
        base_url = self._settings.nvidia_base_url

        if not self.is_configured():
            return ConnectionTestResult(
                ok=False,
                message=MISSING_CREDENTIALS_MESSAGE,
                model=model,
                base_url=base_url,
                error_type="configuration",
            )

        try:
            response = self.generate(
                CONNECTION_TEST_PROMPT,
                system_prompt=CONNECTION_TEST_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=CONNECTION_TEST_MAX_TOKENS,
                timeout=timeout or min(
                    self._settings.nvidia_timeout_seconds, CONNECTION_TEST_TIMEOUT_SECONDS
                ),
            )
        except NvidiaConfigurationError as error:
            return ConnectionTestResult(
                ok=False,
                message=str(error),
                model=model,
                base_url=base_url,
                error_type="configuration",
            )
        except NvidiaError as error:
            logger.warning("NVIDIA connection test failed: %s", type(error).__name__)
            # Only connectivity problems justify blaming the network. A response the
            # provider shaped differently (for example a token limit spent entirely on
            # reasoning) already carries an accurate, actionable message.
            hint = ""
            if isinstance(error, (NvidiaConnectionError, NvidiaTimeoutError)):
                hint = (
                    " Check your network connection, the model name "
                    f"(NVIDIA_MODEL={model}) and the endpoint (NVIDIA_BASE_URL={base_url})."
                )
            return ConnectionTestResult(
                ok=False,
                message=f"{error}{hint}",
                model=model,
                base_url=base_url,
                error_type="request",
            )

        return ConnectionTestResult(
            ok=True,
            message=f"Connected to {response.model} successfully.",
            model=response.model,
            base_url=base_url,
            latency_ms=response.latency_ms,
            response_preview=response.text[:MAX_PREVIEW_CHARS],
            error_type=None,
        )

    # ------------------------------------------------------------------ internals
    def _ensure_configured(self) -> None:
        if not self.is_configured():
            raise NvidiaConfigurationError(MISSING_CREDENTIALS_MESSAGE)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = get_nvidia_client()
        return self._client

    def _build_messages(
        self, prompt: str, system_prompt: str | None
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt or self._settings.llm_system_prompt},
            {"role": "user", "content": prompt.strip()},
        ]

    def _create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> Any:
        """Send the request, adapting to parameters the model does not support."""
        client = self._get_client()
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            self._token_parameter: max_tokens,
            "timeout": timeout,
        }
        if self._temperature_supported:
            params["temperature"] = temperature

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return client.chat.completions.create(**params)
            except BadRequestError as error:
                last_error = error
                if attempt == 0 and self._apply_parameter_fallback(params, error):
                    continue
                raise self._translate_error(error) from error
            except NvidiaError:
                raise
            except Exception as error:  # noqa: BLE001 - translated into a safe error
                raise self._translate_error(error) from error

        raise self._translate_error(last_error or RuntimeError("request failed"))

    def _apply_parameter_fallback(self, params: dict[str, Any], error: Exception) -> bool:
        """Adjust request parameters rejected by the model; True when retrying."""
        message = str(error).lower()

        if (
            self._token_parameter in params
            and self._token_parameter == "max_tokens"
            and _MAX_TOKENS_PATTERN.search(message)
            and any(hint in message for hint in _TOKEN_PARAMETER_HINTS)
        ):
            params["max_completion_tokens"] = params.pop("max_tokens")
            self._token_parameter = "max_completion_tokens"
            logger.warning(
                "The configured model rejected 'max_tokens'; retrying with 'max_completion_tokens'."
            )
            return True

        if (
            "temperature" in params
            and _TEMPERATURE_PATTERN.search(message)
            and any(hint in message for hint in _TOKEN_PARAMETER_HINTS)
        ):
            params.pop("temperature", None)
            self._temperature_supported = False
            logger.warning(
                "The configured model rejected 'temperature'; retrying without it."
            )
            return True

        return False

    def _translate_error(self, error: Exception) -> NvidiaError:
        """Convert a provider exception into a safe application error."""
        if isinstance(error, NvidiaError):
            return error

        if isinstance(error, (AuthenticationError, PermissionDeniedError)):
            return NvidiaAuthenticationError(
                "NVIDIA rejected the supplied API key. Create a new key in your NVIDIA "
                "account and update NVIDIA_API_KEY in your local .env file."
            )
        if isinstance(error, RateLimitError):
            return NvidiaRateLimitError(
                "The NVIDIA rate limit was reached. Wait a moment and try again."
            )
        if isinstance(error, APITimeoutError):
            return NvidiaTimeoutError(
                "The NVIDIA request timed out. Check your connection and increase "
                "NVIDIA_TIMEOUT_SECONDS if needed."
            )
        if isinstance(error, APIConnectionError):
            return NvidiaConnectionError(
                "Could not reach the NVIDIA endpoint. Check your internet connection, "
                "any proxy settings and NVIDIA_BASE_URL."
            )
        if isinstance(error, NotFoundError):
            return NvidiaModelError(
                "The configured model is not available at this endpoint "
                f"(HTTP {getattr(error, 'status_code', 'unknown')}). "
                "Check NVIDIA_MODEL in your local .env file."
            )
        if isinstance(error, APIStatusError):
            return NvidiaRequestError(
                "The NVIDIA endpoint returned an error "
                f"(HTTP {getattr(error, 'status_code', 'unknown')}). Please try again."
            )

        logger.debug("Unexpected provider error: %s", type(error).__name__)
        return NvidiaRequestError(
            "An unexpected error occurred while contacting NVIDIA. See the local logs "
            "for details."
        )

    @staticmethod
    def _choice_attr(response: Any, attribute: str) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        return getattr(choices[0], attribute, None)

    @classmethod
    def _content_to_text(cls, container: Any) -> str:
        """Extract plain answer text from a message, delta or content part list.

        Only ``content`` is read; provider specific reasoning fields are ignored
        so private reasoning is never displayed.
        """
        if container is None:
            return ""
        content = getattr(container, "content", container)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or ""
                else:
                    text = getattr(part, "text", "") or ""
                if text:
                    parts.append(str(text))
            return "".join(parts)
        return ""

    @classmethod
    def _extract_text(cls, response: Any) -> str:
        message = cls._choice_attr(response, "message")
        text = cls._content_to_text(message).strip()
        if text:
            return text
        if cls._finish_reason(response) == "length":
            raise NvidiaOutputTruncatedError(
                "The model used its whole token budget before returning any answer. "
                "Reasoning models spend part of that budget on private reasoning, so "
                "raise LLM_MAX_TOKENS in your local .env file and try again."
            )
        raise NvidiaResponseError(
            "The provider returned an empty answer. Try again, or verify the "
            "configured model with Settings."
        )

    @staticmethod
    def _finish_reason(response: Any) -> str | None:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        reason = getattr(choices[0], "finish_reason", None)
        return str(reason) if reason else None

    @staticmethod
    def _usage_from(response: Any) -> dict[str, int | None]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
