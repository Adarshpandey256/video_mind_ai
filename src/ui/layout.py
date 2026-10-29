"""Sidebar navigation, page routing and page renderers for VideoMind AI.

The interface is deliberately thin: it validates user input, displays state and
delegates every provider call to :mod:`src.services`. Pages that belong to later
development steps are rendered as clearly marked placeholders instead of
pretending to work.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

import streamlit as st
from pydantic import ValidationError

from config.settings import Settings, get_settings_load_error
from src.models.schemas import (
    DEFAULT_PAGE,
    NAV_PAGES,
    ROADMAP_STAGES,
    ConnectionTestResult,
    FeatureStatus,
    HealthStatus,
    NavPage,
)
from src.services.health_service import (
    environment_details,
    run_health_checks,
    summarise_checks,
)
from src.services.llm_service import LLMService
from src.ui import components as ui
from src.ui import (
    chat_page,
    reports_page,
    summarization_page,
    transcription_view,
)
from src.ui.styles import BRAND_NAME, HERO_BODY, TAGLINE
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

NAV_WIDGET_KEY = "nav_radio"
NAV_REQUEST_KEY = "nav_request"
ACTIVE_PAGE_KEY = "active_page"
CONNECTION_RESULT_KEY = "nvidia_test_result"

SESSION_DEFAULTS: dict[str, Any] = {
    ACTIVE_PAGE_KEY: DEFAULT_PAGE,
    CONNECTION_RESULT_KEY: None,
}

PAGE_KEYS: tuple[str, ...] = tuple(page.key for page in NAV_PAGES)


# --------------------------------------------------------------------------- state
def initialise_session_state() -> None:
    """Create the session keys the interface relies on (safe to call repeatedly)."""
    for key, value in SESSION_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    transcription_view.initialise_state()


def get_page(page_key: str) -> NavPage:
    """Return the navigation entry for a page key."""
    for page in NAV_PAGES:
        if page.key == page_key:
            return page
    raise KeyError(f"Unknown page: {page_key}")


def resolve_page(requested: str | None) -> str:
    """Return a valid page key, falling back to the default page."""
    if requested is not None and requested in PAGE_KEYS:
        return requested
    return DEFAULT_PAGE


def get_active_page(state: Mapping[str, Any]) -> str:
    """Read the active page from a session-state-like mapping."""
    return resolve_page(state.get(ACTIVE_PAGE_KEY))


def set_active_page(state: MutableMapping[str, Any], page_key: str) -> str:
    """Store the active page in a session-state-like mapping."""
    resolved = resolve_page(page_key)
    state[ACTIVE_PAGE_KEY] = resolved
    return resolved


def request_page(state: MutableMapping[str, Any], page_key: str) -> str:
    """Request navigation from a button handler (pure part, no rerun)."""
    resolved = set_active_page(state, page_key)
    state[NAV_REQUEST_KEY] = resolved
    return resolved


def go_to_page(page_key: str) -> None:
    """Navigate to a page from a widget callback and rerun the app."""
    request_page(st.session_state, page_key)
    st.rerun()


def nav_option_label(page_key: str) -> str:
    """Format a page key for the sidebar navigation widget."""
    page = get_page(resolve_page(page_key))
    return f"{page.icon}  {page.label}"


# --------------------------------------------------------------------------- sidebar
def render_sidebar(settings: Settings) -> str:
    """Render the sidebar and return the page that should be displayed."""
    requested = st.session_state.pop(NAV_REQUEST_KEY, None)

    with st.sidebar:
        st.markdown(
            f'<div class="vm-brand"><span class="vm-brand__mark">🎬</span>'
            f'<span class="vm-brand__name">{BRAND_NAME}</span></div>'
            f'<p class="vm-brand__tag">{TAGLINE}</p>',
            unsafe_allow_html=True,
        )

        # Keep the navigation widget in sync when a page requested navigation,
        # before the widget is instantiated in this run.
        if requested is not None:
            st.session_state[NAV_WIDGET_KEY] = resolve_page(requested)

        selected = st.radio(
            "Navigation",
            options=list(PAGE_KEYS),
            format_func=nav_option_label,
            key=NAV_WIDGET_KEY,
            label_visibility="collapsed",
        )

        st.divider()
        ui.render_badges(
            [
                (
                    f"NVIDIA key {settings.nvidia_key_status}",
                    "ok" if settings.has_nvidia_credentials else "warn",
                )
            ]
        )
        st.caption(f"Model: {settings.nvidia_model}")
        st.caption(f"v{settings.app_version} · Step 5 of 5")
        st.caption("Transcription, summarisation, grounded chat and report exports are ready.")

    active = requested if requested is not None else selected
    return set_active_page(st.session_state, active)


# --------------------------------------------------------------------------- routing
def render_page(page_key: str, settings: Settings) -> str:
    """Render a page by key and return the resolved key."""
    resolved = resolve_page(page_key)
    _PAGE_RENDERERS[resolved](settings)
    return resolved


def render_unexpected_error() -> None:
    """Show a safe message when a page failed to render."""
    ui.render_status_banner(
        HealthStatus.ERROR,
        "Something went wrong while rendering this page. The application is still "
        "running - see logs/app.log for the technical details.",
        "Unexpected error",
    )


# --------------------------------------------------------------------------- home
def _foundation_cards(settings: Settings) -> list[ui.InfoCard]:
    key_variant = "ok" if settings.has_nvidia_credentials else "warn"
    key_text = "Key configured" if settings.has_nvidia_credentials else "Key missing"
    transcription_ready = transcription_view.media_stack_ready(settings)
    return [
        ui.InfoCard(
            icon="🧱",
            title="Application foundation",
            body="Central configuration, navigation, logging, data models and health checks.",
            badge_text="Available",
            badge_variant="ok",
        ),
        ui.InfoCard(
            icon="🔌",
            title="NVIDIA Nemotron",
            body=f"{settings.nvidia_model} through the OpenAI-compatible endpoint, with an explicit connection test.",
            badge_text=key_text,
            badge_variant=key_variant,
        ),
        ui.InfoCard(
            icon="🎙️",
            title="Transcription",
            body="Whisper speech-to-text with timestamped segments for uploaded or linked media.",
            badge_text="Available" if transcription_ready else "Setup needed",
            badge_variant="ok" if transcription_ready else "warn",
        ),
        ui.InfoCard(
            icon="📝",
            title="Summarisation",
            body="Structured summaries, key takeaways, topics and action items from the transcript.",
            badge_text="Available" if settings.has_nvidia_credentials else "Key missing",
            badge_variant="ok" if settings.has_nvidia_credentials else "warn",
        ),
        ui.InfoCard(
            icon="💬",
            title="Chat with your video",
            body="Local embeddings and ChromaDB retrieval, so answers cite the passages and timestamps they used.",
            badge_text="Available" if settings.has_nvidia_credentials else "Key missing",
            badge_variant="ok" if settings.has_nvidia_credentials else "warn",
        ),
    ]


def render_home_page(settings: Settings) -> None:
    """Render the dashboard: transcription workflow, provider summary and roadmap."""
    transcription_ready = transcription_view.media_stack_ready(settings)
    ui.hero(
        title=BRAND_NAME,
        tagline=TAGLINE,
        body=HERO_BODY,
        badges=[
            ("Foundations ready", "ok"),
            (
                "Transcription ready" if transcription_ready else "Transcription needs setup",
                "ok" if transcription_ready else "warn",
            ),
            (
                f"NVIDIA key {settings.nvidia_key_status}",
                "ok" if settings.has_nvidia_credentials else "warn",
            ),
            ("Summarisation ready", "ok"),
            ("Chat with video ready", "ok"),
            ("Report export ready", "ok"),
        ],
    )
    ui.render_cards(_foundation_cards(settings))

    st.divider()
    transcription_view.render_transcription_workflow(settings)

    st.divider()
    render_home_provider_panel(settings)

    st.divider()
    ui.section_header(
        "Planned workflow",
        "How the five development steps turn a source into knowledge.",
    )
    ui.render_steps(
        ROADMAP_STAGES,
        note_text=(
            "Steps 1-5 are implemented: transcription, structured summarisation, grounded "
            "retrieval chat and multi-format report export work end to end."
        ),
    )


def render_home_provider_panel(settings: Settings) -> None:
    """Render a compact provider summary with a shortcut to the settings page."""
    configured = settings.has_nvidia_credentials
    ui.section_header(
        "NVIDIA provider",
        "Summaries and grounded chat answers are generated by the configured NVIDIA model.",
    )
    ui.render_key_values(
        [
            ("API key", "Configured - the value is never displayed" if configured else "Not configured"),
            ("Model", settings.nvidia_model),
            ("Endpoint", settings.nvidia_base_url),
        ]
    )

    if configured:
        ui.render_status_banner(
            HealthStatus.OK,
            "Credentials are configured. Run an explicit connection test from Settings "
            "to verify the provider answers.",
            "Provider ready",
        )
    else:
        ui.render_status_banner(
            HealthStatus.WARNING,
            "Add NVIDIA_API_KEY to your local .env file and restart the application. "
            "The rest of the interface works without it.",
            "Provider not configured",
        )

    column_button, _ = st.columns([1, 3])
    with column_button:
        if st.button("Open Settings", key="home_open_settings"):
            go_to_page("settings")


# --------------------------------------------------------------------------- summarisation
def render_summarization_page(settings: Settings) -> None:
    """Render the Step 3 summarisation workflow.

    Delegates to :mod:`src.ui.summarization_page`; the page owns its own controls and
    never generates a summary on load.
    """
    summarization_page.render_summarization_page(settings)


def render_chat_page(settings: Settings) -> None:
    """Render the Step 4 retrieval augmented chat page.

    Delegates to :mod:`src.ui.chat_page`; that page owns its controls and never
    embeds or answers anything on load.
    """
    chat_page.render_chat_page(settings)


def render_reports_page(settings: Settings) -> None:
    """Render the Step 5 report generator and export workflow.

    Delegates to :mod:`src.ui.reports_page`; that page owns its controls and
    never re-runs transcription or summarisation to produce a report.
    """
    reports_page.render_reports_page(settings)


# --------------------------------------------------------------------------- settings
def render_settings_page(settings: Settings) -> None:
    """Render provider configuration, credential guidance and diagnostics."""
    ui.section_header(
        "Settings",
        "Provider configuration, credential guidance and local diagnostics.",
    )
    render_nvidia_panel(settings)
    st.divider()
    render_diagnostics_panel(settings)
    st.divider()
    render_security_panel(settings)


def render_nvidia_panel(settings: Settings) -> None:
    """Render the NVIDIA connection panel with the explicit connection test."""
    configured = settings.has_nvidia_credentials
    ui.section_header(
        "NVIDIA connection",
        "VideoMind AI calls NVIDIA Nemotron through the OpenAI-compatible API.",
    )
    ui.render_key_values(
        [
            ("API key", "Configured - the value is never displayed" if configured else "Not configured"),
            ("Model", settings.nvidia_model),
            ("Endpoint", settings.nvidia_base_url),
            ("Request timeout", f"{settings.nvidia_timeout_seconds:.0f} s"),
            ("Retries", str(settings.nvidia_max_retries)),
            ("Temperature", f"{settings.llm_temperature}"),
            ("Max output tokens", str(settings.llm_max_tokens)),
        ]
    )
    st.markdown("")

    column_action, column_hint = st.columns([1, 2])
    with column_action:
        clicked = st.button("Test NVIDIA Connection", type="primary", key="test_nvidia_connection")
    with column_hint:
        st.caption(
            "Sends one short request when clicked. The application never calls the "
            "provider on startup or on a page re-render."
        )

    if clicked:
        _run_connection_test(settings)

    result = _stored_connection_result()
    if result is not None:
        render_connection_result(result)
        if st.button("Clear test result", key="clear_connection_result"):
            st.session_state[CONNECTION_RESULT_KEY] = None
            st.rerun()
    else:
        ui.render_status_banner(
            HealthStatus.PENDING,
            "The provider connection has not been tested in this session. A configured "
            "key alone does not prove that the endpoint answers.",
            "Connection not tested",
        )

    with st.expander("How to configure NVIDIA_API_KEY safely"):
        _render_credential_instructions(settings)


def _run_connection_test(settings: Settings) -> None:
    """Run one explicit provider request and store its safe result."""
    service = LLMService(settings)
    with st.spinner("Contacting the NVIDIA endpoint..."):
        try:
            result = service.test_connection()
        except Exception:  # noqa: BLE001 - keep the interface alive
            logger.exception("Unexpected failure during the NVIDIA connection test")
            result = ConnectionTestResult(
                ok=False,
                message=(
                    "The connection test could not be completed. Check logs/app.log for "
                    "the technical details."
                ),
                model=settings.nvidia_model,
                base_url=settings.nvidia_base_url,
                error_type="request",
            )
    st.session_state[CONNECTION_RESULT_KEY] = result.model_dump(mode="json")
    logger.info(
        "NVIDIA connection test finished (ok=%s, error_type=%s)", result.ok, result.error_type
    )


def _stored_connection_result() -> ConnectionTestResult | None:
    """Return the stored connection test result, if it can be parsed."""
    stored = st.session_state.get(CONNECTION_RESULT_KEY)
    if not stored:
        return None
    try:
        return ConnectionTestResult.model_validate(stored)
    except ValidationError:  # pragma: no cover - defensive against stale session data
        return None


def render_connection_result(result: ConnectionTestResult) -> None:
    """Display the outcome of the last connection test."""
    tested_at = result.tested_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if result.ok:
        ui.render_status_banner(
            HealthStatus.OK,
            f"{result.message} (tested {tested_at})",
            "Connection verified",
        )
        latency = f"{result.latency_ms:.0f} ms" if result.latency_ms is not None else "unknown"
        ui.render_key_values(
            [
                ("Model", result.model or "unknown"),
                ("Endpoint", result.base_url or "unknown"),
                ("Latency", latency),
                ("Provider reply", result.response_preview or "-"),
            ]
        )
    else:
        ui.render_status_banner(
            HealthStatus.ERROR,
            f"{result.message} (tested {tested_at})",
            "Connection failed",
        )
        if result.error_type == "configuration":
            ui.render_status_banner(
                HealthStatus.WARNING,
                "Add NVIDIA_API_KEY to your local .env file and restart the application, "
                "then run the test again.",
                "Configuration required",
            )


def _render_credential_instructions(settings: Settings) -> None:
    """Explain how to configure credentials without revealing any value."""
    ui.render_bullets(
        [
            "Create or rotate a key in your NVIDIA account and revoke any key that was "
            "previously shared in chat, committed or captured in a screenshot.",
            "Copy .env.example to .env in the project root.",
            "Set NVIDIA_API_KEY to your new key inside .env. Never paste the key into "
            "chat, source files, notebooks or logs.",
            "Restart the application so the environment is reloaded, then use the test "
            "button above.",
        ]
    )
    st.code(
        "Copy-Item .env.example .env\n"
        "notepad .env\n"
        "streamlit run app.py",
        language="powershell",
    )
    st.caption(
        f"The key is read from the local environment only; .env is excluded by .gitignore. "
        f"Endpoint: {settings.nvidia_base_url} · Model: {settings.nvidia_model}"
    )


def render_diagnostics_panel(settings: Settings) -> None:
    """Render local health checks and non-secret environment details."""
    ui.section_header(
        "Diagnostics",
        "Local checks only: no provider request, no model download and no vector store.",
    )
    checks = run_health_checks(
        settings,
        connection_result=_stored_connection_result(),
        settings_load_error=get_settings_load_error(),
    )
    summary = summarise_checks(checks)
    ui.render_badges(
        [
            (f"{summary.get('ok', 0)} healthy", "ok"),
            (f"{summary.get('warning', 0)} warnings", "warn"),
            (f"{summary.get('error', 0)} errors", "err"),
            (f"{summary.get('pending', 0)} pending", "plan"),
        ]
    )
    ui.render_health_checks(checks)

    with st.expander("Environment details"):
        details = environment_details(settings)
        ui.render_key_values(
            [
                ("Python", str(details["python_version"])),
                ("Platform", str(details["platform"])),
                ("Environment", str(details["app_environment"])),
                ("Base directory", str(details["base_directory"])),
                ("Log file", str(details["log_file"])),
                (".env present", "yes" if details["dotenv_file_present"] else "no"),
            ]
        )
        ui.render_key_values(
            [(name, str(version or "not installed")) for name, version in details["packages"].items()]
        )

    with st.expander("Safe configuration summary (no secrets)"):
        st.json(settings.safe_summary())


def render_security_panel(settings: Settings) -> None:
    """Describe how credentials and user data are handled."""
    ui.section_header("Security and privacy", "How this foundation protects your data.")
    ui.render_bullets(
        [
            "The API key is read from the local environment and held in a secret type "
            "that cannot be printed or exported by accident.",
            "The key value is never rendered in the interface, never stored in session "
            "state and never passed to the browser.",
            "Log messages are filtered so API keys and authorisation headers are redacted.",
            ".env, virtual environments, logs, uploads and generated reports are excluded "
            "from version control.",
            "Media files are validated locally in Step 1; no third party receives them.",
        ]
    )
    st.caption(f"Configured endpoint: {settings.nvidia_base_url}")


# --------------------------------------------------------------------------- registry
_PAGE_RENDERERS: dict[str, Any] = {
    "home": render_home_page,
    "summarization": render_summarization_page,
    "chat": render_chat_page,
    "reports": render_reports_page,
    "settings": render_settings_page,
}

AVAILABLE_PAGES: tuple[str, ...] = tuple(
    page.key for page in NAV_PAGES if page.status == FeatureStatus.AVAILABLE
)


def planned_pages() -> Sequence[NavPage]:
    """Return the navigation entries that are not implemented yet."""
    return tuple(page for page in NAV_PAGES if page.status != FeatureStatus.AVAILABLE)
