## Chapter 16 — Security, privacy and responsible use

### 16.1 Credential protection

| Mechanism | Where | What it actually does |
| --- | --- | --- |
| `SecretStr` | `config/settings.py` (`nvidia_api_key`) | `repr(settings)` and logs show `**********`, not the key |
| Placeholder detection | `API_KEY_PLACEHOLDERS` + `_clean_api_key` | A template value is treated as "not configured" instead of being sent to the provider |
| One client factory | `nvidia_client.create_nvidia_client` | The key is read in exactly one function and handed straight to the SDK |
| Non-secret status | `nvidia_key_status`, `has_nvidia_credentials`, `describe_client_config` | The UI and logs can report *whether* a key exists, never its value |
| No key in prompts or documents | `SummaryOptions`, `StructuredSummary`, prompts | Asserted by `test_credentials_never_reach_a_prompt_or_a_stored_document` and `test_prompts_never_contain_credentials` |
| No configuration in exports | JSON exporter | Asserted by `test_json_export_never_contains_secrets_or_configuration` |
| Log redaction | `RedactionFilter` / `redact_secrets` | Key-shaped strings and `Authorization:` headers are removed from every log record before handlers see it |
| `.env` excluded | `.gitignore` (`env`, `.env.*` with `!.env.example`) | Prevents committing the real key |
| Cookies excluded and opt-in | `.gitignore` (`cookies*.txt`, `*.cookies`), `YTDLP_COOKIES_FILE` unset by default | Authorized cookies are never uploaded, stored or logged by the app; unknown browser names are refused |

**Recommendation (not a code feature):** if a key was ever shared in chat, a screenshot, a
repository or a log excerpt, revoke it in the NVIDIA console and create a new one. Rotation costs a
minute; a leaked key can cost an account.

### 16.2 Input validation and injection defences (implemented)

| Threat | Defence | Evidence |
| --- | --- | --- |
| Path traversal via filename | `sanitize_filename` (strips directories and unsafe characters), `unique_upload_path`, `is_inside` containment, `cleanup_stored_file` limited to the managed directory | `tests/test_source_service.py` |
| Upload that lies about its size | The limit is enforced **while streaming**, counting real bytes | `test_streaming_enforces_the_limit_even_when_the_size_is_understated` |
| SSRF (server-side request forgery) | Scheme allow-list, no embedded credentials, DNS resolution, and a block-list covering loopback, private, link-local, multicast and reserved ranges — including IPv4-mapped, 6to4 and NAT64 forms; redirects are re-validated; the streamed size and timeout are bounded | 67 tests in `tests/test_url_ingestion.py` |
| Access-control bypass | Private/sign-in-only/403/bot-checked media is reported and refused; optional cookies must be the user's own export | `ISS-001`, `ISS-002`; `test_private_media_is_not_bypassed`, `test_private_platform_video_is_not_bypassed` |
| Shell injection | FFmpeg/ffprobe are called with argument lists and `shell=False` | `test_extraction_uses_a_safe_argument_list` |
| Unsafe identifiers → filesystem | Storage validates identifiers against a safe pattern before building a path | `test_unsafe_identifiers_are_rejected` (transcripts and summaries) |
| HTML injection in reports/UI | `html.escape` on every interpolated value; `_safe_href` allows only absolute `http(s)` links, with `rel="noopener noreferrer"` | `test_export_html.py` (11 tests) |
| CSV/spreadsheet formula injection | `protect_csv_cell` neutralises leading `=`/`+`/`-`/`@`; `xlsx_exporter._set_text` writes literal strings | `test_csv_cells_that_look_like_formulas_are_neutralised`, `test_excel_treats_formula_like_text_as_a_string` |
| Markdown structure injection | Aggressive escaping plus adaptive code fences | `test_markdown_escapes_block_markup_from_user_text`, `test_markdown_fence_grows_when_content_contains_backticks` |
| Prompt injection from transcript text | The transcript is fenced with explicit markers and declared as data; the system prompt forbids following instructions found in it; a user's custom instruction is sanitised and length-limited | `test_injection_text_stays_inside_the_transcript_region`, `test_chunk_and_merge_prompts_keep_the_same_separation`, `test_prompt_marks_passages_as_untrusted_and_keeps_ids_visible` |
| Fabricated citations | Citations are created only from retrieved passages; unknown refs and unverified quotes are dropped and reported | `test_unknown_citation_is_removed_and_reported`, `test_citation_without_a_source_is_never_invented` |
| Silent data loss | Oversized inputs are refused (transcript minutes, chunk cap, report characters, upload size, download size) | Chapter 2, NFR-3 and the referenced tests |

### 16.3 Privacy: what leaves the machine

| Data | Leaves the machine? | Where it goes | When |
| --- | --- | --- | --- |
| Your audio/video file | **No** — transcription is 100% local | — | — |
| The transcript text | **Yes** (when you summarise or ask a question) | NVIDIA `NVIDIA_BASE_URL` | Only on an explicit user action |
| Your question and the retrieved passages | **Yes** | NVIDIA | When you press Ask |
| Embeddings | **No** — computed locally from a local model | — | — |
| Vectors and metadata | **No** — stored in local `chroma_data/` | — | — |
| Downloaded media | Fetched from the source host, stored in a per-job directory, deleted in `finally` | The source host | During URL ingestion only |

Practical consequences:

- Because transcript text is sent to a third party for summarising/answering, **do not process
  confidential recordings** unless you are allowed to send their text to that provider. Uploading
  and transcribing a recording alone sends nothing anywhere.
- Transcripts, summaries, exported reports (which may contain a full transcript) and logs stay on
  your disk. `.gitignore` excludes `data/`, `logs/`, `reports/*`, `chroma_data/` so they are not
  committed.

### 16.4 Authorized use of media

The application implements the policy "never bypass access controls":

- A bot check, HTTP 403, "private"/"sign-in required"/"members-only" response is translated into an
  explanation that names the two supported *authorized* options (`YTDLP_COOKIES_FILE`,
  `YTDLP_COOKIES_FROM_BROWSER`) or suggests uploading a copy you are entitled to use.
- Cookies come from **your own** signed-in browser session; the file is validated before use, passed
  only to yt-dlp, and never logged, stored or uploaded.
- A public-domain video was used for live verification (documented in `DEVELOPMENT_ISSUES.md`).

Responsibility rests with the user: respect the source's terms of service and copyright.

### 16.5 Safeguards that are *not* implemented (honest list of gaps)

