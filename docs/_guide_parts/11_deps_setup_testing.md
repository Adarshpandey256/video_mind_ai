## Chapter 13 — Dependencies and external technologies

### 13.1 Direct dependencies (from `requirements.txt`, exact pinned versions)

| Package | Version | Role in this project | Used by | Local or remote | Required for |
| --- | --- | --- | --- | --- | --- |
| `streamlit` | 1.65.0 | The entire UI + app server, in one process | `app.py`, all of `src/ui` | Local | Everything the user sees |
| `python-dotenv` | 1.2.4 | Loads `.env` into the environment | `config/settings.load_environment` | Local | Configuration |
| `pydantic` | 2.14.1 | Validation/serialisation for every model | `src/models/*`, `src/reports/report_models.py` | Local | Whole application |
| `pydantic-settings` | 2.15.0 | Environment-backed `Settings` class | `config/settings.py` | Local | Whole application |
| `openai` | 3.29.0 | HTTP client for the OpenAI-compatible Nemotron endpoint | `src/services/nvidia_client.py` | **Remote** (when called) | Steps 3 and 4 answering |
| `openai-whisper` | 20250625 | Local speech-to-text | `src/services/transcription_service.py` | Local (model download) | Transcription |
| `torch` | 2.14.1 (CPU wheel) | Whisper's backend; also used for embeddings | Whisper, `sentence-transformers` | Local | Transcription and embeddings |
| `yt-dlp` | 2026.8.19 | Extracts public platform media | `url_ingestion_service._download_with_ytdlp` | **Remote** | URL ingestion of platform pages |
| `langchain-core` | 1.6.12 | `ChatPromptTemplate`, LCEL runnables, `Embeddings` interface | `src/rag/answer_generator.py`, `embeddings.py`, tests | Local | Step 4 |
| `langchain-chroma` | 1.1.0 | Chroma `VectorStore` wrapper | `src/rag/vector_store.py` | Local | Step 4 |
| `chromadb` | 1.5.9 | Persistent local vector database | `src/rag/vector_store.py` | Local | Step 4 |
| `sentence-transformers` | 6.1.0 | Local embedding model (`all-MiniLM-L6-v2`, ~90 MB on first use) | `src/rag/embeddings.py` | Local | Step 4 |
| `reportlab` | 5.1.0 | PDF via the Platypus layout engine | `src/reports/pdf_exporter.py` | Local | PDF export |
| `python-docx` | 1.2.0 | DOCX | `docx_exporter.py` | Local | Word export |
| `openpyxl` | 3.1.5 | XLSX | `xlsx_exporter.py` | Local | Excel export |
| `python-pptx` | 1.0.2 | PPTX | `pptx_exporter.py` | Local | PowerPoint export |
| `pytest` | 9.1.1 | Test runner | `tests/` | Local | Development |
| `pytest-mock` | 3.16.0 | `mocker` fixture | tests | Local | Development |
| `pypdf` | 6.21.2 | Reopens generated PDFs to prove they are real | `tests/test_export_pdf.py` | Local | Development (test-only) |

### 13.2 Formats implemented with the standard library only

