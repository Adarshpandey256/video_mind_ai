"""Media inspection and audio extraction through FFmpeg (Step 2).

FFmpeg and ffprobe are external executables. All invocations use explicit
argument lists with ``shell=False``, a timeout, captured diagnostics and no
shell interpolation of user supplied values.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from config.settings import Settings, get_settings
from src.models.schemas import MediaInfo

logger = logging.getLogger(__name__)

#: Whisper resamples internally to 16 kHz mono, so we normalise once, up front.
TARGET_SAMPLE_RATE_HZ = 16000
TARGET_CHANNELS = 1
EXTRACTED_AUDIO_SUFFIX = ".wav"
MAX_DIAGNOSTIC_CHARS = 400

FFMPEG_SETUP_MESSAGE = (
    "FFmpeg was not found. Install it with 'winget install Gyan.FFmpeg', reopen the "
    "terminal and restart the application. FFmpeg is required to read media files."
)


class MediaToolingError(RuntimeError):
    """Raised when FFmpeg/ffprobe is missing or cannot be executed."""


class MediaInspectionError(RuntimeError):
    """Raised when a media file cannot be inspected."""


class MediaDurationError(MediaInspectionError):
    """Raised when a media file is longer than the configured limit."""


class AudioExtractionError(RuntimeError):
    """Raised when the audio track cannot be extracted."""


class NoAudioStreamError(AudioExtractionError):
    """Raised when the media file contains no audio track."""


class MediaProcessingTimeout(AudioExtractionError):
    """Raised when FFmpeg exceeds the configured processing timeout."""


@dataclass(frozen=True)
class MediaTooling:
    """Availability of the external media executables."""

    ffmpeg: str | None
    ffprobe: str | None

    @property
    def available(self) -> bool:
        """True when both executables were found on PATH."""
        return bool(self.ffmpeg and self.ffprobe)


@lru_cache(maxsize=8)
def _locate(ffmpeg_binary: str, ffprobe_binary: str) -> MediaTooling:
    return MediaTooling(
        ffmpeg=shutil.which(ffmpeg_binary),
        ffprobe=shutil.which(ffprobe_binary),
    )


def detect_media_tooling(settings: Settings | None = None) -> MediaTooling:
    """Locate FFmpeg and ffprobe (cached - safe to call on every rerun)."""
    resolved = settings or get_settings()
    return _locate(resolved.ffmpeg_binary, resolved.ffprobe_binary)


def reset_tooling_cache() -> None:
    """Clear the tooling cache (used by tests after changing PATH)."""
    _locate.cache_clear()


def require_media_tooling(settings: Settings | None = None) -> MediaTooling:
    """Return the tooling or raise an actionable error."""
    tooling = detect_media_tooling(settings)
    if not tooling.available:
        raise MediaToolingError(FFMPEG_SETUP_MESSAGE)
    return tooling


def _run_command(
    command: list[str],
    *,
    timeout: float,
    timeout_error: Exception,
) -> subprocess.CompletedProcess[str]:
    """Run an external command with no shell, a timeout and captured output."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argument list, shell disabled
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as error:
        raise timeout_error from error
    except OSError as error:
        raise MediaToolingError(FFMPEG_SETUP_MESSAGE) from error


def _diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    """Return a short, log-only tail of an FFmpeg error message."""
    text = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
    return text[-MAX_DIAGNOSTIC_CHARS:]