| Gap | Risk | Note |
| --- | --- | --- |
| No authentication / multi-user accounts | Anyone who can reach the port can use the app and see stored transcripts | It is a local single-user application; bind to localhost only |
| No encryption at rest | Transcripts, summaries and ChromaDB files are plain files | Use OS disk encryption if the machine is shared |
| Upload type checked by extension, then content | A non-media file with an allowed extension passes validation | `ffprobe` then refuses it at the inspection stage — a second line of defence, not a full one |
| No malware scanning of uploads | A malicious file is stored and probed | Do not process untrusted files on a shared machine |
| Transcript retention off by default | Data stays until deleted | Set `TRANSCRIPT_RETENTION_DAYS` or delete from the UI |
| `cookies.txt` file permissions are the user's responsibility | A readable cookie file exposes the user's session elsewhere | Store it outside version control (`.gitignore` covers common names) |
| No rate limiting on provider calls | A loop of questions could be costly | Budgets exist per call; no global limiter |
| No audit trail of who exported what | No accountability record | Out of scope for a single-user tool |
| Not a git repository in this workspace | `git` cannot enforce the ignore rules here | `.gitignore` is prepared; initialise a repository and commit carefully |

---

## Chapter 17 — Limitations and future improvements

### 17.1 Confirmed current limitations

| # | Limitation | Evidence | Impact |
| --- | --- | --- | --- |
| 1 | A long transcript can exhaust the model's output budget on private reasoning, producing no answer (`ISS-004`) | Log excerpt in `docs/DEVELOPMENT_ISSUES.md` (2026-10-25 10:51:41, `finish_reason=length`, 147 s, `type=truncated`) | Reported honestly; the user must raise `SUMMARY_MAX_TOKENS` or use a non-reasoning model |
| 2 | Whisper `base` mis-hears words (`ISS-005`: "trunks" → "punks") | Recorded in the issue log | Transcription accuracy is model-bound; use a larger model for accuracy |
| 3 | Chat history is session-scoped (`ISS-006`) | `chat_page` stores history in `st.session_state` | Closing the app forgets conversations |
| 4 | Anonymous YouTube access is often refused (`ISS-001`, `ISS-002`) | Live log excerpts | Those videos need authorized cookies or an uploaded copy |
| 5 | Processing is synchronous | No async/threads in the pipeline | The browser waits during download/transcription; a long recording blocks that session |
| 6 | Whisper runs on CPU by default | `torch` CPU wheel pinned | Slow for long recordings |
| 7 | First embedding/index build downloads models | `sentence-transformers` (~90 MB) | The first index build is slower and needs network |
| 8 | Timestamps can be absent | Whisper without segment times, or plain-text transcripts | Downstream steps honestly report "timestamp unavailable" instead of inventing one |
| 9 | Chunk-and-merge summarisation has no recorded live run | Only stubbed tests | Treat long-transcript summarisation quality as **not live-verified** |
| 10 | Streaming output is not wired into the UI | `stream_generate` exists but is unused | No token-by-token display |
| 11 | Not a git repository in this workspace | `git status` fails | No history/rollback safety net |
| 12 | Unicode PDF quality depends on an installed font | `resolve_font` fallback + warning | Without a suitable font, non-Latin text falls back to Latin |

### 17.2 Potential risks requiring further verification

| Risk | Why it needs verification |
| --- | --- |
| Long-transcript summaries (`SUMMARY_MAX_CHUNKS=40`) on a real 2-hour recording | Never run live; merge quality and partial-coverage reporting are unproven at scale |
| GPU transcription path (`WHISPER_DEVICE=cuda`) | CUDA torch is not installed here, so the CUDA branch is untested locally |
| Prompt-injection defence in the wild | Markers and instructions are a mitigation, not a proof; a determined transcript could still skew an answer |
| Retrieval quality on real lectures | Chunk size 1200/overlap 200 were chosen sensibly but no retrieval-quality evaluation (recall@k) was measured |
| ChromaDB index growth | `RAG_MAX_CHUNKS_PER_VIDEO=2000` caps a video, but total disk use across many videos is untracked |
| Concurrent use of one workspace | File writes are atomic, but two app instances sharing `data/` are untested |
| Cost of many questions | No global limiter or spend guard |

### 17.3 Optional future improvements

**Reliability**

1. Move processing to a background worker (queue + status polling) so the UI never blocks.
2. Persist chat history (SQLite or JSON per video) so conversations survive restarts.
3. Add an automatic retry/fallback ladder for truncated summaries (raise the budget once, then fall back to a non-reasoning model).
4. Cache transcription results by media hash so re-processing the same file is instant.
5. Add an index garbage-collection job that removes orphaned Chroma collections (the registry already stores what it needs).

**Speed and cost**

6. Offer a GPU path (`torch` CUDA wheel) and expose a "speed vs accuracy" Whisper preset.
7. Batch-embed larger groups and stream-write vectors for very long transcripts.
8. Summarise with a two-tier strategy (cheap model for chunks, strong model for the final merge).

**Retrieval quality**

9. Add a retrieval evaluation harness (golden questions per video, recall@k / MRR) so chunk size and top-k can be tuned with evidence.
10. Add a lexical hybrid search (BM25) alongside embeddings to catch exact names, numbers and rare terms.
11. Re-rank retrieved passages with a cross-encoder before generating.

**User experience**

12. Stream answers into the UI as they are written (the service already supports streaming).
13. Show a per-video timeline of topics (chapters + timestamps) with jump-to-time links.
14. Add a "compare two videos" mode built on the existing per-video indexes.
15. Provide a searchable transcript view with in-page highlighting.

**Observability**

16. Record per-run metrics (durations, token use, model calls, outcomes) into a local metrics file so cost and latency are visible.
17. Add structured JSON logging with a correlation id per job.

**Security**

18. Optional local passphrase protection for stored transcripts.
19. Content-type sniffing in addition to extension checks on uploads.
20. A secrets scan (e.g. a pre-commit hook) once the project is a git repository.

**Maintainability**

21. Add `ruff`/`black` and a CI workflow that runs the 651 tests on every push.
22. Add type checking (`mypy`) — the code is already fully annotated.
23. Turn the `docs/_guide_parts` sources into a generated PDF in CI so documentation cannot drift.

### 17.4 What this project is *not*

It is not a production multi-user service, not a hosted product, and not a transcription service
(no queueing, no tenancy, no SLA). It is a complete, well-tested local AI application — and it is
honest about every one of its boundaries above.

---

## Chapter 18 — Project viva and interview preparation

### 18.1 The three prepared explanations

**30-second introduction**

