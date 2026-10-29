"""Manual integration verification for the Step 2 media pipeline.

Unit tests mock FFmpeg and Whisper on purpose. This script does the opposite: it
builds real media fixtures, runs the *real* pipeline (FFprobe, FFmpeg, openai-whisper
on the CPU) and checks the resulting transcript, storage round-trip and cleanup.

Run it from the project root with the project virtual environment:

    ./.venv/Scripts/python.exe scripts/verify_step2_media.py

It is intentionally not part of ``pytest``: it needs FFmpeg and downloads the
Whisper ``tiny`` checkpoint on first use, so it must not slow down the unit suite.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings, load_environment  # noqa: E402
from src.models.schemas import (  # noqa: E402
    PipelineOutcome,
    PipelineResult,
    StageStatus,
    TranscriptionOptions,
)
from src.services import media_service  # noqa: E402
from src.services.source_service import cleanup_stored_file, save_upload  # noqa: E402
from src.services.video_pipeline import VideoPipeline  # noqa: E402

FIXTURE_DIR = Path(tempfile.gettempdir()) / "videomind-fixtures"
SPEECH_TEXT = (
    "VideoMind AI turns recorded lectures into timestamped transcripts. "
    "Step two adds media ingestion, audio extraction and Whisper transcription."
)

SPEECH_WAV = FIXTURE_DIR / "speech.wav"
SPEECH_SIDECAR = FIXTURE_DIR / "speech.txt"
LECTURE_MP4 = FIXTURE_DIR / "lecture.mp4"
SILENT_MP4 = FIXTURE_DIR / "silent.mp4"

#: Words the transcript must contain for the speech fixture to count as recognised.
EXPECTED_WORDS = ("video mind", "transcript", "whisper", "extraction", "step")

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record one verification result."""
    checks.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' - {detail}' if detail else ''}")


def run(command: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    """Run a helper command and capture its output."""
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )


# --------------------------------------------------------------------------- fixtures
def wav_is_usable(path: Path) -> bool:
    """True when a WAV file exists and carries a non-trivial amount of audio."""
    if not path.is_file() or path.stat().st_size < 20_000:
        return False
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() > handle.getframerate()
    except (wave.Error, OSError):
        return False


def build_speech_wav() -> bool:
    """Synthesise a speech fixture with the Windows speech synthesiser.

    The spoken text is recorded in a sidecar file so an existing clip is only
    reused when it says exactly what the checks below expect.
    """
    if wav_is_usable(SPEECH_WAV) and SPEECH_SIDECAR.is_file():
        if SPEECH_SIDECAR.read_text(encoding="utf-8").strip() == SPEECH_TEXT:
            return True
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Rate = 0;"
        f"$s.SetOutputToWaveFile('{SPEECH_WAV}');"
        f"$s.Speak(\"{SPEECH_TEXT}\");"
        "$s.Dispose()"
    )
    result = run(["powershell", "-NoProfile", "-Command", script], timeout=120)
    if result.returncode != 0:
        print(f"    speech synthesis failed: {result.stderr.strip()[:200]}")
    if wav_is_usable(SPEECH_WAV):
        SPEECH_SIDECAR.write_text(SPEECH_TEXT, encoding="utf-8")
    return wav_is_usable(SPEECH_WAV)


def build_video_fixtures() -> bool:
    """Build a video that contains speech and one that has no audio track."""
    if not LECTURE_MP4.is_file() or LECTURE_MP4.stat().st_mtime < SPEECH_WAV.stat().st_mtime:
        run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=navy:s=320x240:r=15",
                "-i", str(SPEECH_WAV),
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(LECTURE_MP4),
            ]
        )
    if not SILENT_MP4.is_file():
        run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=15",
                "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-an",
                str(SILENT_MP4),
            ]
        )
    return LECTURE_MP4.is_file() and SILENT_MP4.is_file()


