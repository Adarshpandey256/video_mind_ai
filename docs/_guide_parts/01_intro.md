# VideoMind AI — Complete Project Guide

**A beginner-to-advanced walkthrough of the actual source code**

| Item | Value |
| --- | --- |
| Project | VideoMind AI — an AI video assistant (ingest → transcribe → summarise → chat → export) |
| Document version | 1.0 |
| Document date | 26 October 2026 |
| Project root | `C:\ai video assistant` |
| Entry point | `app.py` |
| Measured size | 31,656 lines of Python in 105 files across `app.py`, `config/`, `src/`, `tests/`, `scripts/` (of which 30,165 lines are the application plus its original helper scripts; 1,491 lines are the PDF builder written for this handbook) |
| Verified test result | **651 passed** (`python -m pytest`, 48.84 s, 26 October 2026) |
| Audience | B.Tech CSE student, project viva, demonstrations, future development |

> **How to read this handbook.** Every claim about behaviour comes from the code in this
> workspace. Where something was verified by running it (a test suite, a live provider
> call, a real download) the chapter says so and names the evidence. Where something is
> only implemented (code exists, unit-tested with stubs) it is labelled
> *implemented, mocked-only*. Where something could not be verified, it is labelled
> *unverified*. Nothing here is invented: if a file, class or function is named, it exists.

**Where the facts came from.** This guide was written from: the real source tree
(105 Python modules inventoried with an AST tool, see `scripts/inventory_for_docs.py`), the
dependency files (`requirements.txt`, `requirements-later-stages.txt`), the configuration
module (`config/settings.py`), the project's own logs (`docs/DEVELOPMENT_ISSUES.md`,
`docs/PROJECT_STATUS.md`) and a fresh full test run executed on 26 October 2026.

---

## Table of contents

1. **Chapter 1 — Project introduction**
2. **Chapter 2 — Project objectives and requirements**
3. **Chapter 3 — Complete project folder structure**
4. **Chapter 4 — Architecture and complete system design**
5. **Chapter 5 — Step 1: Project foundation**
6. **Chapter 6 — Step 2: Video/audio ingestion and transcription**
7. **Chapter 7 — Step 3: NVIDIA Nemotron summarisation**
8. **Chapter 8 — Step 4: Retrieval-augmented chatbot (RAG)**
9. **Chapter 9 — Step 5: Report generation and export**
10. **Chapter 10 — Complete end-to-end execution flow**
11. **Chapter 11 — Important functions, classes, variables and imports**
12. **Chapter 12 — Data models and data flow**
13. **Chapter 13 — Dependencies and external technologies**
14. **Chapter 14 — Configuration, setup and running the project**
15. **Chapter 15 — Testing and debugging**
16. **Chapter 16 — Security, privacy and responsible use**
17. **Chapter 17 — Limitations and future improvements**
18. **Chapter 18 — Project viva and interview preparation**
19. **Chapter 19 — Glossary**
20. **Chapter 20 — Final project reference**

---

## Chapter 1 — Project introduction

### 1.1 What VideoMind AI is

VideoMind AI is a **single-process Streamlit application** that turns a long video or
audio recording into knowledge you can read, question and export. One Python process
(`app.py` launched with `streamlit run`) contains both the user interface and the backend
services. You give it a recording — by uploading a file or by pasting a URL — and it:

1. **acquires** the media (upload copy, or a *yt-dlp* download of a supported public page);
2. **inspects** it with `ffprobe` and **extracts** a clean 16 kHz mono audio track with FFmpeg;
3. **transcribes** it locally with **OpenAI Whisper**, producing timestamped segments;
4. **stores** the transcript as JSON on disk;
5. **summarises** the transcript with **NVIDIA Nemotron** through an OpenAI-compatible API,
   returning a validated structured summary (takeaways, topics, chapters, action items …);
