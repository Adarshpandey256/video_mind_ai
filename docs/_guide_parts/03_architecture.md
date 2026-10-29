## Chapter 4 — Architecture and complete system design

### 4.1 The shape of the system in one paragraph

VideoMind AI is a **layered, single-process application**. Streamlit renders every page in one
Python process, so there is no HTTP API between the UI and the backend: the UI imports service
classes and calls their methods directly. Data cannot leak upward by accident because each stage
returns a *typed model*; errors cannot be swallowed silently because each stage returns an
*outcome enum* explaining why it failed.

```
        +---------------------------------------------------------------+
        |  PRESENTATION (Streamlit, one process)                        |
        |  app.py -> src/ui/layout.py -> {transcription_view,           |
        |                               summarization_page,             |
        |                               chat_page, reports_page,        |
        |                               styles/components}              |
        +---------------------------|-----------------------------------+
                                    | plain function/class calls
        +---------------------------v-----------------------------------+
        |  ORCHESTRATION (own the workflow, report progress)            |
        |  video_pipeline.VideoPipeline | summarization_service         |
        |  rag.service.RagService      | reports.report_builder        |
        +------|----------------|---------------|----------------|-------+
               |                |               |                |
        +------v------+  +------v------+ +------v------+  +------v-----------+
        | Step 2      |  | Step 3      | | Step 4      |  | Step 5           |
        | ingestion   |  | summarising | | retrieval   |  | reporting        |
        | source_     |  | preprocessor| | adapter     |  | report_models    |
        |  service    |  | chunk_sum.  | | chunking    |  | report_builder   |
        | url_        |  | merger      | | embeddings  |  | export_formats   |
        |  ingestion  |  | validation  | | vector_store|  | + 11 exporters   |
        | media_svc   |  | prompts     | | retriever   |  +------------------+
        | transcrip.  |  | timestamp   | | prompts     |           |
        +------|------+  +------|------+ | answer_gen  |           |
               |                |        +------|------+           |
        +------v----------------v---------------v------------------v-------+
        |  SHARED FOUNDATION                                                 |
        |  config/settings.py  ·  src/models/*  ·  src/utils/*               |
        |  llm_service.py + nvidia_client.py (one provider client)           |
        |  *_storage.py (atomic JSON)  ·  transcript_service.py              |
        +----------------------------|---------------------------------------+
                                     |
        +----------------------------v---------------------------------------+
        |  PERSISTENCE / EXTERNAL                                           |
        |  data/processed/*.json   ·  chroma_data/   ·  logs/app.log         |
        |  FFmpeg + ffprobe (local)  ·  Whisper (local)                      |
        |  NVIDIA API (remote)  ·  yt-dlp targets (remote, optional)         |
        +-------------------------------------------------------------------+
```

**Diagram 4.1 — Layered architecture.** Read it top-down: the user acts in the presentation
layer; orchestration classes own each multi-step workflow; each step's building blocks are
listed; all of them stand on the shared foundation; and only that bottom row touches the disk,
the network, or local executables. *No component is drawn that does not exist in the code.*

### 4.2 Component responsibilities

| Layer | Components | What it may do | What it must never do |
| --- | --- | --- | --- |
| Presentation | `app.py`, `src/ui/*` | Render, collect input, call services, show honest status | Call the NVIDIA API directly, write files itself, invent progress |
| Orchestration | `VideoPipeline`, `SummarizationService`, `RagService`, `report_builder` | Sequence steps, enforce policies, report stages, return outcome enums | Skip validation, hide an error, return a fake success |
| Feature services | ingestion, media, transcription, summarisation, retrieval, export modules | One job each, with typed inputs/outputs | Know about Streamlit |
| Foundation | `config/settings.py`, `src/models/*`, `src/utils/*` | Provide validated configuration, typed models, helpers | Import from `src/ui` or from an orchestration module (keeps imports acyclic) |
| Persistence/external | storages, ChromaDB, FFmpeg, Whisper, NVIDIA, yt-dlp | Store/read bytes; do the heavy work | Be reachable from anywhere except through a service |

### 4.3 The AI and model integration layer

There is exactly **one** provider client in the whole application:

```
config/settings.py  --(api key, base_url, timeout, retries)-->  nvidia_client.create_nvidia_client()
                                                                        |
                                             get_nvidia_client() (lru_cache, one per process)
                                                                        |
                                          llm_service.LLMService.generate()/stream_generate()
                                                     |                        |
                            summary_generation.SummaryJsonClient    rag.answer_generator
                              (Steps 3 chunk/merge/single)          GroundedAnswerGenerator
```