# --------------------------------------------------------------------------- helpers
def submit(path: Path, settings: Settings) -> tuple[object | None, PipelineResult | None]:
    """Push a file through the real upload store and the real pipeline."""
    with path.open("rb") as stream:
        stored = save_upload(stream, path.name, settings, expected_size=path.stat().st_size)
    stages: list[tuple[str, str]] = []
    progress = lambda message: stages.append((message, ""))  # noqa: E731
    pipeline = VideoPipeline(settings)
    result = pipeline.run(stored.as_source(), options=TranscriptionOptions(), progress=progress)
    cleanup_stored_file(stored.path, settings)
    return stored, result


def describe(result: PipelineResult) -> str:
    """One-line summary of a pipeline result."""
    completed = [s.stage for s in result.stages if s.status is StageStatus.COMPLETED]
    return f"outcome={result.outcome.value} stages={len(completed)} :: {result.message[:120]}"


def main() -> int:
    load_environment()
    # keep the run quick; override with VERIFY_WHISPER_MODEL=base for a real check
    import os

    settings = Settings(whisper_model=os.environ.get("VERIFY_WHISPER_MODEL", "tiny"))
    settings.ensure_directories()

    print("== environment ==")
    tooling = media_service.detect_media_tooling(settings)
    check("FFmpeg and FFprobe are available", tooling.available, tooling.summary if hasattr(tooling, "summary") else "")
    if not tooling.available:
        print(media_service.FFMPEG_SETUP_MESSAGE)
        return 1
    print(f"  whisper model for this run: {settings.whisper_model}")

    print("== fixtures ==")
    check("speech fixture created (Windows speech synthesis)", build_speech_wav(), str(SPEECH_WAV))
    check("video fixtures created with FFmpeg", build_video_fixtures(), str(FIXTURE_DIR))

    temp_dir = settings.media_temp_dir
    before = {p.name for p in temp_dir.iterdir()} if temp_dir.exists() else set()
    uploads_before = {p.name for p in settings.upload_dir.iterdir()}

    # ---------------------------------------------------------------- audio input
    print("== real transcription: audio (speech.wav) ==")
    stored, audio_result = submit(SPEECH_WAV, settings)
    print(f"  {describe(audio_result)}")
    check("audio upload transcribed successfully", audio_result.outcome is PipelineOutcome.SUCCESS)
    record = audio_result.transcript
    check("transcript has segments", record is not None and record.segment_count > 0, f"{getattr(record, 'segment_count', 0)} segment(s)")
    check("transcript text is non-empty", bool(record and record.full_text.strip()))
    recognised = sum(
        1 for word in EXPECTED_WORDS if record and word in record.full_text.lower()
    )
    check(
        "spoken words recognised",
        recognised >= 3,
        f"{recognised}/{len(EXPECTED_WORDS)} expected words: {(record.full_text[:120] if record else '')}",
    )
    check(
        "segments carry ordered timestamps",
        bool(
            record
            and record.segments
            and all(
                s.start_seconds is not None
                and s.end_seconds is not None
                and s.end_seconds >= s.start_seconds
                for s in record.segments
            )
        ),
    )
    check(
        "segments are in chronological order",
        bool(record and list(record.segments) == sorted(record.segments, key=lambda s: s.start_seconds or 0)),
    )
    check("language detected", bool(record and record.language), str(getattr(record, "language", None)))
    check("duration reported", bool(record and record.duration_seconds), f"{getattr(record, 'duration_seconds', None)}s")
    check("source type is local audio", record is not None and record.source_type.value == "local_audio", str(getattr(record, "source_type", "")))

    # ----------------------------------------------------------------- persistence
    print("== transcript storage round-trip ==")
    pipeline_service = VideoPipeline(settings).transcripts
    loaded = pipeline_service.load(record.transcript_id) if record and record.transcript_id else None
    check("stored transcript reloads by id", loaded is not None and loaded.full_text == record.full_text)
    metadata_ids = [m.transcript_id for m in pipeline_service.list_transcripts()]
    check("transcript listed in metadata index", record.transcript_id in metadata_ids)
    check("metadata index omits segments", not hasattr(pipeline_service.list_transcripts()[0], "segments"))
    stored_path = settings.transcript_dir / f"{record.transcript_id}.json"
    check("transcript JSON file written in the managed directory", stored_path.is_file(), str(stored_path.relative_to(PROJECT_ROOT)))
    check("transcript JSON excludes the API key", "NVIDIA_API_KEY" not in stored_path.read_text(encoding="utf-8"))
    check("no video bytes stored in JSON", stored_path.stat().st_size < 200_000, f"{stored_path.stat().st_size} bytes")

    # ----------------------------------------------------------------- video input
    print("== real transcription: video with speech (lecture.mp4) ==")
    _, video_result = submit(LECTURE_MP4, settings)
    print(f"  {describe(video_result)}")
    check("video upload transcribed successfully", video_result.outcome is PipelineOutcome.SUCCESS)
    check(
        "audio was extracted from the video",
        any("Extracted" in (s.detail or "") for s in video_result.stages),
    )
    check(
        "video transcript matches the audio transcript",
        bool(
            video_result.transcript
            and audio_result.transcript
            and audio_result.transcript.full_text.split()[:4]
            == video_result.transcript.full_text.split()[:4]
        ),
    )
    check("video source classified as local video", video_result.transcript.source_type.value == "local_video")

    # ------------------------------------------------------------ failure handling
    print("== failure path: video with no audio track (silent.mp4) ==")
    _, silent_result = submit(SILENT_MP4, settings)
    print(f"  {describe(silent_result)}")
    check("silent video is reported as a failure, not a success", silent_result.outcome is not PipelineOutcome.SUCCESS)
    check(
        "failure is attributed to the audio stage",
        silent_result.outcome is PipelineOutcome.AUDIO_EXTRACTION_FAILED,
        silent_result.outcome.value,
    )
    check("failure message is readable and leaks no path", "/" not in silent_result.message and silent_result.message.endswith("."), silent_result.message)
    check("no transcript returned for a failure", silent_result.transcript is None)

    # -------------------------------------------------------------------- cleanup
    print("== cleanup and resource handling ==")
    after = {p.name for p in temp_dir.iterdir()} if temp_dir.exists() else set()
    check(
        "job scratch space removed (extracted audio, downloads)",
        after <= before,
        f"left behind: {sorted(after - before)}",
    )
    check("no job directory remains", not list(temp_dir.glob("job-*")), str(sorted(temp_dir.glob('job-*'))))
    uploads_after = {p.name for p in settings.upload_dir.iterdir()}
    check(
        "uploaded copies removed from the managed upload dir",
        uploads_after <= uploads_before,
        f"left behind: {sorted(uploads_after - uploads_before)}",
    )
    check("fixture originals untouched", SPEECH_WAV.is_file() and LECTURE_MP4.is_file() and SILENT_MP4.is_file())
    try:
        import torch

        check("torch reports availability detected at runtime", True, f"torch {torch.__version__}, cuda={torch.cuda.is_available()}")
    except Exception as error:  # noqa: BLE001
        check("torch reports availability detected at runtime", False, str(error))

    print("== duplicate-job guard ==")
    registry = __import__("src.services.video_pipeline", fromlist=["JobRegistry"]).JobRegistry()
    from src.services.video_pipeline import job_key_for

    key = job_key_for(stored.as_source())
    check("first job start accepted", registry.start(key) is True)
    check("second job with the same key refused", registry.start(key) is False)
    registry.finish(key, record.transcript_id)
    check("completed job remembers its transcript", registry.completed_transcript(key) == record.transcript_id)

    # ---------------------------------------------------------------------- summary
    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n== summary: {len(checks) - len(failed)}/{len(checks)} checks passed ==")
    if failed:
        for name in failed:
            print(f"  FAILED: {name}")
        return 1
    print("Real end-to-end verification passed on actual media with real Whisper.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