6. **indexes** the transcript into a **ChromaDB** vector store using local
   `sentence-transformers` embeddings, so you can **ask questions** and get answers grounded
   in the recording, with citations that point at real timestamps;
7. **builds reports** from the stored transcript, summary and chat and exports them as
   **PDF, DOCX, TXT, Markdown, JSON, HTML, CSV (×3) and XLSX**, plus a concise **PPTX** deck.

### 1.2 The problem it solves

Recordings are long and one-dimensional. A 90-minute lecture does not tell you where the
answer to your question is, and you cannot search a video for "the moment the professor
explains overfitting". VideoMind AI solves three concrete problems:

| Problem | What the application does about it |
| --- | --- |
| You cannot read or search a video | Whisper produces a timestamped transcript you can read, search and download |
| Long recordings are impossible to review quickly | Nemotron summarisation produces short, structured summaries of a long transcript |
| You cannot ask a recording a question | The RAG chatbot retrieves the relevant passages and answers **from those passages**, citing timestamps |
| Notes for submissions/reports must be assembled by hand | The report builder assembles one document from the real stored data in 11 export variants |

### 1.3 Who would use it

- **Students** revising from recorded lectures: read the summary, jump to the timestamp of a
  topic, ask "what did she say about entropy?".
- **Anyone preparing notes** from talks, webinars, meetings or interviews.
- **Researchers/junior engineers** learning how a modern AI pipeline (ASR → LLM → embeddings
  → vector search → RAG → document export) fits together in one working application.

### 1.4 Why it is an "AI-powered" application

Four independent AI/ML techniques are used, each for a different job — none of them trivially
replaceable by ordinary code:

| Technique | Where | What it does that normal code cannot |
| --- | --- | --- |
| Automatic speech recognition (Whisper) | `src/services/transcription_service.py` | Converts audio waveforms into text with time ranges |
| Large language model (NVIDIA Nemotron) | `src/services/llm_service.py`, `summarization_service.py` | Writes prose summaries, extracts topics/decisions/action items |
| Embeddings (`sentence-transformers/all-MiniLM-L6-v2`) | `src/rag/embeddings.py` | Turns text into vectors so *meaning* can be compared, not just keywords |
| Retrieval-augmented generation | `src/rag/*` | Finds the relevant passages and makes the model answer **from them** with citations |

### 1.5 Main features and user benefits

| # | Feature | User benefit |
| --- | --- | --- |
| 1 | Upload a video/audio file or paste a URL | Two ways in; URL path supports supported public platforms and direct media links |
| 2 | Local Whisper transcription with timestamps | Private (audio never leaves the machine), works offline, gives real timestamps |
| 3 | Eight summary styles × three detail levels | A quick gist, a deep study summary, a meeting minute, a chapter breakdown … |
| 4 | Structured, validated output | Summaries are typed Python objects, not loose text; bad model output is repaired or refused |
| 5 | Retrieval chatbot with citations | Answers you can check: every citation resolves to a passage actually supplied to the model |
| 6 | Per-video isolation | Each video has its own index and its own conversation; questions never mix two recordings |
| 7 | Eleven export variants | One report, many formats — submit a PDF, edit a DOCX, analyse an XLSX |
| 8 | Honest diagnostics | The Settings page reports what is actually installed, configured and working |

### 1.6 Technology stack and why each piece is used