**Diagram 4.2 — One provider client, two consumers.** Step 3 and Step 4 both go through
`LLMService`, which normalises retries, parameter quirks and error types. A test asserts this
architecture rule holds: `tests/test_rag_answer_generator.py::test_llm_service_is_still_the_single_nvidia_client`.

Local AI (no network): Whisper for speech-to-text and `sentence-transformers/all-MiniLM-L6-v2`
for embeddings. Both are cached with `lru_cache` so a Streamlit rerun does not reload a model.

### 4.4 The data storage layer

| Store | Location | Format | Written by | Read by |
| --- | --- | --- | --- | --- |
| Uploads | `data/uploads/` | Original bytes with a safe, unique name | `source_service.save_upload` | the pipeline (then deleted for uploads after transcription) |
| Job scratch | `data/temp/job-<hex>/` | Downloads + extracted WAV | `VideoPipeline.run` | the same run, removed in `finally` |
| Transcripts | `data/processed/transcripts/tr-*.json` | `TranscriptRecord` JSON, written atomically (temp file + `os.replace`) | `transcript_storage.save` | Steps 3/4/5, UI, tests |
| Summaries | `data/processed/summaries/sm-*.json` | `StructuredSummary` JSON, atomic | `summary_storage.save` | Steps 4/5, UI |
| Vector index | `chroma_data/<collection>/` + `chroma_data/chroma.sqlite3` | ChromaDB | `ChromaVectorStore.build_index` | `search`, `count`, `status` |
| Index registry | `chroma_data/index_registry.json` | `{video_id: IndexRecord}` JSON, atomic | `IndexRegistry.put/remove` | `status`, `delete`, staleness detection |
| Logs | `logs/app.log` | Rotating text, secrets redacted | `logging_config.configure_logging` | human debugging |

**Why a registry next to ChromaDB?** Chroma knows collections, not *videos*. The registry maps
`video_id → collection name + transcript fingerprint + embedding model id + counts + timestamps`,
which is what makes "stale index" and "embedding model changed" detectable instead of silently
wrong.

### 4.5 The retrieval and reporting layers

```
Question ──► RagService.ask
              ├─ invalid question? ─────────────► RagOutcome.INVALID_QUESTION
              ├─ no transcript? ────────────────► RagOutcome.NO_TRANSCRIPT
              ├─ provider not configured? ──────► RagOutcome.MISSING_CREDENTIALS
              ├─ index stale/missing? ──────────► RagOutcome.NO_INDEX / INDEX_UNAVAILABLE
              ├─ Retriever.retrieve(video_id, query)
              │     └─ ChromaVectorStore.search (scoped to ONE video collection)
              ├─ no passages? ──────────────────► RagOutcome.NO_RESULTS
              ├─ GroundedAnswerGenerator.generate(question, sources, history)
              │     ├─ render sources (untrusted, labelled S1..Sn)
              │     ├─ LCEL chain: prompt | LLMService adapter | StrOutputParser
              │     ├─ extract JSON (repair once if malformed)
              │     └─ validate: unknown citations dropped, unverified quotes dropped
              └─ RagResult(outcome=success, answer with resolved citations)

Report ──► reports_page → build_report(transcript, summary, chat_messages, options)
              └─ VideoMindReport ──► export_formats.build_export(report, key)
                    └─ RenderResult(bytes, warnings, omitted sections) → st.download_button
```

**Diagram 4.3 — Retrieval and reporting flows.** Both are "fail with a reason" pipelines: every
early exit has a named outcome, and nothing is generated when the evidence is missing.

### 4.6 How control and data move between components

| Step | Caller → callee | Data in | Data out |
| --- | --- | --- | --- |
| Start | `app.main` → `bootstrap` → `get_settings`, `ensure_directories`, `configure_logging` | — | `Settings` |
| Render | `app.main` → `layout.render_sidebar` → `layout.render_page(page_key, settings)` | settings | HTML/widgets |
| Ingest (upload) | `transcription_view.execute_transcription` → `source_service.save_upload` | uploaded file object | `StoredUpload` → `ValidatedSource` |
| Ingest (URL) | `...` → `url_ingestion_service.build_url_source` → `VideoPipeline.run` → `download_media` | URL string | `DownloadedMedia` → `ValidatedSource` |
| Process | `VideoPipeline.run` → `media_service.probe_media` → `prepare_audio_for_transcription` → `TranscriptionService.transcribe` → `TranscriptService.build_record/save` | `ValidatedSource` | `PipelineResult(transcript, stages, outcome)` |
| Summarise | `summarization_page.run_summarization` → `SummarizationService.summarize` | `TranscriptRecord`, `SummaryOptions` | `SummaryResult(summary, stages, coverage, outcome)` |
| Index | `chat_page.build_index` → `RagService.index_transcript` → adapter → chunker → embeddings → vector store | `TranscriptRecord` | `IndexResult(IndexInfo, outcome)` |
| Ask | `chat_page.ask_question` → `RagService.ask` → retriever → generator | question + history | `RagResult(RagAnswer with citations, outcome)` |
| Export | `reports_page._generate` → `build_report` → `build_export` | stored data + options | `ReportExport(name, bytes, mime)` |