> "VideoMind AI is a Streamlit application that turns long videos or audio into knowledge. It takes
> an uploaded file or a public URL, extracts audio with FFmpeg, transcribes it locally with Whisper
> into timestamped segments, summarises it with NVIDIA's Nemotron model, indexes it into ChromaDB
> with local embeddings so a RAG chatbot can answer questions with timestamp citations, and finally
> exports the whole thing as a report in eleven formats. It is modular, fully typed and covered by
> 651 automated tests that need no network."

**2-minute explanation**

> "The project follows a five-step roadmap. Step 1 is the foundation: a Streamlit shell, a single
> validated configuration module, redacting logging, and a typed model layer. Step 2 is ingestion:
> uploads are validated and streamed to disk with the size limit enforced while writing; URLs go
> through SSRF checks before anything is fetched, then either direct streaming or yt-dlp downloads the
> media into a per-job scratch directory. FFmpeg and ffprobe inspect the media and extract 16 kHz mono
> audio, Whisper transcribes it locally into timestamped segments, and the transcript is stored
> atomically as JSON. Step 3 is summarisation: the transcript is rendered with its timestamps, split
> into overlapping chunks when it does not fit one request, each chunk is summarised, the chunk
> summaries are merged, and the model's JSON is validated so timestamps must fall inside real
> segments and owners or deadlines that the source never states are removed. Step 4 is retrieval:
> the transcript is normalised and chunked with timestamps preserved, embedded locally with
> all-MiniLM-L6-v2, and stored in one ChromaDB collection per video with a JSON registry that detects
> staleness and embedding-model changes. A question is embedded, the four closest passages in that
> video's collection are retrieved, labelled S1..S4 and sent to Nemotron with a strict instruction to
> answer only from them, and every citation the model returns is validated against the passages that
> were actually supplied. Step 5 builds one canonical report from the stored transcript, summary and
> chat, and renders it to PDF, DOCX, TXT, Markdown, JSON, HTML, three CSV variants, XLSX and PPTX.
> Throughout, failures return a named outcome — missing credentials, download failed, stale index,
> truncated budget — instead of a fake success."

**Detailed technical explanation (demonstration script)**

1. Show `app.py` and explain the bootstrap/dispatch split, then the five pages in the sidebar.
2. Open `config/settings.py` and show one field, one validator and `get_settings`' fallback behaviour.
3. Home page: upload a short clip (`scripts/verify_step2_media.py` builds one) and narrate the six
   stages as they light up; open the stored JSON transcript to show segments and timestamps.
4. Summarization page: show the mode list, the request/token estimate, the pre-generation state, then
   the validated structured summary with coverage and quality notes.
5. Ask Your Video: show `NOT_INDEXED` → Build index → `READY` with chunk counts; ask a question;
   point at the citations and their timestamps, and at the "retrieved sources" list.
6. Reports page: pick sections, generate a PDF, then the same content as XLSX and PPTX without
   regenerating the report.
7. Settings page: run the diagnostics and the provider connection test; show that the key value is
   never displayed.
8. Finish with `pytest` (651 passed) and the honest list of limitations (`ISS-004`, `ISS-006`, the
   YouTube restrictions).

### 18.2 Questions and answers — grouped by difficulty

#### Level 1 — Project overview

**Q1. What is the single entry point of the application?**
`app.py`. Streamlit executes it; `main()` sets the page config, calls `bootstrap()`, initialises
session state, injects the stylesheet, renders the sidebar and dispatches to one of five pages.

**Q2. Why is one process enough for both UI and backend?**
Because Streamlit runs Python server-side: the UI *is* Python. The pages import service classes and
call them directly, so there is no HTTP API layer to maintain.

**Q3. Name the five pages and their keys.**
`home` (Home), `summarization` (Summarization), `chat` (Ask Your Video), `reports` (Reports),
`settings` (Settings); the default is `home`. They are defined in `NAV_PAGES` in `src/models/schemas.py`.

**Q4. What are the five development steps?**
1 Foundation; 2 video/audio ingestion + transcription; 3 Nemotron summarisation; 4 RAG chatbot;
5 reports and exports.

**Q5. How many tests exist and do they need a key?**
651 tests in 32 modules; they pass in ~49 s and need no API key and no network — every external
service is stubbed.

**Q6. What does the app do when there is no API key?**
It starts. The shell, upload/URL ingestion, transcription, storage, indexing, retrieval and all
exports still work. Summaries and answers return `missing_credentials` with an explanation.

**Q7. Which parts run locally and which need the internet?**
Local: FFmpeg, Whisper, embeddings, ChromaDB, storage, all exports. Internet: the NVIDIA API
(summarise/answer) and URL ingestion (yt-dlp/direct download).

#### Level 2 — Python and architecture

**Q8. Why pydantic models instead of dictionaries?**
Validation at the boundary. `TranscriptSegment` rejects an end time before its start, so impossible
data cannot travel to the model or a report; and every stage's contract is explicit.

**Q9. How is configuration centralised?**
One `pydantic-settings` class, `Settings`, in `config/settings.py`; field names map to upper-case
environment variables. No other module reads `os.environ`.

**Q10. What happens if an environment variable is invalid?**
`get_settings()` catches the `ValidationError`, records a short description via
`describe_validation_error`, and returns defaults built with `model_construct()`. The Settings page
shows the problem. The app never crashes on configuration.

**Q11. How do you know a module graph has no cycles?**
`test_modules_import_without_circular_imports` imports every module in a parametrised test.

**Q12. Why are there dependencies injected into service constructors (e.g. `transcriber=`, `store=`)?**
So tests can replace the external boundary (Whisper, ChromaDB, the provider client) without touching
the real implementation — the production default is still constructed when nothing is injected.

**Q13. What is the purpose of `src/models/__init__.py` and `src/services/__init__.py`?**
They are facades: they re-export public names so imports read cleanly (`from src.models import
TranscriptRecord`) while the implementation stays split across focused modules.

**Q14. Why is `lru_cache` used in several places?**
Streamlit re-runs the script on every interaction. Caching `get_settings`, `get_nvidia_client`,
`load_sentence_transformer`, the Chroma client, `detect_media_tooling`, `cuda_available_cached` and
Whisper models avoids re-doing expensive work on each rerun. Tests clear these caches with the
provided `reset_*` functions.

**Q15. How is state kept between reruns?**
`st.session_state`, plus pure helper functions that take a state-like mapping
(`get_active_page(state)`, `set_active_page(state, key)`, `get_history(state, video_id)`) so the
routing and history logic is testable without Streamlit.

#### Level 3 — Streamlit