| Technology | Version (pinned) | Why this one |
| --- | --- | --- |
| **Python** | 3.12 (`.venv`, 3.12.2) | Modern typing (`X | None`), `dataclasses`, `match`-ready code; all chosen libraries support it |
| **Streamlit** | 1.65.0 | One process for UI + backend; no separate frontend to build; script-rerun model suits long batch jobs with progress messages |
| **pydantic / pydantic-settings** | 2.14.1 / 2.15.0 | Typed, validated configuration and data models; a bad value falls back instead of crashing |
| **python-dotenv** | 1.2.4 | Loads `.env` into the environment so secrets never live in code |
| **openai SDK** (pointed at NVIDIA) | 3.29.0 | Official, robust HTTP client for the OpenAI-compatible Nemotron endpoint |
| **openai-whisper + torch** | 20250625 / 2.14.1 | Local speech-to-text with word/segment timestamps; CPU wheel by default, CUDA optional |
| **yt-dlp** | 2026.8.19 | The standard, actively maintained extractor for public platforms |
| **FFmpeg / ffprobe** (external) | 9.0.2 on this machine | Media probing and audio normalisation; Whisper also needs it to read compressed audio |
| **langchain-core** | 1.6.12 | Prompt templates, runnables (LCEL) and the `Embeddings` interface — only these parts are used |
| **langchain-chroma + chromadb** | 1.1.0 / 1.5.9 | Persistent local vector store; one collection per video |
| **sentence-transformers** | 6.1.0 | Local, free embeddings (`all-MiniLM-L6-v2`, ~90 MB, downloaded on first index build) |
| **reportlab** | 5.1.0 | PDF via the Platypus layout engine (pagination, tables, page numbers) |
| **python-docx / openpyxl / python-pptx** | 1.2.0 / 3.1.5 / 1.0.2 | Real Office documents, generated fully in memory |
| **pytest / pytest-mock / pypdf** | 9.1.1 / 3.16.0 / 6.21.2 | 651 automated tests; `pypdf` reopens generated PDFs to prove they are real files |

### 1.7 What the application can and cannot do

**Can (implemented in this workspace):**

- Validate and store uploads, and validate/download from URLs with SSRF protection and limits.
- Extract audio with FFmpeg and transcribe locally with Whisper, producing timestamped segments.
- Summarise with the configured Nemotron model, including long transcripts (chunk → merge).
- Index and retrieve with real ChromaDB persistence and answer with validated citations.
- Export 11 report variants from stored data, with no extra model calls.
- Start and remain usable **without** an API key (the shell, upload flow, storage and exports still work).

**Cannot (by design or by external constraint — see Chapter 17):**

- It does **not** bypass access controls. Private, members-only, bot-checked or 403-refused
  media is reported honestly; optional authorized cookies you export yourself may be used.
- It does **not** run without FFmpeg: audio extraction and Whisper both need it.
- It does **not** persist chat history across application restarts (`ISS-006`).
- It does **not** guarantee hallucination-free answers; it constrains and cites them.
- It does **not** publish or deploy anywhere; it is a local application.

### 1.8 Concise description for a teacher or interviewer

> "VideoMind AI is a Streamlit application that turns long videos into searchable knowledge.
> It ingests an uploaded file or a public URL, extracts audio with FFmpeg, transcribes it
> locally with Whisper into timestamped segments, summarises it with NVIDIA's Nemotron model
> through an OpenAI-compatible API, indexes it into ChromaDB with local embeddings so a
> retrieval-augmented chatbot can answer questions with timestamp citations, and finally
> assembles everything into reports that export to eleven formats. It is fully modular —
> services, models, RAG and reports are separate packages — and it is covered by 651 automated
> tests that run without any network access."

### 1.9 Current limitations discovered during inspection

Detailed in Chapter 17; the short list:

1. `ISS-004` — a long transcript can exhaust the model's output budget on private reasoning
   before it writes the JSON answer. Mitigation documented; the run is reported honestly
   (`error_type=truncated`) rather than producing a partial summary silently.
2. `ISS-005` — Whisper `base` mis-heard a word in the live test ("trunks" → "punks"). A model
   accuracy issue, not a code defect.
3. `ISS-006` — chat history is session-scoped (in-memory), not persisted.
4. YouTube anonymous access is frequently refused with a bot check (`ISS-001`) or HTTP 403
   (`ISS-002`); handling was fixed and a public video was verified end-to-end, but the
   external restriction remains.
