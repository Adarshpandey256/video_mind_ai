"""VideoMind AI - Streamlit entry point.

Step 1 of the five-step roadmap: a runnable foundation with secure
configuration, a modular service layer and a professional interface shell.

The application must start (and stay usable) without NVIDIA credentials, and it
must never contact the provider while rendering a page.
"""

from __future__ import annotations

import streamlit as st

from config.settings import Settings, get_settings, load_environment
from src.ui import layout
from src.ui.styles import BRAND_NAME, inject_styles
from src.utils.logging_config import configure_logging, get_logger

PAGE_TITLE = f"{BRAND_NAME} · AI Video Assistant"
PAGE_ICON = "🎬"


def bootstrap() -> Settings:
    """Load settings, prepare directories and configure logging."""
    load_environment()

    try:
        settings = get_settings()
    except Exception:  # pragma: no cover - defensive: the shell must always open
        get_logger(__name__).exception(
            "Settings could not be loaded; continuing with built-in defaults."
        )
        settings = Settings.model_construct()

    try:
        settings.ensure_directories()
    except OSError:
        get_logger(__name__).warning(
            "Working directories could not be created; logs and reports may be unavailable."
        )

    configure_logging(
        level=settings.log_level,
        log_dir=settings.logs_dir,
        debug=settings.app_debug,
    )
    return settings


def main() -> None:
    """Configure the page, render the shell and dispatch to the active page."""
    # Must be the first Streamlit command that runs.
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )

    settings = bootstrap()
    logger = get_logger(__name__)
    logger.info(
        "%s started (version=%s, environment=%s, provider_key=%s)",
        BRAND_NAME,
        settings.app_version,
        settings.app_env,
        settings.nvidia_key_status,
    )

    layout.initialise_session_state()
    inject_styles()

    active_page = layout.render_sidebar(settings)
    try:
        layout.render_page(active_page, settings)
    except Exception:
        # A failing page must not take down the whole application.
        logger.exception("Unhandled error while rendering page '%s'", active_page)
        layout.render_unexpected_error()


if __name__ == "__main__":
    main()