**Q16. How does navigation work?**
`render_sidebar` renders a `radio` over page keys and returns the active key;
`NAV_REQUEST_KEY` lets a button request navigation before the widget is created, and
`layout.render_page` dispatches through `_PAGE_RENDERERS`.

**Q17. Why does the app show stage labels instead of a progress bar?**
Because a percentage would be invented. `PipelineStageReport` records the true state of each of the
six stages, and the UI renders exactly that list.

**Q18. How do you prevent a page from doing expensive work on load?**
By design and by test: indexes and summaries are only produced on an explicit button click
(`test_index_selection_never_generates_anything_on_load`,
`test_loading_the_page_never_generates_a_summary`).

**Q19. What happens if a page raises an exception?**
`app.main` wraps `layout.render_page` in `try/except`, logs with `logger.exception`, and calls
`render_unexpected_error()` to show a safe message. The process stays alive.

**Q20. How are HTML widgets kept safe?**
`src/ui/components.py` escapes all interpolated values with `html.escape`, and the transcript viewer
uses its own `_escape` helper.

#### Level 4 — Audio ingestion, FFmpeg and Whisper

**Q21. What is the difference between downloading, extracting, transcribing and storing?**
Downloading fetches media from a URL; extracting converts the container to 16 kHz mono WAV with
FFmpeg; transcribing converts audio into text with timestamps using Whisper; storing writes a
validated JSON transcript that later steps reload. Each can fail independently and reports its own
outcome.

**Q22. Why 16 kHz mono?**
It is the sample rate Whisper expects for speech; mono halves the data and removes channel
duplication, so transcription is faster and no less accurate.

**Q23. How does the app know FFmpeg is missing?**
`detect_media_tooling` uses `shutil.which` for `ffmpeg` and `ffprobe`; `require_media_tooling` raises
`MediaToolingError` with `FFMPEG_SETUP_MESSAGE`. The pipeline checks this at stage 1, and the Settings
page reports it in `check_media_dependencies`.

**Q24. How are external commands executed safely?**
`_run_command` passes a list to `subprocess.run` with no shell, captures output, enforces a timeout
and raises a specific error on timeout. User input can never become a shell command.

**Q25. What happens to downloaded media afterwards?**
It lives in `data/temp/job-<hex>/`, which the pipeline creates and deletes in a `finally` block — on
success and on failure. Uploaded copies are deleted by the UI after a successful run, because the
application keeps transcripts, not media.

**Q26. How are timestamps validated after transcription?**
`_validate_segments` sorts segments, drops impossible/negative values to `None`, skips blank
segments, and raises `EmptyTranscriptError` when no speech was detected, so an empty transcript is
never stored as if it were a result.

**Q27. What does the JobRegistry protect against?**
The same source being processed twice in one session, and a lock left behind by an interrupted run
(`ISS-009`): `reset_stale`, `reset_running` and the Home page's reset button recover from it.

**Q28. How is a transcript identified across steps?**
`TranscriptRecord.transcript_id` (`tr-…`) for the document, and `video_id_for(source_reference)` — a
stable `sha1`-derived id — for everything video-scoped: indexes, chat history and reports.

#### Level 5 — NVIDIA Nemotron and LLM APIs

**Q29. How is the provider configured?**
`NVIDIA_API_KEY`, `NVIDIA_MODEL` (`nvidia/nemotron-3.5-lightning-30b-a3b`) and `NVIDIA_BASE_URL`
(`https://integrate.api.nvidia.com/v1`), plus timeout and retry settings. The `openai` SDK is pointed
at that base URL because the endpoint is OpenAI-compatible.

**Q30. Where is the key read, and how is it kept out of logs and UI?**
Only `create_nvidia_client` reads it, from a `SecretStr`. The UI uses `nvidia_key_status` (words, not
values), and `RedactionFilter` removes key-shaped text from every log record.

**Q31. What is an LLM, a prompt, a token and a context window?**
An LLM predicts the next token; a prompt is the input text (system + user messages); a token is the
model's unit of text (roughly ¾ of a word); the context window is the maximum tokens the model can
consider at once. The endpoint does not publish this model's window, so the project keeps a
conservative, configurable input budget.

**Q32. What parameter quirks does `LLMService` handle?**
`max_tokens` rejected → retried once with `max_completion_tokens` (and remembered); `temperature`
rejected → dropped for subsequent calls; reasoning content is never returned as the answer; an empty
answer with `finish_reason=length` is reported as a token-limit problem, not a network failure.

**Q33. Why does the default summary budget sit at 16 384 tokens?**
Because it was measured: with 3072/8192 the reasoning model spent the whole budget on private
reasoning and returned no answer; 16384 produced a validated summary on the live transcript. The
comment in `config/settings.py` and `ISS-004` record this.

**Q34. Which errors are retried?**
Rate limits and transient provider failures within `NVIDIA_MAX_RETRIES` /
`SUMMARY_MAX_RETRIES` / `RAG_MAX_RETRIES`. Authentication, configuration and truncation are not
retried blindly — the first two cannot succeed by retrying, and the third needs a bigger budget.

**Q35. How does the project avoid a second competing provider configuration?**
There is exactly one client factory and one `LLMService`; Steps 3 and 4 consume it
(asserted by `test_llm_service_is_still_the_single_nvidia_client`).

#### Level 6 — Prompt engineering and structured output

**Q36. How is the transcript passed to the model?**
Rendered one line per segment with its source position (when known), wrapped between explicit
`TRANSCRIPT_OPEN`/`TRANSCRIPT_CLOSE` markers, and described as data that must not be treated as
instructions.

**Q37. How is structured output obtained?**
The prompt includes `JSON_CONTRACT` (the field names of `StructuredSummary`), the response is parsed
with `extract_json_object` (which ignores braces inside strings), and if the JSON is malformed the
model is asked once more with `build_repair_prompt`.

**Q38. How do you prevent the transcript from changing the app's behaviour?**
The transcript is fenced as data, the user instruction is sanitised and length-limited, and
configuration is never taken from model output —
`test_transcript_text_cannot_change_application_configuration` asserts it.

**Q39. What is removed from model output, and why?**
Timestamps outside real segments (snapped or dropped), owners/deadlines the source never states,
excerpts that do not appear in the transcript, duplicate takeaways, impossible intervals, placeholder
owner strings. All removals are listed in the summary's notes, so nothing disappears silently.

**Q40. How do the eight summary modes differ technically?**
Each mode contributes its own instruction block to the prompt (`SUMMARY_MODE_DESCRIPTIONS` and the
mode requirements in `summary_prompts`), while the JSON contract stays identical — so the pipeline
and validation are shared and only the requested content changes. `custom` appends the user's
sanitised instruction.