### 4.7 Where errors are handled

There are **four** deliberate layers of error handling — each one narrows a technical failure into
something a student can read:

| Layer | Mechanism | Example |
| --- | --- | --- |
| Provider boundary | `nvidia_client` exception hierarchy + `LLMService._translate_error` | An `HTTP 401` becomes `NvidiaAuthenticationError` → "NVIDIA rejected the configured API key…" |
| Domain error mapping | `VideoPipeline._translate` maps any exception to `(PipelineOutcome, message)`; `_translate_ytdlp_error` maps download failures | A DNS failure becomes "the media server's host name could not be resolved…" instead of "check the link" |
| Outcome enums | `PipelineOutcome`, `SummaryOutcome`, `RagOutcome`, `IndexOutcome` | The UI can distinguish *missing credentials* from *provider failure* from *validation failure* |
| UI notices | `_set_notice(level, title, message)` + `st.error/warning/info` | The user sees the reason and what to do next; the transcript is never erased by a failed summary |

Nothing swallows an exception: `except` blocks either re-raise a translated error or record the
failure in the returned result. The only `except` that continues silently is the **progress
callback** (`_notify`, `RagService._notify`), which is deliberate: a UI callback must never break
the work it reports on (tested by
`test_progress_callback_receives_real_stage_labels` /
`test_a_failing_progress_callback_does_not_break_the_run`).

### 4.8 Synchronous vs asynchronous

**Everything is synchronous.** There is no `asyncio`, no thread pool for the AI work, no queue.
The design compensates in three ways:

1. **Progress callbacks.** Long steps accept `progress`/`on_stage` callables, so the UI shows the
   current real stage label instead of a fake percentage.
2. **Caching.** `get_nvidia_client`, `load_sentence_transformer`, `_persistent_client`,
   `detect_media_tooling`, `cuda_available_cached` and Whisper model loading are all cached, so
   the expensive part happens once per process.
3. **Duplicate-job and stale-lock guards.** `JobRegistry` marks a source as running, remembers a
   successful transcript id, and can `reset_stale`/`reset_running` so an interrupted run cannot
   block the source forever (`ISS-009`).

The consequence to be honest about: while Whisper is transcribing, the browser is waiting on that
one request. That is a known limitation (Chapter 17) with a documented future improvement
(background worker + status polling).

### 4.9 Which components talk to the outside world

| Component | External target | Direction | Guard |
| --- | --- | --- | --- |
| `llm_service` / `summary_generation` / `answer_generator` | NVIDIA API (`NVIDIA_BASE_URL`) | out | Timeout, retries, error translation, token budgets |
| `url_ingestion_service._download_direct` | Any public `http(s)` host | out | SSRF block-list, size cap, timeout, redirect re-validation |
| `url_ingestion_service._download_with_ytdlp` | yt-dlp-supported public pages | out | Same validation, cookie opt-in, error translation |
| `media_service` | `ffmpeg`, `ffprobe` executables | out (local process) | Argument lists (no shell), timeouts, captured output |
| `transcription_service` | local Whisper model files | local | Device resolution, language validation, memory-error hint |
| `embeddings` | local model files (`~/.cache/huggingface` or torch cache) | local | Batch size, cached per process |
| `vector_store` | local `chroma_data/` | local | Atomic registry writes, orphan cleanup |
| Nothing else | — | — | The UI itself performs **no** network or model call on page load; a test asserts this (`test_index_selection_never_generates_anything_on_load`, `test_loading_the_page_never_generates_a_summary`) |

### 4.10 Module dependency direction

```
app.py
  └── src.ui.layout ──► src.ui.{components,styles,transcription_view,summarization_page,chat_page,reports_page}
                          └── src.services.* / src.rag.service / src.reports.*
                                └── src.models.*  ·  src.utils.*  ·  config.settings
```

**Diagram 4.4 — Dependency direction.** Imports only ever point *downwards*. `config.settings`
imports nothing from `src/` except `src.models.summary_schemas` (for the summary mode/length
choices); `src/models/*` import nothing but `pydantic`, `src.utils.time_utils` and each other;
`src/utils/*` may import `config` and `src.models`. A dedicated test imports every module and
fails on any circular import.