5. The workspace folder has a `.gitignore` but **is not a git repository** (`git status` →
   "not a git repository"), so nothing has been committed from here.

---

## Chapter 2 — Project objectives and requirements

### 2.1 Functional requirements

| ID | Requirement | Where it is implemented (verified paths) |
| --- | --- | --- |
| FR-1 | The application starts and shows a usable interface without an API key | `app.py` (`bootstrap`, `main`), `src/ui/layout.py` |
| FR-2 | Configuration is read from environment/`.env`, validated, with safe fallbacks | `config/settings.py` (`Settings`, `get_settings`, `load_environment`) |
| FR-3 | The API key is never displayed, logged or written to disk by the app | `Settings.nvidia_api_key` (`SecretStr`), `src/services/nvidia_client.py`, `src/utils/logging_config.py` (`RedactionFilter`) |
| FR-4 | Upload a video/audio file with validation | `src/services/source_service.py` (`validate_upload`, `save_upload`), `src/utils/file_utils.py` |
| FR-5 | Accept a URL, validate it, and refuse unsafe destinations | `src/services/url_ingestion_service.py` (`validate_url`, `address_is_blocked`, `assert_public_destination`) |
| FR-6 | Download supported public media with size/time/duration limits and cleanup | `url_ingestion_service.download_media`, `_download_direct`, `_download_with_ytdlp` |
| FR-7 | Inspect media and extract normalised audio | `src/services/media_service.py` (`probe_media`, `extract_audio`, `prepare_audio_for_transcription`) |
| FR-8 | Transcribe locally with Whisper, producing timestamped segments | `src/services/transcription_service.py` (`TranscriptionService.transcribe`, `_build_result`) |
| FR-9 | Persist transcripts and list/reopen/delete them | `src/services/transcript_storage.py`, `src/services/transcript_service.py` |
| FR-10 | Report processing status per stage, honestly | `src/services/video_pipeline.py` (`PIPELINE_STAGES`, `PipelineStageReport`), `src/ui/transcription_view.py` |
| FR-11 | Summarise a transcript in eight modes and three detail levels | `src/services/summarization_service.py`, `src/prompts/summary_prompts.py`, `src/models/summary_schemas.py` |
| FR-12 | Handle long transcripts (chunk, summarise, merge) | `src/services/transcript_preprocessor.py`, `chunk_summarizer.py`, `summary_merger.py` |
| FR-13 | Validate model output into typed objects; never fabricate owners/deadlines/quotes | `src/services/summary_validation.py`, `src/services/timestamp_service.py` |
| FR-14 | Persist and reuse summaries | `src/services/summary_storage.py` |
| FR-15 | Normalise transcripts and chunk them with timestamps preserved | `src/rag/transcript_adapter.py`, `src/rag/chunking.py` |
| FR-16 | Embed locally and store vectors persistently per video | `src/rag/embeddings.py`, `src/rag/vector_store.py` |
| FR-17 | Retrieve per video and answer with validated citations | `src/rag/retriever.py`, `src/rag/answer_generator.py`, `src/rag/service.py` |
| FR-18 | Keep a per-video conversation, isolated between videos | `src/ui/chat_page.py` (`history_key`, `get_history`, `set_history`) |
| FR-19 | Build a report from real stored data only | `src/reports/report_builder.py` (`build_report`, `section_availability`) |
| FR-20 | Export to PDF, DOCX, TXT, MD, JSON, HTML, CSV ×3, XLSX, PPTX | `src/reports/export_formats.py` (`EXPORT_FORMATS`) plus one module per format |
| FR-21 | Provide diagnostics and a connection test | `src/services/health_service.py`, `src/ui/layout.py` |
| FR-22 | Guard against duplicate/stuck processing jobs | `src/services/video_pipeline.py` (`JobRegistry`), `src/ui/transcription_view.py` |

### 2.2 Non-functional requirements