#### Level 7 — Embeddings, vector databases, LangChain and ChromaDB

**Q41. What is an embedding?**
A vector of numbers representing a text's meaning. Here `all-MiniLM-L6-v2` produces 384 numbers per
passage; passages about the same idea end up close together, which lets retrieval match meaning
rather than exact words.

**Q42. Why is ChromaDB used, and how is it persisted?**
It is a local vector database. The project uses its persistent client pointed at `chroma_data/`, so
vectors survive restarts; the SQLite file plus collection directories are the storage.

**Q43. What does LangChain contribute?**
Only three things: `ChatPromptTemplate` for the answer prompt, LCEL runnables to compose
prompt → model → parser, and the `Embeddings` interface that Chroma consumes. The meta-package and
`langchain-community` are deliberately not dependencies.

**Q44. How is one video isolated from another?**
The collection name is derived from the video id and the transcript fingerprint
(`collection_name_for`), the registry maps `video_id → collection`, and `search` requires a known
`video_id` — a second video's vectors are simply not in that collection.

**Q45. How does the app detect that an index is out of date?**
`compute_fingerprint(video_id, segments, full_text)` is recomputed and compared with the value stored
in the registry: a mismatch is `IndexStatus.STALE`, and a different embedding model is
`EMBEDDING_MISMATCH`. Both block answering until you rebuild.

**Q46. Why are chunk timestamps preserved?**
So a citation can point at a real position in the recording. Each chunk stores `start_seconds`,
`end_seconds` and the segment range, and `time_label` formats them; unknown timestamps are omitted
from Chroma metadata rather than stored as `0`.

**Q47. Why is chunk size measured in characters, not tokens?**
No tokenizer is loaded at chunk time, so characters are the cheap, deterministic unit; 1200
characters is roughly 300 words — small enough to be precise, large enough to carry meaning.

**Q48. What is `RAG_MAX_CHUNKS_PER_VIDEO` for?**
It refuses a recording that would need more than 2000 chunks instead of silently truncating it, and
tells the user which setting to raise.

#### Level 8 — RAG retrieval, citations and honesty

**Q49. Explain the RAG flow in one sentence.**
Retrieve the passages of *your* transcript that match the question, hand only those to the model as
the evidence, and validate every citation the model returns against what was actually supplied.

**Q50. What happens when nothing relevant is retrieved?**
`RagOutcome.NO_RESULTS` with a message that the recording does not appear to cover the question —
the model is not asked, so no unsupported answer can be invented.

**Q51. How are citations produced?**
Retrieved passages are labelled `S1..Sn` **after** selection, the prompt requires the model to cite
those labels, and `_validate` maps each label back to the real passage. Unknown labels are removed
and reported; quotations that do not appear verbatim in the passage are dropped.

**Q52. Where do citation timestamps come from?**
From the stored chunk metadata, never from the model's text — `RagCitation.from_source` copies them
from the retrieved `RetrievedSource`.

**Q53. How is a follow-up question handled?**
`needs_conversation_context` detects short/referential questions; `contextual_query` widens the
*search* with the previous question, and up to six recent turns are rendered as history (old answers
trimmed to 400 characters) so they inform but do not dominate.

**Q54. Is the chatbot hallucination-proof?**
No, and the guide says so. It is *constrained*: answers must be grounded in retrieved passages,
citations are validated, missing evidence is reported, and summary-derived passages are labelled as
paraphrases — but an LLM can still produce an unsupported sentence that happens to cite a real
passage.

**Q55. Name three RAG-specific failure modes and the response in code.**
(1) Wrong passages retrieved → the retrieved-sources list makes it visible, plus top-k/overlap tuning.
(2) Missing evidence → `NO_RESULTS` instead of a guess. (3) Fabricated citation → removed and
reported. Others: stale index → refused with a rebuild prompt; prompt injection → fenced untrusted
data.

#### Level 9 — Report generation and exports

**Q56. What is the canonical report model and why does it exist?**
`VideoMindReport` in `src/reports/report_models.py`. Every exporter renders only this object, so all
formats agree and exporters cannot reach into the database or the model.

**Q57. How are the eleven formats produced, and does exporting cost anything?**
`EXPORT_FORMATS` maps each key to a builder; all of them work in memory from stored data, so
exporting makes no model calls and needs no key (asserted by tests).

**Q58. How are PDF page numbers produced?**
ReportLab Platypus with a page-number canvas: `_PageDecorator` draws the header and the
"Page N of M" footer, and `build_pdf` returns a warning if only a Latin font is available.

**Q59. How is untrusted text made safe in each format?**
HTML: `html.escape` plus an `http(s)`-only link allow-list. CSV: `protect_csv_cell` neutralises
formula prefixes. XLSX: values written as literal strings. Markdown: escaping plus adaptive fences.
PDF: XML escaping for canvas text.

**Q60. What happens if a selected section has no data?**
It is omitted **with a reason** recorded in the report and shown in the output; nothing is
fabricated and no placeholder text is printed.

**Q61. How does the page avoid showing stale bytes?**
`export_cache_key` fingerprints transcript id, video id, summary id, sections, title, author,
organisation, notes, the timestamps flag and the format; any change invalidates the generated file.

**Q62. What happens with a huge transcript?**
`REPORT_MAX_CHARACTERS` (5 000 000) refuses the report with a message instead of truncating it.

#### Level 10 — Security, testing and future scope

**Q63. How is SSRF prevented?**
The URL is validated for scheme/host/credentials, resolved, and the resolved addresses are checked
against a block-list (loopback, private, link-local, multicast, reserved), including IPv4-mapped,
6to4 and NAT64 wrappers; redirects are re-validated, and size/timeout are bounded.

**Q64. How is secret leakage prevented?**
`SecretStr`, placeholder detection, a single read site, words-not-values status strings,
`RedactionFilter` on logs, tests that assert credentials never appear in prompts, stored summaries or
JSON exports, and `.env`/`cookies*.txt` in `.gitignore`.

**Q65. What are the security gaps?**
No authentication, no encryption at rest, extension-based upload typing (with ffprobe as a second
check), no malware scanning, no global rate limit, and no git repository to enforce ignores. All are
listed in Chapter 16.5.

**Q66. How do tests avoid calling the network?**
By injecting stubs at the seam: `FakeClient`/`StubLLM` for the provider,
`FakeEmbeddings`/`FakeRagService`/`StubRagService` for retrieval, `FakeYoutubeDL`/`FakeResponse` for
downloads, `FakeModel` for Whisper, and `StubPipeline`/`StubClient` for the UI. `conftest` also
removes provider configuration and points the `.env` loader at a missing file.