TXT (`textwrap`), Markdown (`re`), JSON (`json`), HTML (`html`, `urllib.parse`) and CSV (`csv`, `io`)
need **no** third-party package. `requirements.txt` says so explicitly ("TXT, Markdown, JSON, HTML and
CSV exports use the standard library only").

### 13.3 External (non-Python) prerequisites

| Tool | Why it cannot be a pip package | How it is verified here |
| --- | --- | --- |
| **FFmpeg** | A native executable; Whisper also shells out to it to decode compressed audio | `ffmpeg -version`; `media_service.detect_media_tooling`; this machine has 9.0.2 |
| **ffprobe** | Ships with FFmpeg; used for inspection | `ffprobe -version`; `probe_media` |
| **Whisper checkpoint** | Downloaded model weights (e.g. `base` ≈ 140 MB) on first use | `TranscriptionService.load_model`; cached per process |
| **Embedding model** | Downloaded weights (`all-MiniLM-L6-v2`, ~90 MB) on first index build | `load_sentence_transformer` |

### 13.4 Transitive dependencies (selected, pulled in automatically)

`torch` brings `numpy`, `sympy`, `networkx`, `filelock`; `openai-whisper` brings `tiktoken`,
`numba`, `tqdm`, `more-itertools`; `chromadb` brings `onnxruntime`, `pypika`, `posthog`, `httpx`;
`sentence-transformers` brings `huggingface-hub`, `transformers`, `scikit-learn`, `scipy`;
`streamlit` brings `pyarrow`, `altair`, `pandas`, `tornado`; `python-docx`/`python-pptx` both bring
`lxml`. **They are transitive**: the project code does not import them directly (a few are used
indirectly, e.g. `httpx` inside the OpenAI SDK, and `tiktoken` for token estimates when importable).

### 13.5 Beginner-friendly explanation of the key technologies

| Technology | One-paragraph explanation |
| --- | --- |
| **Python** | A high-level, dynamically typed language. This project uses modern typing, `dataclasses`, f-strings and context managers. |
| **Streamlit** | A Python framework that turns a script into a web app: widgets are function calls, and every interaction re-runs the script top to bottom. That is why this project caches expensive objects and stores state in `st.session_state`. |
| **Whisper** | OpenAI's speech recognition model. It takes audio and returns text with time ranges. Running it locally means no per-minute cost and no audio leaving the machine, but it is slower than a hosted API. |
| **NVIDIA Nemotron** | The LLM used for summaries and answers, served through an *OpenAI-compatible* endpoint, which is why the standard `openai` client works unchanged with a different `base_url`. |
| **Prompt engineering** | Choosing the system/user messages, the JSON contract and the explicit data markers so the model's output is parseable and grounded. |
| **Embeddings** | Vectors that represent meaning. `all-MiniLM-L6-v2` maps text to 384 numbers; similar meanings end up close in that space. |
| **ChromaDB** | A vector database. It stores vectors with metadata and can answer "which stored vectors are nearest this query vector". This project uses the persistent local mode, one collection per video. |
| **LangChain (core + chroma)** | Supplies the prompt template, the composable runnable chain and the vector-store wrapper. Only those pieces are used — no agents, no memory abstraction. |
| **FFmpeg / ffprobe** | The universal media toolkit: ffprobe reports what a file contains, FFmpeg converts it. Both are called as subprocesses with argument lists (never a shell). |
| **yt-dlp** | The maintained successor to youtube-dl: reads a public page's metadata and downloads the media it is allowed to serve. |
| **ReportLab Platypus** | ReportLab's high-level layout system: you describe flowables (paragraphs, tables, spacers) and it paginates them, which is how the PDF report gets page numbers. |
| **pytest** | The test runner. Fixtures (`tmp_path`, `monkeypatch`) let the suite create isolated workspaces and replace network calls with stubs. |

### 13.6 "Listed" is not "working" — the distinctions that matter

| Statement | Truth in this workspace |
| --- | --- |
| All 19 pinned packages are installed | **Verified**: the suite imports and exercises them (651 tests pass, including model/format-opening tests) |
| `streamlit`, `openai`, `pydantic`, `reportlab` are actually used | Verified by imports in the source |
| `pypdf` is a runtime dependency | **No** — test-only |
| `langchain` (meta-package) and `langchain-community` are needed | **No** — deliberately absent (`requirements-later-stages.txt`) |
| `torch` supports CUDA here | **Unverified** — the installed wheel is the CPU build; CUDA would be used only if PyTorch reported it |
| Every listed technology works end-to-end live | Only the paths recorded in `docs/PROJECT_STATUS.md` were live-verified (2026-10-25). Anything not listed there is implemented + unit-tested, not live-verified |

---

## Chapter 14 — Configuration, setup and running the project

### 14.1 Prerequisites

| Requirement | Verified value | How to check |
| --- | --- | --- |
| Python | 3.12 (this venv: 3.12.2) | `py -0p` (Windows) or `python -V` |
| pip | bundled | `python -m pip --version` |
| FFmpeg + ffprobe | 9.0.2 on this machine | `ffmpeg -version`, `ffprobe -version` |
| ~2 GB disk | `torch` + models + data | — |
| NVIDIA API key | needed only for Steps 3 and 4 answers | https://build.nvidia.com (create your own key) |

### 14.2 Install (Windows, matching this workspace)

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

**Keep the lock:** `requirements.txt` contains the exact resolved versions, so installing it
reproduces this environment. Do not upgrade packages casually — `requirements-later-stages.txt`
records why each block exists.

### 14.3 FFmpeg

```bash
# Windows (the build used on this machine)
winget install Gyan.FFmpeg

# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

Then **reopen the terminal** (PATH changes are not picked up by a running shell) and verify with
`ffmpeg -version`. The application checks this itself: a missing FFmpeg produces
`MediaToolingError` with install instructions, shown as `TOOLING_MISSING` on the Home page, and the
Settings page reports it in `check_media_dependencies`.

### 14.4 Configure `.env` safely

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Then edit `.env` and set your own key:

```
NVIDIA_API_KEY=your_own_key_here
NVIDIA_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
```

Rules that the code enforces for you:

- **Never commit `.env`.** It is listed in `.gitignore` (along with `cookies*.txt`, `data/`, `logs/`,
  `reports/*`, `chroma_data/`).
- Placeholder values (`replace_with_your_new_nvidia_api_key`, `your_api_key_here`, `changeme`, …) are
  treated as "no key", so a copied template behaves honestly instead of failing with a 401.
- The key is stored as `SecretStr`, never logged (the log filter redacts it), never shown in the UI
  (`nvidia_key_status` prints words), and never written into a summary, prompt or report.
- **If a key was ever pasted into chat, a screenshot or a repository, revoke it and generate a new
  one** — rotating is cheaper than an unexpected bill.

**Note about this workspace:** `.gitignore` is present, but the folder is **not** a git repository
(`git status` → "not a git repository"). Nothing has been committed from here, so the "never commit"
rules are currently precautionary rather than enforced by git.

### 14.5 Start the application

```bash
# Option A - the project's launcher (Windows)
run_backend.bat

# Option B - direct (any OS)
streamlit run app.py
```

Both start one process on **http://localhost:8501**. This handbook was written while verifying that
exact startup: the server logged `Local URL: http://localhost:8501` and
`GET /_stcore/health` answered `ok`.

To open only the browser: `run_frontend.bat` (it does not start a server).

### 14.6 Run the tests

```bash
.venv/Scripts/python -m pytest                 # no key, no network required
```

**Measured result in this workspace (26 October 2026):**

```
============================ 651 passed in 48.84s =============================
```

**Useful tip (found while writing this guide).** `pytest.ini` sets `addopts = -q`, and adding another
`-q` on the command line makes the verbosity `-qq`, which **suppresses the final summary line**. If
you want to see "651 passed in …", either run plain `python -m pytest` or override the options:

```bash
python -m pytest -o addopts="--strict-markers"        # shows the summary line
python -m pytest tests/test_rag_service.py -q         # one module
python -m pytest -k citation                          # by name
```

### 14.7 Optional live media verification

```bash
.venv/Scripts/python scripts/verify_step2_media.py
```

This script synthesises a speech fixture with the Windows speech engine, builds a video fixture (plus
one with no audio track), pushes both through the **real** upload store and pipeline, and prints a
checklist. It needs FFmpeg and downloads the Whisper checkpoint on first use — that is exactly why it
is **not** part of the pytest suite.

### 14.8 Diagnosing common startup failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| `streamlit: command not found` | Virtual environment not activated | Activate it, or use `python -m streamlit run app.py` |
| Port 8501 already in use | Another instance is running | `streamlit run app.py --server.port 8502` (or stop the other process) |
| Page shows "NVIDIA key not configured" | `.env` missing or placeholder | Copy `.env.example` to `.env` and add your key, then restart |
| `TOOLING_MISSING` / "FFmpeg is not available" | FFmpeg missing or not on PATH | Install it (`winget install Gyan.FFmpeg`), reopen the terminal, verify `ffmpeg -version` |
| Settings → Diagnostics reports a configuration problem | Invalid environment value | Fix it; the exact field name is shown (never the value) |
| Upload rejected at ~75 MB despite `MAX_UPLOAD_SIZE_MB=500` | `.streamlit/config.toml` `maxUploadSize` not aligned | Change both values together (documented in that file) |
| Transcription is very slow | CPU Whisper | Use a smaller model (`WHISPER_MODEL=tiny`/`base`) or a CUDA build of torch |
| First index build appears to hang | The ~90 MB embedding model is downloading | Wait; it is cached for later runs |

### 14.9 Cleaning generated data

| To remove | Delete |
| --- | --- |
| One transcript / summary | Use the UI (Saved transcripts / Saved summaries have Delete buttons) |
| All transcripts and summaries | `data/processed/transcripts/*`, `data/processed/summaries/*` |
| The whole vector index | Delete `chroma_data/` (or use Delete index on the chat page for one video) |
| Exported reports | `reports/` |
| Logs | `logs/app.log` (kept rotated by `RotatingFileHandler`) |
| Temporary media | Normally automatic (the pipeline removes its job directory); stale folders can be deleted under `data/temp/` |

---

## Chapter 15 — Testing and debugging

### 15.1 The suite at a glance

| Metric | Value (measured 26 October 2026) |
| --- | --- |
| Test modules | 32 `test_*.py` + 4 support modules |
| Tests collected | **651** |
| Result | **651 passed, 0 failed, 0 skipped** |
| Wall time | 48.84 s |
| Network / API key required | **No** — all provider, download and model calls are stubbed |
| Real binaries used | FFmpeg/ffprobe detection test, Whisper installation test, `openpyxl`/`python-docx`/`python-pptx`/`pypdf` reopening generated files |

### 15.2 What each test module verifies

| Module | Tests | Scope | Mocks used |
| --- | --- | --- | --- |
| `test_app_foundation.py` | 37 | Imports (no circular imports), navigation registry, roadmap, health checks, connection-check states, file validation, log redaction, and `AppTest` renders of the whole app | `AppTest`; no key configured |
| `test_settings.py` | 18 | Defaults, placeholder keys, env overrides, URL fallback, log level, list parsing, invalid config fallback, `.env` loading, directory creation | `monkeypatch` |
| `test_llm_service.py` | 22 | Request parameters, overrides, empty prompt, empty response, reasoning-trace hiding, every provider error translation, `max_tokens`→`max_completion_tokens` retry, temperature retry, streaming, connection tests, token-limit-not-network | `FakeClient`, `FakeCompletions` |
| `test_nvidia_client.py` | 7 | Endpoint/limits, no key exposure, missing key error, caching, no request at construction | `monkeypatch` |
| `test_source_service.py` | 14 | Upload validation, streaming size enforcement, sanitising, collisions, containment, cleanup scope | `tmp_path` |
| `test_url_ingestion.py` | 67 | URL syntax, free text, schemes, credentials, local hostnames, private/metadata addresses, IPv4-mapped/6to4/NAT64, DNS failure, sizes, timeouts, redirects, caching, yt-dlp success/failures, cookie opt-in rules, bot check, 403, DNS-not-the-link | `FakeResponse`, `FakeYoutubeDL`, patched DNS |
| `test_media_service.py` | 18 | Real tooling detection, missing FFmpeg, ffprobe parsing (cover art, stream duration, silent video), argument-list safety, extraction failure/timeout/no-audio cleanup, pass-through, duration limit | `monkeypatch` on `subprocess` |
| `test_transcription_service.py` | 28 | Validated result, options, auto language, empty/short/inconsistent/negative timestamps, ordering, model load failure, exception wrapping, memory hint, unknown model/language, missing file, Whisper-not-installed, model caching/unload, device resolution, progress labels, Whisper really installed | `FakeModel` |
| `test_video_pipeline.py` | 27 | Success path and every stage report, job cleanup (success and failure), options forwarding, `store=False`, URL download then cleanup, invalid input, missing FFmpeg, download failure, blocked URL, inspection failure, duration limit, missing audio, transcription failure, invalid transcript, storage failure, unexpected error mapping, no transcript after failure, registry duplicate/stale/reset, job-key stability | Stub tooling/transcriber/probe/prepare |
| `test_transcript_storage.py` | 22 | Save/load/list/delete, unique ids, unsafe ids, invalid JSON, incomplete docs, storage failure, newest-first, pruning, exports, default location | `tmp_path` |
| `test_transcript_preprocessor.py` | 17 | Segment rendering, fallbacks, timestamps on/off, token estimates, single-chunk vs chunked, ordering/coverage, timestamp survival, overlap behaviour, reserved tokens shrink the budget, no segment lost | `tmp_path` |
| `test_summarization_service.py` | 47 | Single request for short input, rejected empty/missing/too-long transcripts, all modes, custom mode, typed parsing, tolerance of missing fields, empty response, malformed JSON + repair, truncation naming the budget, reasoning-budget regression, provider exception, connection message, missing credentials, rate-limit retries, storage failure still returns the summary, progress labels and safety, action-items switch, transcript association, takeaway excerpt from the transcript, chunked path, ordering, timestamps in prompts, single merge request, dedupe, partial results, all-chunks-failed, auth stop, injection/config safety, credentials never in prompts/documents, no invented owner/deadline, service-free entry, storage round trip, corrupt documents skipped | `FakeClient`, `FakeLLM` |
| `test_summary_validation.py` | 29 | Timestamp keep/snap/drop rules, impossible intervals, no-timeline transcripts, clock strings, grounding, owner/deadline only when stated, placeholder owners, unverifiable excerpts, takeaways from strings/objects, dedupe, topics/chapters/decisions/points, minimal and alternative field names, empty response rejection, notes about repairs and mismatches, fingerprints | none needed |
| `test_summary_prompts.py` | 12 | System-prompt fidelity rules, transcript fencing, injection containment, chunk/merge separation, preferences block, timestamp switch, JSON contract matches the model, every mode has an instruction, custom instruction cleaning, no credentials, bounded repair prompt | `tmp_path` |
| `test_summarization_ui.py` | 14 | Mode list, options reaching the service, no generation on load, custom-without-instruction disabled, single service call, partial shown as warning, missing credentials keep the transcript, failed run keeps the transcript, reopen/delete saved, listing per transcript, guidance when no transcript, step 1/2 surfaces still render, reuse caption honesty | `AppTest`, `StubClient` |
| `test_rag_transcript_adapter.py` | 20 | Whitespace/Unicode, timestamp order, dropped blanks, no fabricated timestamps, impossible intervals, negative values, full-text fallback, empty refusal, unsafe ids, source-name fallback, fingerprint stability/sensitivity, documented view | none |
| `test_rag_chunking.py` | 21 | One-chunk short transcripts, ordered chunks, sentence boundaries, overlap on/off, no invented timestamps, empty refusal, blank skipping, Unicode/Hindi, deterministic ids, tiny-final-chunk merge, budget enforcement, Chroma-safe metadata, summary-as-context without timestamps, summary text content, empty summary, unsafe id, full-text transcripts, fingerprint change, impossible interval, whitespace collapse | `FakeEmbeddings` where needed |
| `test_rag_vector_store.py` | 15 | Build/status/counts, persistence across instances, staleness, embedding-model mismatch, empty chunk rejection, invalid id, ranked search with timestamps, **no cross-video search**, unindexed refusal, blank query, summary exclusion, deterministic rebuild, delete + registry, missing collection, registry resilience | `FakeEmbeddings` |
| `test_rag_retriever.py` | 9 | Conversation-context detection and widening, ranked numbered sources, summary exclusion, extra-query merging, context budget, unindexed refusal, blank query, stale index refusal | Real store + `FakeEmbeddings` |
| `test_rag_answer_generator.py` | 17 | Citation resolution, unknown citation removal, quote verification, no invented citations, insufficient evidence, repair retry, failure after two bad answers, empty response, missing credentials, truncation message, real LCEL chain used, provider error translation, reference normalisation, quote tolerance, prompt marks passages untrusted, summary passages labelled as paraphrases, single-client rule | `StubChain`, `StubLLM` |
| `test_rag_service.py` | 29 | Index build/reuse/rebuild, staleness, invalid input, summary context inclusion/exclusion/fallback, stage callback safety, delete, refusals (no index/transcript/blank/overlong/unconfigured), citation resolution, summary source labelling, history use, no evidence, provider errors, **other-video isolation**, chat options override | `FakeEmbeddings`, stubs |
| `test_chat_ui.py` | 13 | Empty state, unindexed state, no generation on load, build button, stale rebuild prompt, delete confirmation, citations with real timestamps, follow-up history, failed answer retained, retry without duplicate, suggested question, per-video clear, per-video switching | `AppTest`, `StubRagService` |
| `test_report_models.py` | 20 | Option validation, dedupe, schema version, included sections, timestamp reporting, JSON-safe serialisation, safe stems, filenames, extension validation, impossible spans | none |
| `test_report_builder.py` | 30 | Real-data assembly, selection, omissions with reasons, summary-only reports, empty transcript, missing transcript refused, **never mixes two videos**, availability explanations, exact timestamps, no invented timestamps, Unicode, whitespace, dropped blank segments, chat pairing, no invented questions, dropped citations reported, unknown document type, size guard, oversized notes, full transcript, URL sanitising, local files never become URLs, default title | none |
| `test_export_pdf.py` | 11 | Parseable document, titles/sections/timestamps, page numbers, long transcripts, Unicode with a font, Latin fallback warning, configured font, timestamps off, no-timeline statement, omitted sections explained, keyless export | `pypdf` |
| `test_export_docx.py` | 9 | Real Office package, semantic headings, metadata/summary/takeaways, Unicode + timestamps, header/footer fields, chat citations and provenance, omissions, long transcript, timestamps-off statement | `python-docx`, `zipfile` |
| `test_export_text_formats.py` | 27 | TXT sections/timestamps/omissions/Unicode, Markdown hierarchy and escaping and fences, JSON validity/schema/content/secrets/NaN/datetimes, filename and MIME types, refused formats without data | none |
| `test_export_html.py` | 11 | Self-contained document, no external assets, escaping (content/title/metadata), sections/timestamps/citations, summary citations marked, omissions, Unicode, safe link only, section selection, unsafe scheme never linked | `re` |
| `test_export_csv_xlsx.py` | 13 | Transcript CSV rows/columns, missing timestamps, quoting/Unicode, chat citation columns, sources rows, formula neutralisation (CSV and Excel), transcript requirement, workbook sheets/headers/selection/Unicode, overview of omissions | `csv`, `openpyxl` |
| `test_export_pptx.py` | 7 | Real package, title slide + overview, summary/takeaways, sources and answers, no transcript dump, long content survives, works without chat or summary | `python-pptx` |
| `test_reports_ui.py` | 12 | Empty state, sections/formats listed, unavailable sections disabled with reason, PDF bytes + download, second format without regenerating, full transcript exported, title change invalidates bytes, chat inclusion, video switch isolation, section change invalidation, oversized refusal, keyless export | `AppTest` |
| `test_transcription_ui.py` | 8 | Workflow rendering with tool status, empty input rejected, blocked URL refused, successful URL job renders the transcript, duplicate guard, stuck-lock reset, reopening stored transcripts, settings page still works | `AppTest`, `FakePipeline` |

### 15.3 Which tests use mocks and which do not

| Category | Modules | Evidence they need no network |
| --- | --- | --- |
| Pure logic / models | `test_summary_validation`, `test_summary_prompts`, `test_report_models`, `test_report_builder`, `test_transcript_preprocessor`, `test_rag_chunking`, `test_rag_transcript_adapter` | No provider imports at all |
| Mocked external services | `test_llm_service`, `test_nvidia_client`, `test_url_ingestion`, `test_media_service`, `test_transcription_service`, `test_summarization_service`, `test_rag_answer_generator`, `test_rag_service`, `test_rag_vector_store`, `test_rag_retriever` | `FakeClient`, `FakeYoutubeDL`, `FakeResponse`, `FakeModel`, `FakeEmbeddings`, stubs |
| UI via `AppTest` | `test_app_foundation`, `test_transcription_ui`, `test_summarization_ui`, `test_chat_ui`, `test_reports_ui` | Page services replaced by stubs; `conftest.isolated_environment` removes provider configuration |
| Real binaries / real files (no network) | `test_media_service` (tooling detection), `test_transcription_service` (installation guard), `test_export_pdf/docx/pptx` (reopening files), `test_settings` (real `.env` handling) | Local only |

**Integration scenarios that are not covered by the automated suite** (they need a live key, network
or long CPU work): live Nemotron summarisation, live chunk-and-merge on a very long recording, live
answer generation, a real platform download inside pytest, and a real 2-hour transcription. Those were
run manually and recorded in `docs/PROJECT_STATUS.md`, `docs/DEVELOPMENT_ISSUES.md` and
`docs/FINAL_REPORT.md`.

### 15.4 How to run and read results

```bash
.venv/Scripts/python -m pytest -o addopts="--strict-markers"       # everything + summary line
.venv/Scripts/python -m pytest tests/test_rag_service.py            # one module
.venv/Scripts/python -m pytest -k "isolation or stale" -v           # by name
.venv/Scripts/python -m pytest --maxfail=1 -x                       # stop at the first failure
.venv/Scripts/python -m pytest -p no:cacheprovider -q               # ignore the cache plugin
```

### 15.5 How to read a stack trace (the three things to look at)

1. **The last line** — the exception type and message. This project's errors are written for humans:
   `DownloadFailedError: The media server's host name could not be resolved (DNS), even though the
   page itself was read.`
2. **The frames in *your* code** — the bottom-most frames under `src/`. In the ISS-003 traceback the
   relevant line was `url_ingestion_service.py, line 676, in _download_with_ytdlp → raise
   _translate_ytdlp_error(error) from error`, which showed the failure was being *translated*, not
   caused there. The cause was the `yt_dlp.utils.DownloadError` chained above it ("direct cause of").
3. **The `from error` chain** — this codebase uses `raise NewError(...) from original`, so the
   original technical cause is preserved. Read the "direct cause of the following exception" block.

Also read `logs/app.log`: every stage logs a structured line
(`Processing failed (stage=Acquiring media, outcome=download_failed, error=DownloadFailedError)`), and
secrets are already redacted.

### 15.6 Debugging playbooks

| Symptom | Where to look | Typical cause |
| --- | --- | --- |
| Ingestion fails | Home page stage list → `logs/app.log` `yt-dlp reported:` line → `_translate_ytdlp_error` branch | Bot check, 403, private media, DNS, size/timeout, missing yt-dlp |
| Transcription fails | `render_job_stages`; the Settings → Diagnostics media check | FFmpeg missing, no audio stream, model load failure, memory error, unknown language |
| Summary fails | Summarization page failure panel (`_render_failure`) + stage list | Missing key, transcript too long, malformed JSON twice, rate limit, truncated budget (`ISS-004`) |
| Chat refuses to answer | Chat page index state (`IndexInfo.describe`) | Not indexed, stale (changed transcript), embedding mismatch, no key, no retrieved evidence |
| Answer has no citations | The notes under the answer | The model cited passages that were not supplied, or quoted text that did not verify |
| Export unavailable | The format dropdown reason (`FORMAT_REQUIREMENTS`) | The required section is not selected or has no data |
| Report omits content | The report's own omission list | The section had no stored data; the builder records the reason |
| Wrong/missing timestamps | Section text "timestamp unavailable" | The transcript genuinely has no timeline (Whisper without segment times, or plain-text input) |
| Stuck "Job already running" | Home page reset control (`_render_stuck_job_reset`) | An interrupted run left a lock (`ISS-009`); the registry resets stale locks |

### 15.7 How to add a regression test (the project's own pattern)

1. Name it after the behaviour, not the function: `test_media_host_dns_failure_is_not_blamed_on_the_link`.
2. Build isolated input with the fixtures you have: `tmp_path`, `monkeypatch`, `make_transcript`,
   `make_settings`, `sample_summary`, `make_report`.
3. Replace the external service at the seam the page uses (e.g. monkeypatch `chat_page.service_for`)
   so the test drives the real page without network.
4. Assert the *honest* outcome: the error type, the message content, that nothing was stored, that a
   citation was dropped.
5. Add the reproduction steps and the verification to `docs/DEVELOPMENT_ISSUES.md` if it came from a
   real bug — the six `ISS-001` … `ISS-009` entries each name their tests.

### 15.8 Feature verification matrix (evidence-based)

| Feature | Implemented | Automated tests | Live verified | Status label |
| --- | --- | --- | --- | --- |
| Streamlit shell, navigation, settings, diagnostics | Yes | Yes (37) | Yes (this guide's startup check: `/_stcore/health` → ok) | **Implemented and tested** |
| Configuration, `.env`, secret handling, log redaction | Yes | Yes (18 + 7) | Yes (key status shown without the value) | **Implemented and tested** |
| Upload validation and safe storage | Yes | Yes (14) | Yes (via `verify_step2_media.py`) | **Implemented and tested** |
| URL validation and SSRF protection | Yes | Yes (67) | Yes (blocked/direct paths exercised) | **Implemented and tested** |
| Platform (yt-dlp) download | Yes | Yes (mocked) | Yes for a public video (2026-10-25); refused videos remain refused | **Implemented; live-verified on one public video; external restriction remains** |
| FFmpeg inspection + audio extraction | Yes | Yes (18) | Yes (real WebM → 16 kHz WAV) | **Implemented and tested (live-verified)** |
| Whisper transcription + timestamps | Yes | Yes (28) | Yes (CPU `base`) | **Implemented and tested (live-verified)** |
| Transcript persistence + TXT/JSON export | Yes | Yes (22) | Yes (record reloaded by Step 3) | **Implemented and tested (live-verified)** |
| Six-stage pipeline reporting + cleanup | Yes | Yes (27) | Yes | **Implemented and tested** |
| Summary single-pass generation | Yes | Yes (stubbed) | Yes (2026-10-25) | **Implemented; live-verified previously** |
| Summary chunk-and-merge for long input | Yes | Yes (stubbed) | **No** long-transcript live run recorded | **Implemented but not integration-tested** |
| Summary validation (timestamps, grounding, owners) | Yes | Yes (29) | Partly (repairs reported in a live run) | **Implemented and tested** |
| Summary storage and reuse | Yes | Yes (9 within the service module) | Yes | **Implemented and tested** |
| Vector indexing + persistence + staleness | Yes | Yes (15 + 21) | Yes (index built on a live transcript) | **Implemented and tested (live-verified)** |
| Retrieval with per-video isolation | Yes | Yes (9 + 29) | Yes | **Implemented and tested (live-verified)** |
| Grounded answers with validated citations | Yes | Yes (17) | Yes (question answered 2026-10-25) | **Implemented; live-verified previously** |
| Per-video chat history (session-scoped) | Yes | Yes (13) | Yes | **Implemented and tested; persistence across restarts not implemented** |
| Report building from real data | Yes | Yes (30) | Yes | **Implemented and tested** |
| PDF / DOCX / TXT / MD / JSON / HTML exports | Yes | Yes (11 + 9 + 27 + 11) | Yes (files reopened with `pypdf`, `python-docx`, `json`) | **Implemented and tested (live-verified)** |
| CSV ×3 / XLSX exports | Yes | Yes (13) | Yes (8 worksheets reopened) | **Implemented and tested (live-verified)** |
| PPTX export | Yes | Yes (7) | Yes (reopened with `python-pptx`) | **Implemented and tested (live-verified)** |
| Streaming summaries into the UI | **No** | — | — | **Not implemented** (`stream_generate` unused) |
| Background worker / non-blocking processing | **No** | — | — | **Not implemented** (documented limitation) |
| Chat history persisted across restarts | **No** | — | — | **Not implemented** (`ISS-006`) |
| Live provider calls in this verification pass | — | — | **Not re-run** (no key was read or used while writing this handbook) | **Unverified in this pass** |
