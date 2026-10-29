## Chapter 5 — Step 1: Project foundation

Step 1's goal was: *a runnable application with secure configuration, a modular service layer and
a professional interface shell that starts even with no API key.* Everything below exists in the
workspace today and is covered by tests.

### 5.1 `app.py` — the entry point (83 lines)

**What is it?** The only file Streamlit executes. It defines `bootstrap()` and `main()`.

**Why does it exist?** To separate "prepare the process" from "render the interface". Preparation
must happen exactly once per run and must never crash the page.

**Where is it used?** `streamlit run app.py`, and `tests/test_app_foundation.py` starts the whole
application with `AppTest.from_file("app.py")`.

**How does it work?** Real code, `app.py:23-49`:

```python
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
```

and the page shell, `app.py:50-83`:

```python
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
```

**Data flow:** environment → `.env` → `Settings` → directories created → logging configured →
session state initialised → CSS injected → sidebar returns a page key → that page renders.

**What calls it?** Streamlit itself. **What can go wrong?** A broken configuration (caught, defaults
used), an unwritable directory (logged, app continues), an exception inside a page (logged, a
friendly banner shown instead of a traceback).

**How to test it?** `./.venv/Scripts/python -m pytest tests/test_app_foundation.py -q`
(the AppTest cases `test_app_renders_the_dashboard_without_credentials` and
`test_app_navigation_switches_pages`).

**Viva answer:** "`app.py` is a thin shell. `bootstrap()` loads `.env`, builds validated settings,
creates the data directories and configures redacting logging. `main()` sets the Streamlit page
config, injects the stylesheet, renders the sidebar and dispatches to the selected page inside a
try/except so a single failing page cannot kill the process."

### 5.2 `config/settings.py` — configuration (679 lines)

**What is it?** One `pydantic-settings` class that owns every configurable value, plus path
properties and validators.

**Why?** So no module ever reads `os.environ` itself. One place to validate, one place to document,
one place to keep secrets out of the UI.

**How does it work?**

- `Settings` fields map to upper-case environment variables automatically (`nvidia_model` ←
  `NVIDIA_MODEL`). `model_config` sets `env_file=None` because *python-dotenv* does the loading,
  and `extra="ignore"` so unknown variables are harmless.
- `nvidia_api_key: SecretStr | None` — the value is wrapped, so `repr(settings)` shows `**********`.
- Field validators normalise input: `_clean_api_key` turns empty/placeholder values into `None`
  (so a template `.env` behaves as "not configured"); `_clean_base_url` falls back to the default
  for an invalid URL; `_clean_log_level` upper-cases and validates; `_clean_summary_mode` accepts
  only implemented modes; `_chunk_overlap_must_fit_inside_a_chunk` (a `model_validator`) prevents an
  impossible configuration where overlap ≥ chunk size.
- Path properties: `data_dir`, `upload_dir`, `processed_dir`, `reports_dir`, `logs_dir`,
  `log_file`, `chroma_dir`, `rag_registry_file`, `media_temp_dir`, `transcript_dir`, `summary_dir`.
- Safety helpers: `has_nvidia_credentials`, `nvidia_key_status` (a human string such as
  "configured"/"not configured" — never the key), `get_nvidia_api_key()`, `safe_summary()`
  (a log/UI-safe dictionary).