**Q67. How would you add a new export format in five steps?**
Write a `render_*`/`build_*` function returning `RenderResult`; add it to `EXPORT_FORMATS` with key,
label, extension, MIME and filename suffix; add a requirement entry to `FORMAT_REQUIREMENTS` if it
needs a section; add tests for structure, Unicode, missing data and safety; done — the UI picks it up
because it renders the registry.

**Q68. How would you make processing non-blocking?**
Move `VideoPipeline.run` behind a worker (thread or process) with a job id and a status store, and
have the page poll that status. The pipeline already reports stages through a callback and already
has a `JobRegistry`, so the seam exists.

**Q69. What is the biggest remaining risk in the project?**
Two candidates: the long-transcript summarisation path (chunk-and-merge) has no recorded live run, and
the synchronous architecture means a long recording occupies the session. Both are documented rather
than hidden.

**Q70. What did the project deliberately *not* do, and why?**
It does not bypass access controls, does not fabricate summaries or citations, does not silently
truncate oversized input, does not stream mock data into the UI, and does not claim live verification
for anything only covered by stubs.

### 18.3 Likely follow-up questions from an examiner

1. "Show me where the API key is read." → `nvidia_client.create_nvidia_client`, then
   `Settings.get_nvidia_api_key`.
2. "What happens if I stop the app while it is transcribing?" → The job directory is removed when the
   run ends; a stale lock is recoverable via `JobRegistry.reset_running` / the Home page reset.
3. "How do I know the summary is not made up?" → Timestamps are validated against segments,
   owners/deadlines must appear in the source, excerpts are checked, duplicates are removed, and every
   removal is listed in the notes; partial coverage is labelled.
4. "Why not use the vector store's own chunks instead of your chunker?" → Timestamps and provenance
   are the priority: the chunker keeps segment boundaries, real time ranges and deterministic ids.
5. "What is the cost per summary?" → `model_calls`, `latency_ms` and token usage are recorded; the
   chunked path makes one call per chunk plus merge calls, capped by `SUMMARY_MAX_CHUNKS`.
6. "How would this scale to 1000 videos?" → Index per video already scales (one collection each,
   registry-driven cleanup); the real constraints are CPU transcription and disk space, not the
   retrieval design.
7. "Where would you add authentication?" → At the Streamlit boundary (before `render_page`), plus
   per-user data directories; nothing in the services assumes a single user beyond the shared `data/`
   root.
8. "Why is there both a `transcript_id` and a `video_id`?" → A transcript document is one stored run;
   a video is the stable source identity that indexes, chat history and reports must agree on, even
   if you transcribe the same file twice.

---

## Chapter 19 — Glossary

| Term | Simple definition | Example in this project |
| --- | --- | --- |
| **API** | A defined way for programs to talk to each other | The OpenAI-compatible chat-completions API |
| **Endpoint** | The specific URL where an API answers | `https://integrate.api.nvidia.com/v1` |
| **Environment variable** | A named value provided by the operating system, outside the code | `NVIDIA_API_KEY`, `SUMMARY_MAX_TOKENS` |
| **Dependency** | A package or tool the project needs | `streamlit`, `reportlab`, FFmpeg |
| **Direct vs transitive dependency** | Direct = you declared it; transitive = pulled in by another package | `streamlit` vs `pyarrow` |
| **Exception** | A Python error object raised when something fails | `DownloadFailedError` |
| **Traceback** | The report of where an exception happened, frame by frame | `logs/app.log` during ISS-003 |
| **Transcription** | Converting speech audio into text | Whisper converting a WAV to segments |
| **Timestamp** | A position in the recording, in seconds or `MM:SS` | `TranscriptSegment.start_seconds` |
| **Segment** | A short transcribed chunk with its own time range | One Whisper output line |
| **Token** | The model's unit of text (roughly ¾ of a word) | `estimate_tokens` |
| **Prompt** | The text sent to a model: system rules + user task | `summary_prompts.build_summary_messages` |
| **System prompt** | The high-level rules/persona the model must follow | `SYSTEM_PROMPT` |
| **LLM** | Large Language Model, a text-generating neural network | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| **Inference** | Running a trained model to get an output | Calling the model to summarise |
| **Reasoning trace** | Private "thinking" text some models emit before the answer | Hidden by `_extract_text` |
| **Embedding** | A vector of numbers representing meaning | 384 numbers from `all-MiniLM-L6-v2` |
| **Vector database** | A store optimised for finding near vectors | ChromaDB under `chroma_data/` |
| **Collection** | A named group of vectors inside a vector database | One per video |
| **Semantic search** | Searching by meaning instead of exact words | `ChromaVectorStore.search` |
| **Cosine distance / similarity** | A measure of how close two vectors point | `distance` and `similarity` fields |
| **Chunk** | A passage created by splitting a long document | `RagChunk` |
| **Chunking** | The act of splitting for retrieval | `TranscriptChunker.chunk_transcript` |
| **Overlap** | Repeating a chunk's tail at the start of the next | `RETRIEVAL_CHUNK_OVERLAP = 200` |
| **Top-k** | How many results to fetch | `RETRIEVAL_TOP_K = 4` |
| **Retrieval** | Finding the most relevant passages | `Retriever.retrieve` |
| **Context window** | Maximum tokens a model can consider at once | Not published; a conservative input budget is used |
| **Context budget** | A self-imposed cap on how much retrieved text is sent | `RETRIEVAL_MAX_CONTEXT_CHARS = 6000` |
| **RAG** | Retrieval-Augmented Generation: retrieve evidence, then generate from it | Step 4 as a whole |
| **Grounding** | Requiring the answer to be supported by supplied evidence | "Answer only from the passages" |
| **Citation** | A reference from an answer back to a real passage | `RagCitation` with `time_label` |
| **Hallucination** | Fluent text not supported by the source | Mitigated, not eliminated |
| **Prompt injection** | Untrusted text trying to change the model's instructions | Fenced transcript as data |
| **Fingerprint** | A hash that changes when content changes | `compute_fingerprint`, `options_fingerprint` |
| **Stale (index)** | Built from content that has since changed | `IndexStatus.STALE` |
| **Persistence** | Saving state so it survives a restart | JSON transcripts, ChromaDB, registry |
| **Serialization** | Turning objects into storable/transferable form | `report_to_dict`, `model_dump_json` |
| **Atomic write** | Write to a temp file then rename, so a crash cannot half-write | `transcript_storage.save` |
| **Cache** | A stored result reused to avoid repeated work | `lru_cache` on settings/client/model |
| **Mock / stub** | A stand-in object used in tests instead of a real service | `FakeClient`, `StubLLM`, `FakeEmbeddings` |
| **Unit test** | A test of one piece of logic in isolation | `tests/test_summary_validation.py` |
| **Integration test** | A test of several components working together | `tests/test_rag_service.py` |
| **Live / end-to-end verification** | Running against the real service or real media | YouTube → transcript run on 2026-10-25 |
| **SSRF** | Server-Side Request Forgery: tricking a server into fetching an internal URL | Blocked by `assert_public_destination` |
| **Path traversal** | Using `../` in a filename to escape a directory | Neutralised by `sanitize_filename` + `is_inside` |
| **CSV formula injection** | A spreadsheet executing a value starting with `=` | Neutralised by `protect_csv_cell` |
| **Secret redaction** | Removing credentials from logs before they are written | `RedactionFilter` |
| **Outcome enum** | A named reason a run ended the way it did | `PipelineOutcome`, `SummaryOutcome`, `RagOutcome` |
| **Platypus** | ReportLab's high-level paginating layout engine | `pdf_exporter.build_pdf` |
| **LCEL** | LangChain Expression Language: composing steps with `|` | `_build_chain` |
| **Deterministic id** | An id derived from content, so rebuilds are stable | `RagChunk.chunk_id` |

