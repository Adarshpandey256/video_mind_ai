"""NVIDIA (OpenAI-compatible) client factory and error types.

The provider is reached through the official ``openai`` SDK pointed at NVIDIA's
compatible endpoint. This module owns client creation, caching and the exception
hierarchy that the rest of the application maps onto friendly messages.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from openai import OpenAI

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MISSING_CREDENTIALS_MESSAGE = (
    "NVIDIA credentials are not configured. Add NVIDIA_API_KEY to your local "
    ".env file and restart the application."
)


class NvidiaError(RuntimeError):
    """Base class for all NVIDIA related failures."""


class NvidiaConfigurationError(NvidiaError):
    """Raised when the local provider configuration is incomplete."""


class NvidiaRequestError(NvidiaError):
    """Raised when the provider request fails (network, provider or response)."""


class NvidiaAuthenticationError(NvidiaRequestError):
    """Raised when the provider rejects the configured credentials."""


class NvidiaRateLimitError(NvidiaRequestError):
    """Raised when the provider rate limits the request."""


class NvidiaTimeoutError(NvidiaRequestError):
    """Raised when the provider request exceeds the configured timeout."""


class NvidiaConnectionError(NvidiaRequestError):
    """Raised when the provider endpoint cannot be reached."""


class NvidiaModelError(NvidiaRequestError):
    """Raised when the configured model or a request parameter is rejected."""


class NvidiaResponseError(NvidiaRequestError):
    """Raised when the provider response is malformed or empty."""


class NvidiaOutputTruncatedError(NvidiaResponseError):
    """Raised when the provider spent the whole budget without returning an answer.

    A separate type because the caller that set the budget is the only one that can
    name the right setting: Step 1 sizes its own requests with ``LLM_MAX_TOKENS``,
    while Step 3 sizes summarisation with ``SUMMARY_MAX_TOKENS``.
    """


def classify_error_type(error: BaseException) -> str:
    """Map a provider exception onto a stable, non-sensitive error type.

    Shared by every feature that calls the model, so a rate limit is reported as a
    rate limit and not as a generic failure. The truncation check comes first
    because :class:`NvidiaOutputTruncatedError` is also a response error.
    """
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
    if isinstance(error, NvidiaOutputTruncatedError):
        return "truncated"
    if isinstance(error, NvidiaResponseError):
        return "empty_response"
    return "provider"


def create_nvidia_client(settings: Settings | None = None) -> OpenAI:
    """Create an OpenAI-compatible client for the NVIDIA endpoint.

    The API key is read from the configuration only; it is never passed to the UI
    and never logged.
    """
    resolved = settings or get_settings()
    api_key = resolved.get_nvidia_api_key()

    if not api_key:
        raise NvidiaConfigurationError(MISSING_CREDENTIALS_MESSAGE)

    client = OpenAI(
        base_url=resolved.nvidia_base_url,
        api_key=api_key,
        timeout=resolved.nvidia_timeout_seconds,
        max_retries=resolved.nvidia_max_retries,
    )
    logger.info(
        "NVIDIA client created (endpoint=%s, model=%s, timeout=%ss, max_retries=%s)",
        resolved.nvidia_base_url,
        resolved.nvidia_model,
        resolved.nvidia_timeout_seconds,
        resolved.nvidia_max_retries,
    )
    return client


@lru_cache(maxsize=1)
def get_nvidia_client() -> OpenAI:
    """Return a cached client so Streamlit reruns do not rebuild it."""
    return create_nvidia_client(get_settings())


def reset_nvidia_client_cache() -> None:
    """Drop the cached client (used by tests and after a configuration change)."""
    get_nvidia_client.cache_clear()


def is_nvidia_configured(settings: Settings | None = None) -> bool:
    """True when credentials are present, without creating a client."""
    return (settings or get_settings()).has_nvidia_credentials


def describe_client_config(settings: Settings | None = None) -> dict[str, Any]:
    """Return a non-secret description of the provider configuration."""
    resolved = settings or get_settings()
    return {
        "base_url": resolved.nvidia_base_url,
        "model": resolved.nvidia_model,
        "timeout_seconds": resolved.nvidia_timeout_seconds,
        "max_retries": resolved.nvidia_max_retries,
        "api_key_configured": resolved.has_nvidia_credentials,
    }
