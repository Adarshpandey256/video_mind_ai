"""Whisper speech-to-text (Step 2).

This uses the **local** openai-whisper implementation, not a hosted transcription
API. Weights are loaded lazily on first use and cached per ``(model, device)``, so
Streamlit reruns never reload a model and no model is downloaded at application
startup.

CPU always works. CUDA is used only when PyTorch actually reports it, so the
application never claims GPU acceleration that is not available.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.settings import WHISPER_MODEL_CHOICES, Settings, get_settings
from src.models.schemas import (
    TranscriptSegment,
    TranscriptionOptions,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

VALID_DEVICES = ("cpu", "cuda")

TRANSCRIPTION_PROGRESS_STAGES = (
    "Loading Whisper model",
    "Transcribing audio",
    "Validating transcript",
)


class TranscriptionServiceError(RuntimeError):
    """Base class for transcription failures."""


class WhisperUnavailableError(TranscriptionServiceError):
    """Raised when openai-whisper (or its PyTorch backend) is not installed."""


class ModelLoadError(TranscriptionServiceError):
    """Raised when the Whisper checkpoint cannot be loaded."""


class UnknownLanguageError(TranscriptionServiceError):
    """Raised when a requested language is not supported by Whisper."""


class EmptyTranscriptError(TranscriptionServiceError):
    """Raised when no speech was detected in the audio."""


class InvalidTranscriptionError(TranscriptionServiceError):
    """Raised when the model output cannot be turned into a valid transcript."""


class TranscriptionFailedError(TranscriptionServiceError):
    """Raised when the transcription itself fails."""


#: Process-wide model cache: loading weights is expensive, and this application is a
#: single-user local tool. Keyed by (model, device) so different devices never clash.
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = threading.Lock()
_LANGUAGE_CACHE: dict[str, str] | None = None


def unload_whisper_models() -> None:
    """Drop cached models (frees memory without restarting the application)."""
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()


@lru_cache(maxsize=1)
def cuda_available_cached() -> bool:
    """Probe for CUDA once per process - importing torch is expensive."""
    return TranscriptionService().cuda_available()


def whispered_language_names() -> dict[str, str]:
    """Return Whisper's supported language codes and names."""
    global _LANGUAGE_CACHE
    if _LANGUAGE_CACHE is None:
        try:
            from whisper.tokenizer import LANGUAGES  # type: ignore[import-not-found]

            _LANGUAGE_CACHE = dict(LANGUAGES)
        except Exception:  # noqa: BLE001 - whisper may be missing or partially installed
            _LANGUAGE_CACHE = {}
    return _LANGUAGE_CACHE