---

## Chapter 20 — Final project reference

### 20.1 One-page project summary

**VideoMind AI** is a local, single-process Streamlit application that converts long videos and audio
into readable, searchable, exportable knowledge. It ingests an uploaded file or a validated public
URL, extracts 16 kHz mono audio with FFmpeg, transcribes it locally with Whisper into timestamped
segments, summarises it with NVIDIA's Nemotron model through an OpenAI-compatible API (with
chunk-and-merge for long recordings and strict validation of timestamps, owners and quotes), indexes
it into per-video ChromaDB collections using local `all-MiniLM-L6-v2` embeddings so a
retrieval-augmented chatbot can answer questions with validated timestamp citations, and assembles one
canonical report from the stored transcript, summary and chat that exports to eleven formats
(PDF, DOCX, TXT, Markdown, JSON, HTML, 3 × CSV, XLSX, PPTX). It is layered
(presentation → orchestration → services → models/config/utils → persistence), fully typed with
pydantic, honest by design (named outcome enums, no fabricated content, no silent truncation) and
covered by **651 automated tests that require no network or API key**.

### 20.2 Complete source-tree reference (study order)

| Priority | Path | Why start here |
| --- | --- | --- |
| 1 | `app.py` | 83 lines: the whole application's entry, bootstrap and dispatch |
| 2 | `config/settings.py` | Every configuration knob, validator and path in one file |
| 3 | `src/models/schemas.py` (+ `summary_schemas.py`, `rag_schemas.py`) | The vocabulary every stage speaks |
| 4 | `src/services/video_pipeline.py` | The clearest example of orchestration + honest stage reporting |
| 5 | `src/services/transcription_service.py` | A complete, well-guarded external-tool integration |
| 6 | `src/services/summarization_service.py` | The single-pass vs chunk-and-merge decision tree |
| 7 | `src/rag/service.py` → `retriever.py` → `answer_generator.py` | The RAG pipeline, in the order it executes |
| 8 | `src/rag/chunking.py` + `vector_store.py` | The timestamp-aware chunker and ChromaDB persistence |
| 9 | `src/reports/report_builder.py` + `export_formats.py` | One report model, eleven renderers |
| 10 | `src/ui/layout.py` → each page | How the UI calls services without doing their work |

### 20.3 The five files to study first

1. [`app.py`](app.py) — the entry point and page shell.
2. [`config/settings.py`](config/settings.py) — configuration, validation and safe fallbacks.
3. [`src/models/schemas.py`](src/models/schemas.py) — the shared data models.
4. [`src/services/video_pipeline.py`](src/services/video_pipeline.py) — orchestration, stages, cleanup, locks.
5. [`src/rag/service.py`](src/rag/service.py) — indexing and grounded answering, the project's heart.

### 20.4 Key functions and their locations (quick table)

| Need to find… | Look at |
| --- | --- |
| How the app starts | `app.py:23` `bootstrap`, `app.py:50` `main` |
| Where a setting comes from | `config/settings.py:120` `Settings` |
| How a provider call is made | `src/services/llm_service.py:108` `LLMService.generate` |
| How the six stages run | `src/services/video_pipeline.py:240` `VideoPipeline.run` |
| How a URL is made safe | `src/services/url_ingestion_service.py:297` `assert_public_destination` |
| How a download failure is explained | `src/services/url_ingestion_service.py:776` `_translate_ytdlp_error` |
| How audio is extracted | `src/services/media_service.py:246` `extract_audio` |
| How speech becomes text | `src/services/transcription_service.py:232` `TranscriptionService.transcribe` |
| How a transcript is saved | `src/services/transcript_storage.py:72` `save` |
| How a summary run is decided | `src/services/summarization_service.py:119` `summarize` |
| How model JSON is validated | `src/services/summary_validation.py:294` `build_structured_summary` |
| How timestamps are checked | `src/services/timestamp_service.py:70` `TimestampValidator.sanitize` |
| How a transcript is chunked for retrieval | `src/rag/chunking.py:265` `chunk_transcript` |
| How vectors are stored per video | `src/rag/vector_store.py:324` `build_index` |
| How passages are retrieved | `src/rag/retriever.py:118` `retrieve` |
| How citations are validated | `src/rag/answer_generator.py:236` `_validate` |
| How an index is managed from the UI | `src/ui/chat_page.py:151` `build_index`/`ask_question` seams |
| How a report is assembled | `src/reports/report_builder.py:401` `build_report` |
| How a format is rendered | `src/reports/export_formats.py:167` `build_export` |

### 20.5 Concise end-to-end workflow diagram

```
[start] app.py → bootstrap → settings → dirs → logging → sidebar → page
   │
[Home] upload | URL ──► validate ──► VideoPipeline.run
                                     1 validate → 2 acquire → 3 inspect
                                     → 4 extract audio → 5 Whisper → 6 store transcript
   │                                                   │
   ├──────────────────────────────► [Summarization] ◄────┘
   │                                    mode/length → prompt → model → JSON
   │                                    → validate (timestamps, owners, quotes) → store summary
   │                                                   │
   ├──────────────────────────────► [Ask Your Video] ◄─┘
   │                                    normalise → chunk → embed → Chroma (per video)
   │                                    question → retrieve S1..Sn → Nemotron
   │                                    → validate citations → answer + timestamps
   │                                                   │
   └──────────────────────────────► [Reports] ◄────────┘
                                        build_report → VideoMindReport → 11 export formats
                                                                              │
                                                                        [download]
```

