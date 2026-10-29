"""Centralised logging configuration with secret redaction."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_NAME = "app.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

#: Third-party loggers that are noisy at INFO level.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "openai", "asyncio", "watchdog")

_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"nvapi-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([^\s\"',}]+)"),
)

_configured = False


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Remove API keys and authorisation headers from a log message.

    ``extra_secrets`` allows callers to hide known values even when they do not
    match the built-in patterns.
    """
    if not isinstance(text, str):
        text = str(text)

    redacted = text
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}***REDACTED***" if match.groups() else "***REDACTED***", redacted)

    for secret in extra_secrets:
        if isinstance(secret, str) and len(secret) >= 8:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


class RedactionFilter(logging.Filter):
    """Logging filter that redacts secrets from record messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: never break logging
            return True
        record.msg = redact_secrets(message)
        record.args = ()
        return True


def build_formatter() -> logging.Formatter:
    """Return the shared log formatter."""
    return logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


def configure_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    *,
    debug: bool = False,
    force: bool = False,
) -> Path | None:
    """Configure console and rotating file logging.

    Returns the log file path, or ``None`` when file logging is unavailable
    (for example when the directory cannot be created). Repeated calls are
    ignored unless ``force`` is true, so Streamlit reruns stay cheap.
    """
    global _configured

    resolved_level = getattr(logging, str(level).upper(), logging.INFO)
    if _configured and not force:
        return None

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = build_formatter()
    redaction = RedactionFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(resolved_level)
    console.addFilter(redaction)
    root.addHandler(console)

    log_path: Path | None = None
    if log_dir is not None:
        try:
            resolved_dir = Path(log_dir)
            resolved_dir.mkdir(parents=True, exist_ok=True)
            log_path = resolved_dir / LOG_FILE_NAME
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=MAX_LOG_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            # Local development logs keep extra detail; the UI still shows safe messages.
            file_handler.setLevel(logging.DEBUG if debug else resolved_level)
            file_handler.addFilter(redaction)
            root.addHandler(file_handler)
        except OSError:  # pragma: no cover - depends on the local filesystem
            log_path = None
            logging.getLogger(__name__).warning(
                "File logging is unavailable; continuing with console logging only."
            )

    root.setLevel(logging.DEBUG if debug else resolved_level)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Return a module level logger."""
    return logging.getLogger(name)