| ID | Requirement | How it is met in this codebase |
| --- | --- | --- |
| NFR-1 | **Modularity** | Four layers: `src/models` (data), `src/services` (work), `src/rag` (retrieval), `src/ui` + `src/reports` (presentation/output). No circular imports — asserted by `tests/test_app_foundation.py::test_modules_import_without_circular_imports` |
| NFR-2 | **Honesty about failure** | Outcome enums (`PipelineOutcome`, `SummaryOutcome`, `RagOutcome`, `IndexOutcome`) exist so the UI can branch on *why*, and success flags are computed (`PipelineResult.succeeded`, `SummaryResult.succeeded`, `RagResult.succeeded`) |
| NFR-3 | **No silent truncation** | Oversized inputs are refused with an actionable message (`summary_max_transcript_minutes`, `rag_max_chunks_per_video`, `report_max_characters`) |
| NFR-4 | **Testability** | 651 tests; every external service is injected or mocked (`FakeClient`, `FakeEmbeddings`, `StubLLM`, `FakeYoutubeDL`); no network needed |
| NFR-5 | **Local-first privacy** | Transcription and embeddings run locally; only summarisation and answering send text to NVIDIA |
| NFR-6 | **Security** | SSRF blocking, path-traversal-safe filenames, HTML escaping, CSV formula neutralisation, secret redaction in logs |
| NFR-7 | **Performance sanity** | Cached clients/models (`lru_cache`), bounded context sent to the model (`retrieval_max_context_chars`), chunk caps, CPU-friendly defaults |
| NFR-8 | **Readability** | Module docstrings on every module; comments explain *why* (see `_translate_ytdlp_error`, `config/settings.py`) |
| NFR-9 | **Reproducibility** | Exact pinned versions in `requirements.txt`; rationale for each block in `requirements-later-stages.txt` |

### 2.3 User inputs and application outputs

| Input | Format | Validation |
| --- | --- | --- |
| Media file | Any of 10 video + 8 audio extensions (see §5.3) | Extension allow-list, size limit (500 MB default), non-empty, sanitised filename, NUL-byte/traversal rejection |
| URL | `http`/`https` | Scheme allow-list, host syntax, no embedded credentials, public-DNS resolution, SSRF block-list, redirect re-validation |
| Summary options | Mode, length, language, custom instruction, timestamps, action items | Enum-validated; a `custom` mode without an instruction disables generation |
| Chat question | Free text, ≤ 1000 characters (`rag_max_question_chars`) | Non-empty, length-checked |
| Report options | Sections, title, author, organisation, notes, timestamps on/off, format | Title required, ≥ 1 section, notes limited (`report_max_note_chars`) |

| Output | Format | Produced by |
| --- | --- | --- |
| Transcription result | `TranscriptionResult` → `TranscriptRecord` JSON in `data/processed/transcripts/` | `transcription_service.py`, `transcript_storage.py` |
| Transcript downloads | TXT and JSON | `TranscriptService.to_txt`, `to_json` |
| Structured summary | `StructuredSummary` JSON in `data/processed/summaries/` | `summarization_service.py`, `summary_storage.py` |
| Chat answer + citations | `RagAnswer` / `RagResult` in session state | `src/rag/service.py` |
| Report files | 11 variants, in-memory bytes offered as a download | `src/reports/export_formats.py` |
| Logs | Rotating `logs/app.log`, secrets redacted | `src/utils/logging_config.py` |

### 2.4 External services and prerequisites

