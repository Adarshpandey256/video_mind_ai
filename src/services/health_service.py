"""Lightweight application diagnostics.

Health checks are local, cheap and side-effect free: they never call the NVIDIA
API, never download models and never initialise a vector store. The only network
dependent check is reported from an explicit, user triggered connection test.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import platform
import shutil
import sys
from typing import Any

from config.settings import Settings, get_settings, get_settings_load_error
from src.models.schemas import ConnectionTestResult, HealthCheck, HealthStatus
from src.services.transcription_service import cuda_available_cached

#: import name -> distribution name for the packages required by Step 1.
CORE_DEPENDENCIES: dict[str, str] = {
    "streamlit": "streamlit",
    "openai": "openai",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "dotenv": "python-dotenv",
}

#: import name -> distribution name for the packages Steps 3-5 actually use.
#: Step 3 adds no package (it reuses the Step 1 OpenAI client and Pydantic models).
LATER_STAGE_DEPENDENCIES: dict[str, str] = {
    "langchain_core": "langchain-core",
    "langchain_chroma": "langchain-chroma",
    "chromadb": "chromadb",
    "sentence_transformers": "sentence-transformers",
    "reportlab": "reportlab",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "pptx": "python-pptx",
}

MISSING_CREDENTIALS_DETAIL = (
    "Not configured. Add NVIDIA_API_KEY to your local .env file and restart the application."
)


def _distribution_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - env dependent
        return None


def _is_importable(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        return False
    return True


def check_application_configuration(
    settings: Settings,
    settings_load_error: str | None = None,
) -> HealthCheck:
    """Verify that configuration loaded and the working directories are usable."""
    if settings_load_error:
        return HealthCheck(
            identifier="configuration",
            label="Application configuration",
            status=HealthStatus.WARNING,
            detail=(
                "Some environment values were invalid and safe defaults are in use: "
                f"{settings_load_error}"
            ),
            category="foundation",
            stage="Step 1",
        )

    writable = os.access(settings.base_dir, os.W_OK)
    return HealthCheck(
        identifier="configuration",
        label="Application configuration",
        status=HealthStatus.OK if writable else HealthStatus.WARNING,
        detail=(
            f"{settings.app_name} v{settings.app_version} loaded "
            f"(environment: {settings.app_env}, base directory: {settings.base_dir})."
            if writable
            else f"The base directory {settings.base_dir} is not writable."
        ),
        category="foundation",
        stage="Step 1",
    )


def check_core_dependencies() -> HealthCheck:
    """Verify that the Step 1 dependencies import correctly."""
    missing: list[str] = []
    versions: list[str] = []
    for module_name, distribution in CORE_DEPENDENCIES.items():
        if not _is_importable(module_name):
            missing.append(distribution)
            continue
        version = _distribution_version(distribution)
        versions.append(f"{distribution} {version}" if version else distribution)

    if missing:
        return HealthCheck(
            identifier="core_dependencies",
            label="Python dependencies",
            status=HealthStatus.ERROR,
            detail=(
                "Missing packages: "
                + ", ".join(missing)
                + ". Run: pip install -r requirements.txt inside the virtual environment."
            ),
            category="foundation",
            stage="Step 1",
        )

    return HealthCheck(
        identifier="core_dependencies",
        label="Python dependencies",
        status=HealthStatus.OK,
        detail="Installed: " + ", ".join(versions) + ".",
        category="foundation",
        stage="Step 1",
    )


def check_nvidia_credentials(settings: Settings) -> HealthCheck:
    """Report whether credentials exist, without revealing any value."""
    if settings.has_nvidia_credentials:
        detail = "Configured (the key value is never displayed)."
        status = HealthStatus.OK
    else:
        detail = MISSING_CREDENTIALS_DETAIL
        status = HealthStatus.WARNING

    return HealthCheck(
        identifier="nvidia_credentials",
        label="NVIDIA credentials",
        status=status,
        detail=detail,
        category="provider",
        stage="Step 1",
    )


def check_nvidia_connection(
    connection_result: ConnectionTestResult | None,
) -> HealthCheck:
    """Report the outcome of the last explicit connection test.

    A configured key is not evidence of a working connection, so this check stays
    ``pending`` until a real request has succeeded or failed.
    """
    if connection_result is None:
        return HealthCheck(
            identifier="nvidia_connection",
            label="NVIDIA API connection",
            status=HealthStatus.PENDING,
            detail=(
                "Not tested yet. Use the \"Test NVIDIA Connection\" button to send one "
                "short request."
            ),
            category="provider",
            stage="Step 1",
        )

    if connection_result.ok:
        latency = (
            f"{connection_result.latency_ms:.0f} ms"
            if connection_result.latency_ms is not None
            else "unknown latency"
        )
        return HealthCheck(
            identifier="nvidia_connection",
            label="NVIDIA API connection",
            status=HealthStatus.OK,
            detail=f"Last test succeeded with model {connection_result.model} ({latency}).",
            category="provider",
            stage="Step 1",
        )

    return HealthCheck(
        identifier="nvidia_connection",
        label="NVIDIA API connection",
        status=HealthStatus.ERROR,
        detail=f"Last test failed: {connection_result.message}",
        category="provider",
        stage="Step 1",
    )


def check_media_dependencies(settings: Settings | None = None) -> HealthCheck:
    """Report whether the Step 2 media stack (FFmpeg + Whisper) is ready."""
    resolved = settings or get_settings()
    ffmpeg_path = shutil.which(resolved.ffmpeg_binary)
    ffprobe_path = shutil.which(resolved.ffprobe_binary)
    whisper_available = _is_importable("whisper")
    torch_available = _is_importable("torch")
    ytdlp_available = _is_importable("yt_dlp")

    parts: list[str] = []
    parts.append(
        "FFmpeg and ffprobe found on PATH."
        if ffmpeg_path and ffprobe_path
        else "FFmpeg was not found on PATH: run 'winget install Gyan.FFmpeg', reopen the "
        "terminal and restart the app."
    )
    if whisper_available:
        version = _distribution_version("openai-whisper") or "unknown"
        device = "cpu"
        if torch_available:
            # Cached: the CUDA probe costs nothing after the first call in a process.
            device = "cuda" if cuda_available_cached() else "cpu"
        parts.append(
            f"Whisper {version} ready (model '{resolved.whisper_model}', device: {device})."
        )
    else:
        parts.append(
            "openai-whisper is not installed: run 'pip install -r requirements.txt' to enable "
            "transcription."
        )
    parts.append(
        "yt-dlp available for supported media pages."
        if ytdlp_available
        else "yt-dlp is not installed, so only direct media links can be downloaded."
    )

    ready = bool(ffmpeg_path and ffprobe_path and whisper_available)
    return HealthCheck(
        identifier="media_dependencies",
        label="Media dependencies (Step 2)",
        status=HealthStatus.OK if ready else HealthStatus.WARNING,
        detail=" ".join(parts),
        category="media",
        stage="Step 2",
    )


def check_later_stage_dependencies() -> HealthCheck:
    """Report whether the packages used by the implemented Steps 3-5 are present.

    This is a dependency check, not a roadmap: all three features are implemented,
    so reporting them as "planned" would be false. Import checks stay cheap because
    these modules are needed later in the same session anyway.
    """
    missing = [
        distribution
        for name, distribution in LATER_STAGE_DEPENDENCIES.items()
        if not _is_importable(name)
    ]
    if missing:
        return HealthCheck(
            identifier="later_stage_dependencies",
            label="Summarisation, chat and reports (Steps 3-5)",
            status=HealthStatus.WARNING,
            detail=(
                "These features are implemented but their packages are missing: "
                f"{', '.join(missing)}. Run 'pip install -r requirements.txt'."
            ),
            category="roadmap",
            stage="Steps 3-5",
        )
    return HealthCheck(
        identifier="later_stage_dependencies",
        label="Summarisation, chat and reports (Steps 3-5)",
        status=HealthStatus.OK,
        detail=(
            "Summarisation, grounded chat and every report export format are "
            "implemented, and their dependencies are installed."
        ),
        category="roadmap",
        stage="Steps 3-5",
    )


def run_health_checks(
    settings: Settings | None = None,
    connection_result: ConnectionTestResult | None = None,
    settings_load_error: str | None = None,
) -> list[HealthCheck]:
    """Run every diagnostic and return the results in display order."""
    resolved = settings or get_settings()
    error = (
        settings_load_error if settings_load_error is not None else get_settings_load_error()
    )
    return [
        check_application_configuration(resolved, error),
        check_core_dependencies(),
        check_nvidia_credentials(resolved),
        check_nvidia_connection(connection_result),
        check_media_dependencies(resolved),
        check_later_stage_dependencies(),
    ]


def summarise_checks(checks: list[HealthCheck]) -> dict[str, int]:
    """Count check results by status."""
    summary = {status.value: 0 for status in HealthStatus}
    for check in checks:
        summary[check.status.value] = summary.get(check.status.value, 0) + 1
    summary["total"] = len(checks)
    return summary


def get_check(checks: list[HealthCheck], identifier: str) -> HealthCheck | None:
    """Return a single check by identifier."""
    for check in checks:
        if check.identifier == identifier:
            return check
    return None


def environment_details(settings: Settings) -> dict[str, Any]:
    """Return non-secret environment information for the diagnostics panel."""
    return {
        "python_version": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "app_environment": settings.app_env,
        "base_directory": str(settings.base_dir),
        "log_file": str(settings.log_file),
        "dotenv_file_present": (settings.base_dir / ".env").is_file(),
        "packages": {
            distribution: _distribution_version(distribution)
            for distribution in CORE_DEPENDENCIES.values()
        },
    }
