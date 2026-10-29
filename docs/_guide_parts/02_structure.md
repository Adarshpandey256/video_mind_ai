## Chapter 3 — Complete project folder structure

### 3.1 The real source tree

Produced from the actual workspace (virtual environment, caches, `chroma_data/`, `logs/` and
generated media excluded, as required). Line counts are measured with `wc -l`.

```
C:\ai video assistant\
├── app.py                       83 lines   Streamlit entry point (bootstrap + main)
├── requirements.txt                        Pinned, resolved dependency list for Steps 1-5
├── requirements-later-stages.txt            Dependency decisions + rationale per step
├── pytest.ini                              pytest configuration (testpaths, strict markers)
├── run_backend.bat                         Windows convenience launcher (backend/UI)
├── run_frontend.bat                        Windows convenience launcher (frontend)
├── README.md                               Long-form project documentation (86 KB)
├── .env.example                            Safe configuration template (placeholders only)
├── .gitignore                              Excludes .env, data, logs, reports, cookies, caches
├── .streamlit/
│   └── config.toml                         Streamlit server config (maxUploadSize = 500)
├── config/
│   ├── __init__.py                5 lines  Re-exports Settings / get_settings
│   └── settings.py              679 lines  THE configuration module (env vars, paths, validators)
├── src/
│   ├── __init__.py                1 line
│   ├── models/                             Typed data models (no I/O, no Streamlit)
│   │   ├── __init__.py          146 lines  Facade re-exporting every model
│   │   ├── schemas.py           435 lines  Core models: SourceType, TranscriptRecord, PipelineResult...
│   │   ├── summary_schemas.py   482 lines  Step 3 models: SummaryMode, StructuredSummary, SummaryOptions
│   │   └── rag_schemas.py       356 lines  Step 4 models: RagChunk, RetrievedSource, RagAnswer, IndexInfo
│   ├── prompts/
│   │   ├── __init__.py           29 lines  Facade
│   │   └── summary_prompts.py   354 lines  Step 3 prompt construction + JSON contract
│   ├── services/                           Backend logic (Steps 1-3 + orchestration)
│   │   ├── __init__.py          135 lines  Facade exporting every service symbol
│   │   ├── nvidia_client.py     150 lines  OpenAI-compatible client factory + error hierarchy
│   │   ├── llm_service.py       467 lines  Chat-completion wrapper (retries, error translation)
│   │   ├── health_service.py    332 lines  Diagnostics used by the Settings page
│   │   ├── source_service.py    196 lines  Upload validation + safe storage
│   │   ├── url_ingestion_service.py 844 lines  URL validation, SSRF guards, downloads
│   │   ├── media_service.py     348 lines  ffprobe inspection + FFmpeg audio extraction
│   │   ├── transcription_service.py 404 lines  Whisper transcription
│   │   ├── transcript_storage.py 163 lines  Atomic JSON persistence for transcripts
│   │   ├── transcript_service.py 163 lines  Transcript domain service (+ TXT/JSON export)
│   │   ├── video_pipeline.py    469 lines  Step 2 orchestration + JobRegistry
│   │   ├── transcript_preprocessor.py 290 lines  Render/chunk a transcript for the model
│   │   ├── timestamp_service.py 220 lines  Validate model timestamps against the transcript
│   │   ├── summary_generation.py 229 lines  Bounded, validated JSON requests to the model
│   │   ├── chunk_summarizer.py  132 lines  Summarise one chunk
│   │   ├── summary_merger.py    140 lines  Merge chunk summaries
│   │   ├── summary_validation.py 426 lines  Raw JSON -> typed StructuredSummary
│   │   ├── summary_storage.py   220 lines  Atomic JSON persistence for summaries
│   │   └── summarization_service.py 645 lines  Step 3 orchestration
│   ├── rag/                                Step 4 retrieval + answering
│   │   ├── __init__.py           72 lines  Facade
│   │   ├── transcript_adapter.py 177 lines  Normalise a step 2 transcript for retrieval
│   │   ├── chunking.py          384 lines  Timestamp-aware chunk packing
│   │   ├── embeddings.py        162 lines  Local sentence-transformers provider
│   │   ├── vector_store.py      598 lines  ChromaDB collections + JSON registry
│   │   ├── retriever.py         217 lines  Ranking, de-duplication, context budget
│   │   ├── prompts.py           149 lines  Grounded-answer prompts + source rendering
│   │   ├── answer_generator.py  379 lines  LangChain chain + citation validation
│   │   └── service.py           465 lines  Step 4 orchestration (index, ask, delete)
│   ├── reports/                            Step 5 report model + exporters
│   │   ├── __init__.py           63 lines  Facade
│   │   ├── report_models.py     430 lines  ReportSection, VideoMindReport, ReportOptions
│   │   ├── report_builder.py    599 lines  Build the report from real Steps 2-4 data
│   │   ├── export_types.py       51 lines  RenderResult, ExportFormat, ReportExport
│   │   ├── export_formats.py    214 lines  THE list of implemented formats
│   │   ├── filename_utils.py     87 lines  Safe download filenames
│   │   ├── pdf_exporter.py      560 lines  ReportLab Platypus PDF
│   │   ├── docx_exporter.py     372 lines  python-docx
│   │   ├── text_exporter.py     247 lines  Plain text
│   │   ├── markdown_exporter.py 261 lines  Markdown (escaping + fences)
│   │   ├── json_exporter.py      33 lines  JSON (schema-versioned)
│   │   ├── html_exporter.py     368 lines  Self-contained escaped HTML
│   │   ├── csv_exporter.py      131 lines  CSV transcript / chat / sources
│   │   ├── xlsx_exporter.py     252 lines  openpyxl workbook
│   │   └── pptx_exporter.py     317 lines  python-pptx deck
│   ├── ui/                                 Streamlit pages and widgets
│   │   ├── __init__.py            1 line
│   │   ├── layout.py            558 lines  Sidebar navigation + page dispatch + settings/diagnostics
│   │   ├── styles.py            188 lines  Brand constants + injected CSS
│   │   ├── components.py        239 lines  Reusable cards, badges, stage renderers
│   │   ├── transcription_view.py 688 lines  Home page workflow (Step 2)
│   │   ├── summarization_page.py 792 lines  Step 3 page
│   │   ├── chat_page.py         644 lines  Step 4 page
│   │   └── reports_page.py      670 lines  Step 5 page
│   └── utils/                              Small shared helpers
│       ├── __init__.py            1 line
│       ├── logging_config.py    132 lines  Logging + RedactionFilter
│       ├── file_utils.py        109 lines  Filename/size/extension helpers
│       ├── json_utils.py        199 lines  Tolerant extraction of JSON from model output
│       └── time_utils.py         34 lines  format_timestamp / format_duration
├── tests/                                  36 files, 10,407 lines, 651 tests
│   ├── __init__.py, conftest.py (82), rag_support.py (229), report_support.py (236)
│   └── test_*.py                           32 test modules (see Chapter 15)
├── scripts/
│   ├── verify_step2_media.py    303 lines  Manual live Step 2 verification (builds fixtures)
│   ├── inventory_for_docs.py     69 lines  AST inventory used to write this handbook
│   └── generate_project_guide_pdf.py  1,491 lines  Markdown -> PDF builder for this handbook
├── docs/
│   ├── PROJECT_STATUS.md                   Feature checklist with live-verification status
│   ├── DEVELOPMENT_ISSUES.md               Issue log (ISS-001 … ISS-009)
│   ├── FINAL_REPORT.md                     End-of-project report
│   ├── VideoMind_AI_Complete_Project_Guide.md    <- this document
│   └── VideoMind_AI_Complete_Project_Guide.pdf   <- generated from the Markdown
├── data/                    (generated)    uploads/, temp/, processed/transcripts/, processed/summaries/
├── logs/                    (generated)    app.log + verification logs (git-ignored)
├── reports/                 (generated)    live-check exports (git-ignored except .gitkeep)
└── chroma_data/             (generated)    ChromaDB persistence + index_registry.json
```