def probe_media(
    path: Path | str,
    settings: Settings | None = None,
    *,
    timeout: float | None = None,
) -> MediaInfo:
    """Inspect a media file with ffprobe and return typed information."""
    resolved = settings or get_settings()
    tooling = require_media_tooling(resolved)
    media_path = Path(path)
    if not media_path.is_file():
        raise MediaInspectionError("The media file could not be found.")

    command = [
        str(tooling.ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    completed = _run_command(
        command,
        timeout=timeout or resolved.media_processing_timeout_seconds,
        timeout_error=MediaInspectionError("Inspecting the media file timed out."),
    )
    if completed.returncode != 0:
        logger.info("ffprobe failed (code=%s): %s", completed.returncode, _diagnostic(completed))
        raise MediaInspectionError(
            "This file could not be read as media. It may be corrupt, incomplete or use an "
            "unsupported codec."
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        raise MediaInspectionError("The media information could not be parsed.") from error

    try:
        return _media_info_from_payload(payload, media_path)
    except (TypeError, ValueError) as error:
        raise MediaInspectionError("The media file does not contain usable stream data.") from error


def _media_info_from_payload(payload: dict, media_path: Path) -> MediaInfo:
    """Convert an ffprobe JSON payload into :class:`MediaInfo`."""
    format_block = payload.get("format") or {}
    streams = payload.get("streams") or []

    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and stream.get("disposition", {}).get("attached_pic", 0) != 1
        ),
        None,
    )

    duration: float | None = None
    if format_block.get("duration") is not None:
        try:
            duration = max(0.0, float(format_block["duration"]))
        except (TypeError, ValueError):
            duration = None
    if duration is None:
        for stream in streams:
            if stream.get("duration") is not None:
                try:
                    duration = max(0.0, float(stream["duration"]))
                    break
                except (TypeError, ValueError):
                    continue

    sample_rate: int | None = None
    channels: int | None = None
    if audio_stream:
        try:
            sample_rate = int(audio_stream.get("sample_rate") or 0) or None
        except (TypeError, ValueError):
            sample_rate = None
        try:
            channels = int(audio_stream.get("channels") or 0) or None
        except (TypeError, ValueError):
            channels = None

    return MediaInfo(
        name=media_path.name,
        duration_seconds=duration,
        container_format=(format_block.get("format_name") or None),
        size_bytes=media_path.stat().st_size if media_path.is_file() else None,
        has_audio=audio_stream is not None,
        has_video=video_stream is not None,
        audio_codec=(audio_stream or {}).get("codec_name"),
        sample_rate_hz=sample_rate,
        channels=channels,
    )


def enforce_duration_limit(info: MediaInfo, settings: Settings | None = None) -> None:
    """Reject media longer than the configured maximum duration."""
    resolved = settings or get_settings()
    limit = resolved.max_video_duration_seconds
    if info.duration_seconds is not None and info.duration_seconds > limit:
        raise MediaDurationError(
            f"This recording is {info.duration_seconds / 60:.1f} minutes long, which exceeds the "
            f"configured limit of {limit / 60:.0f} minutes. Trim the recording or raise "
            "MAX_VIDEO_DURATION_SECONDS in your local .env file."
        )


def extract_audio(
    source: Path | str,
    destination: Path | str,
    settings: Settings | None = None,
    *,
    sample_rate: int = TARGET_SAMPLE_RATE_HZ,
    channels: int = TARGET_CHANNELS,
    timeout: float | None = None,
) -> Path:
    """Extract a normalised mono WAV audio track from a media file."""
    resolved = settings or get_settings()
    tooling = require_media_tooling(resolved)
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise AudioExtractionError("The media file could not be found for audio extraction.")

    destination_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(tooling.ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(destination_path),
    ]

    try:
        completed = _run_command(
            command,
            timeout=timeout or resolved.media_processing_timeout_seconds,
            timeout_error=MediaProcessingTimeout(
                "Audio extraction timed out. The recording may be very long or the machine "
                "may be under heavy load."
            ),
        )
    except BaseException:
        destination_path.unlink(missing_ok=True)
        raise

    if completed.returncode != 0 or not destination_path.is_file():
        destination_path.unlink(missing_ok=True)
        diagnostic = _diagnostic(completed)
        logger.info("ffmpeg extraction failed (code=%s): %s", completed.returncode, diagnostic)
        lowered = diagnostic.lower()
        if "matches no streams" in lowered or "does not contain any stream" in lowered:
            raise NoAudioStreamError(
                "This file does not contain an audio track, so there is nothing to transcribe."
            )
        raise AudioExtractionError(
            "Audio could not be extracted from this file. It may be corrupt, use an "
            "unsupported codec, or contain no audio track."
        )

    if destination_path.stat().st_size == 0:
        destination_path.unlink(missing_ok=True)
        raise AudioExtractionError("The extracted audio track was empty.")

    return destination_path


def prepare_audio_for_transcription(
    source: Path | str,
    work_dir: Path | str,
    settings: Settings | None = None,
    *,
    info: MediaInfo | None = None,
) -> tuple[Path, bool]:
    """Return an audio file ready for Whisper.

    Audio-only sources are passed through untouched (Whisper decodes them with
    FFmpeg anyway). Video sources are converted to 16 kHz mono WAV. Returns
    ``(audio_path, was_extracted)``.
    """
    resolved = settings or get_settings()
    source_path = Path(source)
    media_info = info or probe_media(source_path, resolved)

    if not media_info.has_audio:
        raise NoAudioStreamError(
            "This file does not contain an audio track, so there is nothing to transcribe."
        )

    if media_info.has_video:
        destination = Path(work_dir) / f"{source_path.stem}{EXTRACTED_AUDIO_SUFFIX}"
        extracted = extract_audio(source_path, destination, resolved)
        logger.info("Extracted audio from video input (%s bytes)", extracted.stat().st_size)
        return extracted, True

    return source_path, False