**Diagram 20.1 — The complete system flow.** Every box is a real module or step in this codebase.

### 20.6 Setup and execution checklist

1. `py -3.12 -m venv .venv`
2. `.venv\Scripts\python.exe -m pip install --upgrade pip`
3. `.venv\Scripts\python.exe -m pip install -r requirements.txt`
4. Install FFmpeg; reopen the terminal; verify `ffmpeg -version` and `ffprobe -version`
5. `copy .env.example .env` and set `NVIDIA_API_KEY` (only needed for Steps 3 and 4)
6. Start: `streamlit run app.py` (or `run_backend.bat`); open http://localhost:8501
7. Settings → run diagnostics and the provider connection test
8. Home → process a short test file first
9. Summarization → generate a summary; check coverage and notes
10. Ask Your Video → build the index; ask a question; verify citations
11. Reports → generate a PDF, then the same content as XLSX and PPTX
12. `python -m pytest` → expect **651 passed**

### 20.7 Testing checklist

- [ ] Full suite green (`651 passed`)
- [ ] `tests/test_url_ingestion.py` (SSRF/limits) green
- [ ] `tests/test_rag_vector_store.py` + `test_rag_service.py` (isolation, staleness) green
- [ ] Export tests green for every format (PDF reopened with `pypdf`, DOCX/PPTX with their libraries, XLSX with `openpyxl`)
- [ ] Optional: `scripts/verify_step2_media.py` for a real FFmpeg + Whisper run
- [ ] Optional live checks: one summary run, one question with citations, one report download

### 20.8 Known issues and remaining work

| ID | Issue | Status |
| --- | --- | --- |
| ISS-001 | YouTube bot check refuses anonymous downloads | Handling fixed and documented; authorized cookies optional; the restriction remains |
| ISS-002 | YouTube HTTP 403 on some media | Handling fixed (explicit 403 message); the restriction remains |
| ISS-003 | Media CDN DNS failure was blamed on the link | Fixed and verified (accurate DNS message added) |
| ISS-004 | Summary truncated when the budget is spent on reasoning | **Open** — root cause established, mitigation documented (`SUMMARY_MAX_TOKENS`) |
| ISS-005 | Whisper `base` mis-heard a word in the live test | **Open** — model accuracy, not a code defect |
| ISS-006 | Chat history is session-scoped, not persisted | **Open** — documented limitation |
| ISS-007 | Reuse caption promised a preview that was not shown | Fixed |
| ISS-008 | Settings diagnostics still said Steps 3–5 were "still planned" | Fixed |
| ISS-009 | An interrupted transcription left a permanent job lock | Fixed; recovery verified |

Additional work (not issues, but genuinely unfinished): long-transcript summarisation has no live
run recorded, answers are not streamed into the UI, and processing is synchronous.

### 20.9 Files to open when explaining the project

| Question you will be asked | Open this |
| --- | --- |
| "How does the application start?" | `app.py` |
| "Where is the configuration and is the key safe?" | `config/settings.py`, `src/services/nvidia_client.py`, `src/utils/logging_config.py` |
| "Show me the ingestion pipeline" | `src/services/video_pipeline.py` (+ `media_service.py`, `transcription_service.py`) |
| "How is a URL made safe?" | `src/services/url_ingestion_service.py` |
| "How does summarisation work?" | `src/services/summarization_service.py`, `src/prompts/summary_prompts.py`, `src/services/summary_validation.py` |
| "How does the chatbot work?" | `src/rag/service.py`, `retriever.py`, `answer_generator.py`, `chunking.py`, `vector_store.py` |
| "How do reports get made?" | `src/reports/report_builder.py`, `export_formats.py` |
| "How do you prove it works?" | `tests/` (651 tests) and `docs/PROJECT_STATUS.md` |
| "What is broken or unproven?" | `docs/DEVELOPMENT_ISSUES.md` and Chapter 17 of this guide |

---

### Appendix A — How this document was produced and verified

| Step | Method | Result |
| --- | --- | --- |
| Project root identified | `pwd` in the workspace | `C:\ai video assistant` |
| Source inventory | `scripts/inventory_for_docs.py` (AST, no execution) | `docs/_inventory.txt`, 2,829 lines covering 105 modules |
| Line counts | `wc -l` per file | 31,656 lines across `app.py`, `config/`, `src/`, `tests/`, `scripts/` (30,165 excluding the 1,491-line PDF builder) |
| Test suite | `python -m pytest -o addopts="--strict-markers"` | **651 passed in 48.84 s** (26 Oct 2026) |
| Per-module test counts | `pytest --collect-only -q` | Table in §15.2 |
| Configuration facts | Read `config/settings.py`, `.env.example` | Chapter 2 environment table |
| Issue/verification facts | Read `docs/DEVELOPMENT_ISSUES.md`, `docs/PROJECT_STATUS.md` | Chapters 6, 15, 17, 20.8 |
| Secrets | `.env` was **never read**; only `.env.example` (placeholders) and `.gitignore` were inspected | No credential appears in this document |
| Application modified? | No source file was changed. Only documentation and two helper scripts were added: `scripts/inventory_for_docs.py` and `scripts/generate_project_guide_pdf.py` | The runtime behaviour is untouched |
| PDF checked after the build | Every page rendered and its text extracted | 92 pages, 20 chapters and 2 appendices, 198 bookmarks; the printed contents page numbers match the bookmarks |

### Appendix B — Regenerating this handbook

The Markdown sources live in `docs/_guide_parts/*.md` and are concatenated into
`docs/VideoMind_AI_Complete_Project_Guide.md`. The PDF is generated from that Markdown with
ReportLab Platypus:

```bash
python scripts/generate_project_guide_pdf.py
```

The script writes `docs/VideoMind_AI_Complete_Project_Guide.pdf` with a cover page, an automatically
numbered table of contents with bookmarks, repeating headers/footers with page numbers, styled code
blocks that wrap, and tables that do not overflow the page.

The four emoji quoted in §5.5 are the real `NAV_PAGES` icons. They are drawn with the system symbol
font, so they appear as monochrome symbols; because they are outside the Basic Multilingual Plane,
those four characters do not copy cleanly out of the PDF text layer. The Markdown remains the
authoritative text.