**Failure behaviour, `config/settings.py:647-675`:**

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings.

    Invalid environment values never crash the application: the problem is
    recorded (see :func:`get_settings_load_error`) and safe defaults are used
    instead, so the interface can still open and explain the problem.
    """
    global _load_error

    load_environment()
    try:
        settings = Settings()
    except ValidationError as error:
        _load_error = describe_validation_error(error)
        logger.error(
            "Invalid configuration detected (%s). Falling back to default settings.",
            _load_error,
        )
        return Settings.model_construct()
    _load_error = None
    return settings
```

**Data flow:** `.env` file → `load_dotenv(override=False)` → `os.environ` → `Settings()` validation →
cached instance shared by every module → directories/paths derived from `base_dir`.

**Callers:** every service and page; `load_environment` in `app.bootstrap`; `get_settings_load_error`
in the Settings page and `health_service.check_application_configuration`.

**Testing:** `tests/test_settings.py` (18 tests) covers defaults, placeholder keys, environment
overrides, invalid base URL fallback, log-level normalisation, comma-separated extensions, invalid
configuration fallback, `.env` loading and directory creation.

**Viva answer:** "Configuration is a single validated pydantic-settings class. Invalid values never
crash the app: the problem is recorded and safe defaults are used, which is why the interface can
always open and explain what is wrong."

### 5.3 `config/settings.py` constants worth knowing

| Constant | Value | Meaning |
| --- | --- | --- |
| `DEFAULT_NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Provider endpoint |
| `DEFAULT_NVIDIA_MODEL` | `nvidia/nemotron-3.5-lightning-30b-a3b` | Configured model |
| `DEFAULT_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model |
| `API_KEY_PLACEHOLDERS` | 4 known placeholder strings | Treated as "no key configured" |
| `VIDEO_EXTENSIONS` / `AUDIO_EXTENSIONS` | 10 / 8 extensions | Upload allow-list |
| `WHISPER_MODEL_CHOICES` | 13 checkpoints (`tiny.en` … `large`, `turbo`) | Validated by the UI, service and tests |
| `DEFAULT_WHISPER_MODEL` | `base` | CPU-friendly default |

### 5.4 `src/utils/logging_config.py` — logging with secret redaction (132 lines)

**What/Why:** one function configures console + rotating file logging, and a filter removes
credentials before any handler sees the record. Without it, a URL containing a token or an
`Authorization: Bearer …` header could be written into `logs/app.log`.

Key symbols: `redact_secrets(text, extra_secrets)`, `class RedactionFilter(logging.Filter)`,
`build_formatter()`, `configure_logging(level, log_dir, *, debug, force)`, `get_logger(name)`.

**Failure behaviour:** if the log directory is not writable, `configure_logging` returns `None`
instead of raising, so the application still runs with console logging only.

**Testing:** `test_log_records_are_redacted` and `test_configure_logging_writes_a_redacted_log_file`.

### 5.5 `src/ui/layout.py` — navigation and dispatch (558 lines)

**What:** five page renderers plus the routing helpers. `NAV_PAGES` (in `src/models/schemas.py:326`)
defines the five pages: `home` (Home 🏠), `summarization` (Summarization 📝), `chat`
(Ask Your Video 💬), `reports` (Reports 📄), `settings` (Settings ⚙️); `DEFAULT_PAGE = "home"`.

**How navigation works** — real code, `src/ui/layout.py:113-152`:

```python
def render_sidebar(settings: Settings) -> str:
    """Render the sidebar and return the page that should be displayed."""
    requested = st.session_state.pop(NAV_REQUEST_KEY, None)

    with st.sidebar:
        ...
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
        ...
    active = requested if requested is not None else selected
    return set_active_page(st.session_state, active)
```

Why a `radio` and session keys rather than Streamlit multipage files? Because the pages share one
in-memory session: the transcript chosen on Home must be visible to the Summarization page and to
the chatbot. Session state plus a single-page dispatch keeps that possible, and
`go_to_page()` / `request_page()` let a button navigate from inside a callback.

`render_page()` looks the key up in `_PAGE_RENDERERS` and calls the matching function; an unknown
key is rejected by `resolve_page()` (tested by `test_unknown_page_key_is_rejected`).

**What else is in this file:** `render_home_page` (+`_foundation_cards`, `render_home_provider_panel`),
`render_settings_page` (+`render_nvidia_panel`, `_run_connection_test`, `render_connection_result`,
`_render_credential_instructions`, `render_diagnostics_panel`, `render_security_panel`),
`render_unexpected_error`, `planned_pages`.

**Important design decision:** the connection test is the *only* provider call the Settings page can
make, and it happens only when the user clicks the button — never on load. The result is stored in
session state and re-rendered from a non-secret model (`ConnectionTestResult`).

### 5.6 `src/ui/styles.py` and `src/ui/components.py`

`styles.py` (188 lines) holds the brand constants (`BRAND_NAME`, `TAGLINE`, `HERO_BODY`) and
`inject_styles()`, which writes one `<style>` block (`.vm-brand`, `.vm-card`, `.vm-badge` …).
`components.py` (239 lines) holds every reusable widget: `hero`, `section_header`, `note`,
`InfoCard`/`card_html`/`render_cards`, `render_key_values`, `render_steps`, `badge`, `variant_for`,
`render_badges`, `render_status_banner`, `render_health_checks`, `render_pipeline_stages`,
`render_bullets`. All of them escape user text with `html.escape` before rendering HTML.

**Why it matters for security:** the interface shows transcript text, model text and titles. Every
path that injects HTML escapes its values, so a transcript containing `<script>` cannot execute.

### 5.7 `src/services/nvidia_client.py` + `llm_service.py` — the provider boundary

**What:** `nvidia_client.py` (150 lines) creates the OpenAI-compatible client and defines the error
hierarchy talked about in Chapter 4 (`NvidiaError` and eight subclasses, plus `classify_error_type`).
`llm_service.py` (467 lines) wraps chat completions.

**How `llm_service` handles model quirks (real behaviour, tested):**

1. `generate()` builds `messages` (system + user), sends the request with the configured timeout.
2. If the model rejects `max_tokens`, `_apply_parameter_fallback` retries **once** with
   `max_completion_tokens` and remembers the working parameter (`_token_parameter`).
3. If the model rejects `temperature`, the parameter is dropped for subsequent calls.
4. `_translate_error` maps every provider exception to a safe application error; `_extract_text`
   returns only the answer content (reasoning traces are never shown — asserted by
   `test_only_answer_content_is_exposed`).
5. If the provider returns HTTP 200 with an empty answer because the budget went to reasoning, the
   result is `NvidiaOutputTruncatedError` → `error_type="truncated"`, naming the budget to raise
   (instead of blaming the network).

**Failure cases handled:** missing key (`NvidiaConfigurationError`), 401 (`NvidiaAuthenticationError`),
429 (`NvidiaRateLimitError`), timeout, connection error, 404/400 model errors, empty/malformed
responses, truncated budget.

**Testing:** `tests/test_llm_service.py` (22 tests, all mocked — no key, no network) and
`tests/test_nvidia_client.py` (7 tests).

### 5.8 `src/services/health_service.py` — diagnostics (332 lines)

**What:** ten checks rendered on the Settings page: `check_application_configuration`,
`check_core_dependencies`, `check_nvidia_credentials`, `check_nvidia_connection`,
`check_media_dependencies` (FFmpeg + Whisper), `check_later_stage_dependencies` (the Step 3–5
packages), plus helpers `run_health_checks`, `summarise_checks`, `get_check`,
`environment_details`.

**Why:** to make the honest state of the machine visible — this is how a student finds out that
FFmpeg is missing *before* a 20-minute download.

**Important detail:** the credential check reports *whether* a key exists, never its value, and the
connection check only reflects a connection test the user actually ran (`HealthStatus.PENDING` until
then). `docs/DEVELOPMENT_ISSUES.md` ISS-008 records that this panel previously claimed Steps 3–5
were "still planned"; that was fixed, and a test asserts the current wording
(`tests/test_app_foundation.py`).

### 5.9 `src/models/schemas.py` — the shared models (435 lines)

Foundation models used by every later step: `utc_now()`, the enums `SourceType`
(`local_video`, `local_audio`, `url`), `ProcessingStatus`, `FeatureStatus`, `HealthStatus`,
`IngestionKind`, `StageStatus`, `PipelineOutcome`; and the models `TranscriptSegment`,
`TranscriptMetadata`, `TranscriptRecord`, `ValidatedSource`, `MediaInfo`, `TranscriptionOptions`,
`TranscriptionResult`, `PipelineStageReport`, `PipelineResult`, `LLMResponse`,
`ConnectionTestResult`, `HealthCheck`, `NavPage`, `RoadmapStage` (+ `NAV_PAGES`, `DEFAULT_PAGE`,
`ROADMAP_STAGES`). Validation rules live in the models (`TranscriptSegment` rejects an end before a
start), so invalid data cannot travel far.