| Prerequisite | Type | Required for | Failure behaviour when absent |
| --- | --- | --- | --- |
| NVIDIA API key + endpoint | Remote service | Step 3 summaries, Step 4 answers | Reported as `missing_credentials`; transcription and exports still work |
| FFmpeg + ffprobe | Local executables | Audio extraction (and Whisper's own decoding) | `MediaToolingError` with install instructions; pipeline stops at the validation stage |
| Whisper checkpoint | Local model download | Transcription | `WhisperUnavailableError` explaining the install step |
| `all-MiniLM-L6-v2` | Local model download (~90 MB, first index build) | Embeddings | `EmbeddingError` → `IndexOutcome.EMBEDDING_UNAVAILABLE` |
| yt-dlp | Python package | URL ingestion of platform pages | Clear "install yt-dlp" message; upload still works |
| Disk space | Local filesystem | Storage of transcripts, summaries, ChromaDB | `TranscriptStorageError` / `SummaryStorageError` / `VectorStoreError` |

### 2.5 Hardware and software requirements

| Item | Requirement | Evidence |
| --- | --- | --- |
| OS | Windows (verified here), macOS/Linux should work (paths use `pathlib`) | `docs/PROJECT_STATUS.md` environment table; `Path` usage throughout |
| Python | 3.12 | `.venv/Scripts/python -V` → `Python 3.12.2` |
| RAM | 8 GB comfortable for Whisper `base` on CPU; larger Whisper models and `torch` need more | `WHISPER_MODEL_CHOICES` docstring warns about size; `test_memory_error_suggests_a_smaller_model` |
| GPU | Optional. `WHISPER_DEVICE=auto|cpu|cuda`; CUDA is used only if PyTorch reports it | `TranscriptionService.cuda_available` / `resolve_device` |
| Disk | ~2 GB for `torch` + models, plus space for transcripts and the Chroma index | `chroma_data/` present; `data/processed/` holds JSON documents |
| Network | Required for NVIDIA calls and URL ingestion; **not** required for transcription, retrieval, or export of stored data | Tests run fully offline (651 passed with no key) |

### 2.6 Environment variables and configuration

Configuration lives in one place — `config/settings.py` (`class Settings(BaseSettings)`), which
maps each field to an upper-case environment variable (`nvidia_model` ← `NVIDIA_MODEL`). Measured
defaults from the code:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NVIDIA_API_KEY` | *(unset)* | Provider credential (`SecretStr`; placeholder values are treated as "missing") |
| `NVIDIA_MODEL` | `nvidia/nemotron-3.5-lightning-30b-a3b` | Summarisation + answering model |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint |
| `NVIDIA_TIMEOUT_SECONDS` / `NVIDIA_MAX_RETRIES` | `60` / `2` | Per-request transport behaviour |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` | `0.2` / `1024` | Step 1 generation defaults (connection test) |
| `LOG_LEVEL` / `APP_DEBUG` | `INFO` / `False` | Logging verbosity |
| `MAX_UPLOAD_SIZE_MB` | `500` | Upload limit (mirrors `.streamlit/config.toml` `maxUploadSize`) |
| `WHISPER_MODEL` / `WHISPER_DEVICE` / `WHISPER_LANGUAGE` | `base` / `auto` / *(auto-detect)* | Transcription |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` | `ffmpeg` / `ffprobe` | External tool discovery |
| `MAX_VIDEO_DURATION_SECONDS` | `7200` | Refuse longer recordings |
| `MAX_DOWNLOAD_SIZE_MB` / `URL_DOWNLOAD_TIMEOUT_SECONDS` / `URL_DOWNLOADS_ENABLED` | `1024` / `120` / `True` | Download limits and kill switch |
| `YTDLP_COOKIES_FILE` / `YTDLP_COOKIES_FROM_BROWSER` | *(unset)* | **Optional** authorized access for pages that refuse anonymous requests |
| `SUMMARY_MODE` / `SUMMARY_LENGTH` / `SUMMARY_LANGUAGE` | `quick` / `standard` / *(transcript language)* | Step 3 defaults |
| `SUMMARY_MAX_TOKENS` | `16384` | Output budget (shared with the model's private reasoning — see Chapter 7) |
| `SUMMARY_MAX_TRANSCRIPT_MINUTES` | `480` | Refuse longer transcripts before any model call |
| `SUMMARY_MAX_CHUNKS` / `SUMMARY_CHUNK_MAX_TOKENS` / `SUMMARY_CHUNK_OVERLAP_TOKENS` | `40` / `6000` / `200` | Long-transcript chunking |
| `SUMMARY_INTERMEDIATE_MAX_TOKENS` / `SUMMARY_MERGE_MAX_TOKENS` | `900` / `4096` | Merge budgets |
| `EMBEDDING_MODEL_NAME` / `EMBEDDING_DEVICE` / `EMBEDDING_BATCH_SIZE` | `sentence-transformers/all-MiniLM-L6-v2` / `auto` / `32` | Step 4 embeddings |
| `RETRIEVAL_CHUNK_SIZE` / `RETRIEVAL_CHUNK_OVERLAP` / `RETRIEVAL_MIN_CHUNK_SIZE` | `1200` / `200` / `200` | Chunking (characters, not tokens) |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MAX_CONTEXT_CHARS` / `RETRIEVAL_INCLUDE_SUMMARY` | `4` / `6000` / `True` | Retrieval |
| `RAG_ANSWER_MAX_TOKENS` / `RAG_TEMPERATURE` / `RAG_MAX_QUESTION_CHARS` | `8192` / `0.1` / `1000` | Answering |
| `RAG_MAX_CHUNKS_PER_VIDEO` | `2000` | Index size cap |
| `CHROMA_PERSIST_DIRECTORY` | *(unset → `<base>/chroma_data`)* | Vector store location |
| `REPORT_PDF_FONT_PATH` / `REPORT_PDF_FONT_SIZE` | *(system font search)* / `10` | PDF typography (Unicode) |
| `REPORT_MAX_CHARACTERS` / `REPORT_MAX_NOTE_CHARS` | `5000000` / `2000` | Report size guards |
| `TRANSCRIPT_RETENTION_DAYS` | `0` (disabled) | Optional automatic transcript pruning |
| `APP_BASE_DIR` | project root | Base for all data directories |

A safe template with placeholders only ships as `.env.example`. The real `.env` is listed in
`.gitignore`; **this workspace is not a git repository**, so nothing has been committed (see
Chapter 16).

### 2.7 Security and privacy considerations

| Consideration | Implemented safeguard | Evidence |
| --- | --- | --- |
| Credential handling | `SecretStr`, placeholder detection, one client factory, never logged | `config/settings.py`, `src/services/nvidia_client.py`, `src/utils/logging_config.py` |
| Log leakage | `RedactionFilter` rewrites keys/authorization headers before they reach handlers | `tests/test_app_foundation.py::test_log_records_are_redacted` |
| SSRF | Private/loopback/link-local/multicast/reserved address blocking, including IPv4-mapped, 6to4 and NAT64 unwrapping; redirects re-validated | `tests/test_url_ingestion.py` (67 tests) |
| Path traversal | `sanitize_filename`, `unique_upload_path`, `is_inside` containment checks | `tests/test_source_service.py` |
| HTML injection | All report HTML passes through `html.escape`; links limited to `http`/`https` | `tests/test_export_html.py` |
| CSV/Excel formula injection | `protect_csv_cell`, `_set_text` write leading-`=` text as text | `tests/test_export_csv_xlsx.py` |
| Prompt injection from untrusted transcripts | Transcript delimited with explicit markers and declared as data, never instructions | `src/prompts/summary_prompts.py`, `src/rag/prompts.py`, `tests/test_summary_prompts.py::test_injection_text_stays_inside_the_transcript_region` |
| Data leaving the machine | Only transcript text and your question go to NVIDIA; audio never leaves | `summarization_service`, `rag/answer_generator` |
| Unauthorized media access | Access controls are never bypassed; optional cookies are user-exported and validated | `_cookie_options`, `docs/DEVELOPMENT_ISSUES.md` ISS-001/ISS-002 |