class TranscriptionService:
    """Transcribe audio files with a lazily loaded Whisper model."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model_factory: Callable[[str, str], Any] | None = None,
        cuda_available: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._model_factory = model_factory
        self._cuda_available = cuda_available

    # ------------------------------------------------------------------ availability
    @property
    def settings(self) -> Settings:
        """Return the settings used by this service."""
        return self._settings

    @staticmethod
    def is_installed() -> bool:
        """True when the whisper package can be imported."""
        import importlib.util

        return importlib.util.find_spec("whisper") is not None

    def torch_available(self) -> bool:
        """True when PyTorch is importable."""
        import importlib.util

        return importlib.util.find_spec("torch") is not None

    def cuda_available(self) -> bool:
        """True only when PyTorch reports a usable CUDA device."""
        if self._cuda_available is not None:
            return bool(self._cuda_available())
        try:
            import torch  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 - missing or broken torch means "no GPU"
            return False
        try:
            return bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            return False

    def resolve_device(self, requested: str | None = None) -> str:
        """Resolve the device to use, falling back to CPU honestly.

        ``auto`` prefers CUDA when PyTorch reports it. Explicitly requesting
        ``cuda`` on a machine without CUDA logs a warning and uses CPU rather than
        failing, so transcription always remains possible.
        """
        preference = (requested or self._settings.whisper_device or "auto").strip().lower()
        if preference not in {"auto", "cpu", "cuda"}:
            preference = "auto"

        if preference == "cpu":
            return "cpu"

        has_cuda = self.cuda_available()
        if preference == "cuda":
            if has_cuda:
                return "cuda"
            logger.warning(
                "WHISPER_DEVICE=cuda was requested but PyTorch reports no CUDA device; "
                "falling back to CPU (transcription will be slower)."
            )
            return "cpu"
        return "cuda" if has_cuda else "cpu"

    # ------------------------------------------------------------------ model loading
    def load_model(self, model_name: str, device: str) -> Any:
        """Load (or reuse) a Whisper checkpoint."""
        if model_name not in WHISPER_MODEL_CHOICES:
            raise ModelLoadError(
                f"'{model_name}' is not a known Whisper model. Choose one of: "
                f"{', '.join(WHISPER_MODEL_CHOICES)}."
            )

        key = (model_name, device)
        with _MODEL_LOCK:
            cached = _MODEL_CACHE.get(key)
            if cached is not None:
                return cached

        if self._model_factory is not None:
            model = self._model_factory(model_name, device)
        else:
            try:
                import whisper  # type: ignore[import-not-found]
            except ImportError as error:
                raise WhisperUnavailableError(
                    "openai-whisper is not installed, so transcription is unavailable. Run "
                    "'pip install -r requirements.txt' inside the virtual environment."
                ) from error
            try:
                model = whisper.load_model(model_name, device=device)
            except MemoryError as error:
                raise ModelLoadError(
                    f"Not enough memory to load the Whisper model '{model_name}'. Try a smaller "
                    "model such as 'tiny' or 'base' (WHISPER_MODEL)."
                ) from error
            except Exception as error:  # noqa: BLE001 - download/IO/format failures
                logger.info("Whisper model load failed: %s", type(error).__name__)
                raise ModelLoadError(
                    f"The Whisper model '{model_name}' could not be loaded. Check that the "
                    "download completed and that you have enough free disk space and memory."
                ) from error

        with _MODEL_LOCK:
            _MODEL_CACHE[key] = model
        logger.info("Loaded Whisper model '%s' on %s", model_name, device)
        return model

    # ------------------------------------------------------------------ language
    def validate_language(self, language: str | None) -> str | None:
        """Validate an optional language code against Whisper's supported list."""
        if not language:
            return None
        code = str(language).strip().lower()
        if not code or code == "auto":
            return None
        languages = whispered_language_names()
        if languages and code not in languages:
            raise UnknownLanguageError(
                f"'{code}' is not a language supported by Whisper. Leave WHISPER_LANGUAGE "
                "empty for automatic detection."
            )
        return code

    # ------------------------------------------------------------------ transcription
    def transcribe(
        self,
        audio_path: Path | str,
        options: TranscriptionOptions | None = None,
        *,
        progress: Callable[[str], None] | None = None,
        audio_duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file and return a validated result."""
        resolved_options = options or TranscriptionOptions()
        audio = Path(audio_path)
        if not audio.is_file():
            raise TranscriptionServiceError("The prepared audio file could not be found.")

        model_name = (resolved_options.model_name or self._settings.whisper_model).strip().lower()
        device = self.resolve_device(resolved_options.device)
        language = self.validate_language(
            resolved_options.language if resolved_options.language is not None
            else self._settings.whisper_language
        )

        _notify(progress, f"Loading Whisper model '{model_name}' (device: {device})...")
        model = self.load_model(model_name, device)

        _notify(
            progress,
            "Transcribing audio"
            + (f" (language: {language})" if language else " (automatic language detection)")
            + "...",
        )
        started = time.perf_counter()
        try:
            raw = model.transcribe(
                str(audio),
                language=language,
                task="transcribe",
                fp16=(device == "cuda"),
                verbose=False,
            )
        except MemoryError as error:
            raise TranscriptionFailedError(
                "Transcription ran out of memory. Try a smaller WHISPER_MODEL such as 'tiny' or "
                "'base', or close other applications."
            ) from error
        except Exception as error:  # noqa: BLE001 - any backend failure becomes a safe error
            logger.info("Whisper transcription failed: %s", type(error).__name__)
            raise TranscriptionFailedError(
                "Transcription failed while processing the audio. Try again, or use a smaller "
                "Whisper model."
            ) from error
        elapsed = round(time.perf_counter() - started, 2)

        _notify(progress, "Validating transcript...")
        return self._build_result(
            raw,
            model_name=model_name,
            device=device,
            requested_language=language,
            processing_seconds=elapsed,
            audio_duration_seconds=audio_duration_seconds,
        )

    # ------------------------------------------------------------------ internals
    def _build_result(
        self,
        raw: Any,
        *,
        model_name: str,
        device: str,
        requested_language: str | None,
        processing_seconds: float,
        audio_duration_seconds: float | None,
    ) -> TranscriptionResult:
        """Validate raw Whisper output and build the typed result."""
        if not isinstance(raw, dict):
            raise InvalidTranscriptionError("The transcription backend returned an unexpected result.")

        segments = self._validate_segments(raw.get("segments"))
        raw_text = str(raw.get("text") or "").strip()

        full_text = " ".join(segment.text for segment in segments).strip() or raw_text
        if not full_text:
            raise EmptyTranscriptError(
                "No speech was detected in this recording. Check that the file contains audible "
                "speech, or select the language manually if automatic detection failed."
            )

        detected = raw.get("language")
        detected_language = str(detected) if isinstance(detected, str) and detected else None

        duration = audio_duration_seconds
        if duration is None:
            ends = [segment.end_seconds for segment in segments if segment.end_seconds is not None]
            duration = max(ends) if ends else None

        return TranscriptionResult(
            text=full_text,
            segments=segments,
            language=detected_language or requested_language,
            model_name=model_name,
            device=device,
            duration_seconds=duration,
            processing_seconds=processing_seconds,
        )

    def _validate_segments(self, raw_segments: Any) -> list[TranscriptSegment]:
        """Convert raw segments into validated, chronologically ordered segments."""
        if raw_segments is None:
            return []
        if not isinstance(raw_segments, list):
            raise InvalidTranscriptionError("The transcript segments could not be read.")

        collected: list[tuple[float | None, float | None, str]] = []
        skipped = 0
        for entry in raw_segments:
            if isinstance(entry, dict):
                text = str(entry.get("text") or "").strip()
                start = _optional_float(entry.get("start"))
                end = _optional_float(entry.get("end"))
            else:
                text = str(getattr(entry, "text", "") or "").strip()
                start = _optional_float(getattr(entry, "start", None))
                end = _optional_float(getattr(entry, "end", None))

            if not text:
                skipped += 1
                continue

            # Missing timestamps stay unavailable instead of being invented.
            if start is None or end is None:
                start, end = None, None
            else:
                if start < 0 or end < 0:
                    raise InvalidTranscriptionError(
                        "The transcript contained a negative timestamp and was rejected."
                    )
                if end < start:
                    raise InvalidTranscriptionError(
                        "The transcript contained inconsistent segment timestamps and was rejected."
                    )
            collected.append((start, end, text))

        ordered = sorted(collected, key=lambda item: (item[0] is None, item[0] or 0.0))
        if ordered != collected:
            logger.warning("Transcript segments were reordered chronologically before storing.")

        if skipped:
            logger.info("Skipped %s empty transcript segment(s).", skipped)

        return [
            TranscriptSegment(
                segment_index=index,
                start_seconds=start,
                end_seconds=end,
                text=text,
            )
            for index, (start, end, text) in enumerate(ordered)
        ]


def _optional_float(value: Any) -> float | None:
    """Convert a value to a float, returning ``None`` when it is not numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