### 3.2 Top-level files

| Path | What it contains | Why it exists | If removed |
| --- | --- | --- | --- |
| `app.py` | `bootstrap()` and `main()` | The only entry point: sets page config, loads settings, configures logging, renders the shell | The application cannot start (`streamlit run app.py` fails) |
| `requirements.txt` | 19 pinned direct dependencies | Reproducible environment | Setup becomes guesswork; versions may drift and break the code |
| `requirements-later-stages.txt` | Commented dependency plan | Records *why* each package exists (and that `langchain-chroma`/`langchain-core` are the only LangChain parts used) | Rationale is lost; no runtime effect |
| `pytest.ini` | `testpaths = tests`, `python_files`, `addopts = -q --strict-markers`, warning filter | Makes `python -m pytest` work from the project root without flags | Tests may not be discovered; typo'd markers become silent |
| `run_backend.bat` / `run_frontend.bat` | Windows launch helpers | Convenience for demos | Application still runs manually |
| `.env.example` | Template with placeholder key and commented optional overrides | Teaches correct configuration without leaking a real key | New users would hardcode keys — a security risk |
| `.gitignore` | Ignores `.env`, `data/`, `logs/`, `reports/*`, `chroma_data/`, `cookies*.txt`, caches, `*.report.*` | Prevents leaking secrets, private transcripts and generated artefacts | Private data and credentials could be committed |
| `.streamlit/config.toml` | `maxUploadSize = 500`, telemetry off | Streamlit rejects oversized uploads before the app sees them, so it must mirror `MAX_UPLOAD_SIZE_MB` | Uploads >75 MB (Streamlit's default) would be rejected with a confusing error |
| `README.md` | 86 KB of long-form documentation | Step-by-step user and developer guide | Documentation only |

### 3.3 `config/` — the single configuration module

| File | Purpose and key symbols | Depends on | What breaks if removed |
| --- | --- | --- | --- |
| `config/__init__.py` | Re-exports `Settings`, `get_settings` | `config.settings` | Import style `from config import Settings` (used in tests) |
| `config/settings.py` | `Settings` (every configurable value + validators), `get_settings()` (cached), `load_environment()`, `get_settings_load_error()`, `reset_settings_cache()`, plus constants `DEFAULT_NVIDIA_BASE_URL`, `DEFAULT_NVIDIA_MODEL`, `VIDEO_EXTENSIONS`, `AUDIO_EXTENSIONS`, `WHISPER_MODEL_CHOICES`, `API_KEY_PLACEHOLDERS` | `python-dotenv`, `pydantic`, `pydantic-settings`, `src.models.summary_schemas` (mode/length choices) | **Everything.** Every service reads configuration from here |

### 3.4 `src/models/` — the shared vocabulary

| File | Key symbols | Who uses it |
| --- | --- | --- |
| `schemas.py` | `utc_now`, `SourceType`, `ProcessingStatus`, `FeatureStatus`, `HealthStatus`, `VideoMetadata`, `TranscriptSegment`, `TranscriptMetadata`, `TranscriptRecord`, `ChatSourceReference`, `ChatResponse`, `LLMResponse`, `ConnectionTestResult`, `HealthCheck`, `IngestionKind`, `ValidatedSource`, `MediaInfo`, `TranscriptionOptions`, `TranscriptionResult`, `PipelineOutcome`, `StageStatus`, `PipelineStageReport`, `PipelineResult`, `NavPage`, `RoadmapStage`, `NAV_PAGES`, `DEFAULT_PAGE`, `ROADMAP_STAGES` | UI, services, RAG, reports, tests |
| `summary_schemas.py` | `SummaryMode` (8 modes), `SummaryLength` (3), `SummaryStatus`, `SummaryOutcome`, `TimestampedItem`, `KeyTakeaway`, `Topic`, `Chapter`, `ActionItem`, `Decision`, `ImportantPoint`, `SummaryCoverage`, `ChunkSummary`, `SummaryOptions`, `SummaryStageReport`, `StructuredSummary`, `SummaryMetadata`, `SummaryResult` + label/choice dictionaries | Step 3 service, prompts, reports, UI |
| `rag_schemas.py` | `DocumentType`, `IndexStatus`, `IndexOutcome`, `RagOutcome`, `RagChunk`, `RetrievedSource`, `RagCitation`, `RagAnswer`, `RagResult`, `ChatTurn`, `IndexInfo`, `IndexResult`, `ChatOptions` | `src/rag/*`, chat page, report builder |
| `__init__.py` | Re-exports all three modules' public names | Everything that imports `from src.models import ...` |

**Why this package exists:** it prevents "dictionary chaos". Every stage hands the next stage a
typed object, so a typo becomes a validation error instead of a silent `None` deep in the UI.
Removing it would break every module in the project.

### 3.5 `src/services/` — business logic

| File | Responsibility | Key entry points | Called by |
| --- | --- | --- | --- |
| `nvidia_client.py` | Client factory + error hierarchy + `classify_error_type` | `create_nvidia_client`, `get_nvidia_client`, `is_nvidia_configured`, `describe_client_config` | `llm_service`, `health_service`, tests |
| `llm_service.py` | One non-streaming/streaming chat completion with retries, parameter fallback and safe error translation; the connection test | `LLMService.generate`, `stream_generate`, `test_connection` | Step 3 (`summary_generation`), Step 4 (`answer_generator`), Settings page |
| `health_service.py` | Non-secret diagnostics | `run_health_checks`, `check_core_dependencies`, `check_media_dependencies`, `check_later_stage_dependencies`, `environment_details` | `src/ui/layout.py` |
| `source_service.py` | Upload validation and safe storage | `validate_upload`, `save_upload`, `unique_upload_path`, `cleanup_stored_file`, `is_inside` | `transcription_view`, `video_pipeline` |
| `url_ingestion_service.py` | URL validation, SSRF protection, direct + yt-dlp downloads, error translation | `validate_url`, `validate_url_syntax`, `assert_public_destination`, `build_url_source`, `download_media`, `_translate_ytdlp_error` | `video_pipeline`, `transcription_view`, tests |
| `media_service.py` | ffprobe inspection, duration limit, audio extraction | `probe_media`, `extract_audio`, `prepare_audio_for_transcription`, `require_media_tooling` | `video_pipeline` |
| `transcription_service.py` | Whisper transcription and validation | `TranscriptionService.transcribe`, `load_model`, `resolve_device`, `unload_whisper_models` | `video_pipeline`, UI readiness checks |
| `transcript_storage.py` | Atomic JSON persistence | `save`, `load`, `list_metadata`, `delete`, `path_for` | `transcript_service` |
| `transcript_service.py` | Domain service: build/save/load/list/delete records, TXT/JSON export, `video_id_for` | `build_record`, `to_txt`, `to_json`, `prune_old_transcripts` | pipeline, Step 3, Step 4, Step 5, UI |
| `video_pipeline.py` | Orchestrates validate → acquire → inspect → extract → transcribe → store; `JobRegistry` blocks duplicates | `VideoPipeline.run`, `job_key_for`, `PIPELINE_STAGES` | `transcription_view` |
| `transcript_preprocessor.py` | Render a transcript for the prompt and chunk it when too long | `render_transcript`, `estimate_tokens`, `TranscriptPreprocessor.prepare` | `summarization_service` |
| `timestamp_service.py` | Validate/snap/drop model timestamps against real segments | `validator_for`, `TimestampValidator.sanitize`, `is_grounded` | `chunk_summarizer`, `summary_merger`, `summary_validation` |
| `summary_generation.py` | Ask for JSON with bounded retries; parse or repair | `SummaryJsonClient.request_json` | `chunk_summarizer`, `summary_merger`, `summarization_service` |
| `chunk_summarizer.py` | One chunk → `ChunkSummary` | `ChunkSummarizer.summarize_chunk`, `build_chunk_summary` | `summarization_service` |
| `summary_merger.py` | Merge chunk summaries (grouped when many) | `SummaryMerger.merge` | `summarization_service` |
| `summary_validation.py` | Raw JSON → typed sections; dedupe; grounding; quality notes | `build_structured_summary`, `build_takeaways`, `build_action_items`, `deduplicate`, `quality_notes` | `summarization_service`, `chunk_summarizer`, `summary_merger` |
| `summary_storage.py` | Atomic JSON persistence for summaries | `save`, `load`, `list_metadata`, `latest_for_transcript`, `delete` | `summarization_service`, UI pages, `rag/service` |
| `summarization_service.py` | Step 3 orchestration: single-pass or chunk+merge, stage reporting, honest failures | `SummarizationService.summarize`, `summarize_transcript` | `summarization_page` |

### 3.6 `src/rag/` — retrieval-augmented generation

| File | Responsibility | Key symbols |
| --- | --- | --- |
| `transcript_adapter.py` | Turn a stored transcript into the retrieval shape; compute a content fingerprint | `normalize_transcript`, `NormalizedTranscript`, `compute_fingerprint`, `normalize_text` |
| `chunking.py` | Split into ordered, timestamped chunks with overlap | `TranscriptChunker.chunk_transcript`, `chunk_summary`, `summary_text` |
| `embeddings.py` | Local embedding provider (LangChain `Embeddings` interface) | `LocalSentenceTransformerEmbeddings`, `build_embeddings`, `embedding_model_id` |
| `vector_store.py` | Per-video ChromaDB collections, atomic JSON registry, status/staleness, search, delete | `ChromaVectorStore`, `IndexRegistry`, `IndexRecord`, `collection_name_for` |
| `retriever.py` | Scope to one video, rank, de-duplicate, enforce the context budget | `Retriever.retrieve`, `contextual_query`, `needs_conversation_context` |
| `prompts.py` | Render sources/history and the grounded-answer contract | `render_sources`, `build_answer_user_prompt`, `SYSTEM_PROMPT` |
| `answer_generator.py` | LangChain LCEL chain, JSON parsing, citation + quote validation | `GroundedAnswerGenerator.generate`, `_validate`, `quote_appears_in` |
| `service.py` | Step 4 orchestration used by the page | `RagService.index_transcript`, `ask`, `index_status`, `delete_index` |

### 3.7 `src/reports/` — Step 5

| File | Responsibility | Key symbols |
| --- | --- | --- |
| `report_models.py` | The canonical report model and its sub-models | `ReportSection` (10 sections), `ReportOptions`, `VideoMindReport`, `ReportBuildError`, `new_report_id`, `REPORT_SCHEMA_VERSION` |
| `report_builder.py` | Build the report from real stored data; report availability and omissions | `build_report`, `section_availability`, `default_report_title`, `safe_source_url` |
| `export_types.py` | `RenderResult`, `ExportFormat`, `ReportExport` | shared value types |
| `export_formats.py` | The single source of truth for supported formats | `EXPORT_FORMATS`, `FORMAT_REQUIREMENTS`, `build_export`, `get_format`, `DEFAULT_FORMAT_KEY` |
| `filename_utils.py` | Safe, bounded, unique download names | `safe_stem`, `report_filename`, `safe_extension` |
| `pdf_exporter.py` | ReportLab Platypus PDF with page decoration and story building | `build_pdf`, `resolve_font`, `_PageDecorator` |
| `docx_exporter.py` | Word document with headings, tables, fields, complex-script font | `build_docx` |
| `text_exporter.py` | Wrapped plain text | `render_text` |
| `markdown_exporter.py` | Escaped Markdown with adaptive code fences | `render_markdown`, `escape_block` |
| `json_exporter.py` | JSON with `report_to_dict` | `render_json` |
| `html_exporter.py` | Self-contained escaped HTML with safe links | `render_html`, `_safe_href` |
| `csv_exporter.py` | Three tabular exports with formula protection | `render_csv_transcript`, `render_csv_chat`, `render_csv_sources`, `protect_csv_cell` |
| `xlsx_exporter.py` | Workbook, one sheet per included content type | `build_xlsx` |
| `pptx_exporter.py` | Concise deck with slide numbering | `build_pptx` |

### 3.8 `src/ui/` — presentation

| File | Responsibility |
| --- | --- |
| `layout.py` | Sidebar navigation, page dispatch, Home dashboard, Settings page (provider panel, connection test, diagnostics, security panel), `planned_pages()` |
| `styles.py` | `BRAND_NAME`, `TAGLINE`, `HERO_BODY`, `inject_styles()` (CSS) |
| `components.py` | `hero`, `section_header`, `note`, `InfoCard`, `render_cards`, `render_key_values`, `render_steps`, `render_status_banner`, `render_health_checks`, `render_pipeline_stages`, `render_bullets`, `badge` |
| `transcription_view.py` | Step 2 experience: upload/URL inputs, options, start, live stages, transcript detail, saved transcripts, stuck-job recovery |
| `summarization_page.py` | Step 3 page: transcript picker, mode/length/language controls, generation, result rendering, saved summaries |
| `chat_page.py` | Step 4 page: video picker, index build/rebuild/delete, conversation, citations, retry, per-video history |
| `reports_page.py` | Step 5 page: section selection, title/author/notes, preview, format choice, generation cache, download |

**Why the UI is separate:** pages call services through a small number of *seam functions*
(`service_for`, `build_index`, `ask_question`, `run_summarization`, `run_health_checks`) so tests
can replace them with stubs. This is exactly how `tests/test_chat_ui.py` and
`tests/test_summarization_ui.py` avoid network calls while still driving the real page.

### 3.9 `src/utils/`

| File | Purpose | If removed |
| --- | --- | --- |
| `logging_config.py` | Rotating file log + console log + `RedactionFilter` + `get_logger` | API keys could leak into `logs/app.log` |
| `file_utils.py` | Extension/size/filename helpers, `guess_source_type`, `looks_like_url` | Upload validation and safe names are lost |
| `json_utils.py` | `extract_json_object` (ignores braces inside strings), type coercion helpers | Model output could not be parsed reliably |
| `time_utils.py` | `format_timestamp`, `format_duration`, `UNAVAILABLE_TIMESTAMP` | Timestamps could be rendered as fake `00:00` values |

### 3.10 `tests/`, `scripts/`, `docs/`

| Path | Purpose |
| --- | --- |
| `tests/conftest.py` | `isolated_environment` fixture (removes provider config, points the `.env` loader at a missing file) and `_reset_rag_caches` (drops embedding/Chroma caches between tests) |
| `tests/rag_support.py` | Offline stand-ins: `FakeEmbeddings`, `StubLLM`, `StubChain`, `StubAnswerGenerator`, `make_transcript`, `make_settings` |
| `tests/report_support.py` | Fictional but realistic report fixtures (`sample_transcript`, `sample_summary`, `sample_chat_messages`, `make_report`) |
| `tests/test_*.py` | 32 modules, 651 tests (Chapter 15) |
| `scripts/verify_step2_media.py` | Manual live check: synthesises speech with the Windows speech engine, builds video fixtures, pushes them through the real pipeline |
| `scripts/inventory_for_docs.py` | AST inventory tool used to document the project |
| `scripts/generate_project_guide_pdf.py` | Renders this handbook's Markdown into the PDF (ReportLab Platypus); documentation only, never imported by the app |
| `docs/PROJECT_STATUS.md` | Feature checklist distinguishing *implemented* from *live-verified* |
| `docs/DEVELOPMENT_ISSUES.md` | Issue log with exact errors, tracebacks, reproduction steps and fixes |
| `docs/FINAL_REPORT.md` | End-of-project report (features, tests, known issues, commands) |

### 3.11 Directories deliberately excluded from the main tree

| Directory | Why excluded | Nature |
| --- | --- | --- |
| `.venv/` | Thousands of dependency files; not project code | Generated (dependencies) |
| `__pycache__/`, `.pytest_cache/` | Byte-code and test caches | Generated |
| `chroma_data/` | Binary ChromaDB persistence (SQLite + level0 files) plus `index_registry.json` | Generated runtime data |
| `logs/` | `app.log` and verification logs; may contain private text | Generated, git-ignored |
| `data/uploads/`, `data/temp/` | User media and per-job scratch directories | Generated, cleaned per job |
| `data/processed/transcripts/`, `data/processed/summaries/` | Stored JSON documents | Generated user data |
| `reports/` | Exported report files | Generated user data |
| `.env` | The real credential file | **Secret — must never be committed or printed** |
| `.freebuff/` | Tool metadata (project id) | Not application code |
