## Chapter 10 — Complete end-to-end execution flow

This chapter follows the **real** code path a user takes, function by function. Where your prompt
list and the implementation differ, the implementation wins — and those differences are called out.

### 10.1 Startup (before any user action)

| # | Step | Code |
| --- | --- | --- |
| 1 | Streamlit starts `app.py` and calls `main()` | `app.py:50` |
| 2 | Page configuration is set (**must be the first Streamlit call**) | `app.py:53-58` |
| 3 | `.env` is loaded (real environment wins) | `bootstrap` → `config.settings.load_environment` |
| 4 | Settings are validated; an invalid value falls back to defaults and is recorded | `get_settings` → `Settings()` |
| 5 | Working directories are created (`data/…`, `logs/`, `reports/`, `chroma_data/`) | `Settings.ensure_directories` |
| 6 | Logging is configured (console + rotating `logs/app.log`, secret redaction) | `configure_logging` |
| 7 | Session-state keys are initialised; the stylesheet is injected | `layout.initialise_session_state`, `inject_styles` |
| 8 | The sidebar renders navigation + the key-status badge and returns a page key | `layout.render_sidebar` |
| 9 | The selected page renders inside a `try/except`; a failure shows a safe banner | `layout.render_page` |

**Note:** no model call, no download and no index build happens at this point. Two tests assert that
loading a page never triggers work (`test_index_selection_never_generates_anything_on_load`,
`test_loading_the_page_never_generates_a_summary`).

### 10.2 Ingestion: upload or URL → validated source

| # | Step | Code |
| --- | --- | --- |
| 1 | The Home page renders the input choice (upload / URL) with live validation feedback | `transcription_view._render_upload_input`, `_render_url_input` |
| 2 | Per-request transcription options are collected (model, device, language) | `_render_options` → `TranscriptionOptions` |
| 3 | "Process source" calls the single entry point | `_handle_start` → `execute_transcription(settings, upload=…, url=…, options=…)` |
| 4a | Upload path: the stream is validated and written to `data/uploads/` | `source_service.save_upload` |
| 4b | URL path: syntax + destination validation, then a `ValidatedSource` describing the acquisition | `url_ingestion_service.build_url_source` → `validate_url` |
| 5 | The duplicate guard checks whether this exact source is already running or done | `JobRegistry.is_running` / `completed_transcript` |

### 10.3 Processing: the six real stages

`VideoPipeline.run(source, options, progress, store)` (`src/services/video_pipeline.py:240`):

| Stage | What actually happens | Failure outcome |
| --- | --- | --- |
| 1 Validating input | The upload is confirmed to still exist **inside** the managed directory; FFmpeg/ffprobe are located | `INVALID_INPUT` / tooling error message |
| 2 Acquiring media | URL only: download into `data/temp/job-<hex>/` (bypassable via `URL_DOWNLOADS_ENABLED=false`) | `DOWNLOAD_FAILED`, `RECORDING_TOO_LONG`, blocked URL, bot check, 403, DNS |
| 3 Inspecting media | `ffprobe` → `MediaInfo`; duration limit enforced | `MEDIA_INSPECTION_FAILED` |
| 4 Extracting audio | 16 kHz mono WAV, or pass-through for a usable audio file; a missing audio track is detected here | `AUDIO_EXTRACTION_FAILED` |
| 5 Transcribing | Whisper (cached model) → validated, chronological segments | `TRANSCRIPTION_FAILED` (includes empty transcript) |
| 6 Saving transcript | `TranscriptRecord` built and written atomically | `STORAGE_FAILED` |
| finally | The job scratch directory is deleted (downloads + extracted audio) | — |

The result is a `PipelineResult` with the transcript, the six `PipelineStageReport`s and an outcome.
The Home page renders those stages verbatim (`render_job_stages`) and then the transcript detail
(metadata, TXT/JSON downloads, timestamped segments, full text).

**Dependency note:** stages 3–6 all need stage 1's tooling. A missing FFmpeg therefore stops the run
*before* a large download, and a missing audio stream stops it *before* Whisper is loaded.

### 10.4 Summarisation (Step 3)

| # | Step | Code |
| --- | --- | --- |
| 1 | The Summarization page lists stored transcripts; opening one loads it | `summarization_page.stored_transcripts`, `load_transcript` |
| 2 | Mode, length, language, custom instruction, timestamps and action-item switches are collected into validated options | `summary_options` → `SummaryOptions` |
| 3 | The page estimates requests and tokens **before** spending anything | `estimate_requests` → `TranscriptPreprocessor` |
| 4 | A matching saved summary may be reused with no model call | `matching_saved_summary` → `SummaryStorage.latest_for_transcript` |
| 5 | Generate → the service runs single-pass or chunk-and-merge | `run_summarization` → `SummarizationService.summarize` |
| 6 | Model JSON is validated into `StructuredSummary`; timestamps/owners/quotes are checked against the transcript | `summary_validation`, `timestamp_service` |
| 7 | The summary is stored (a storage failure still returns the summary) | `SummaryStorage.save` |
| 8 | The page renders takeaways/topics/chapters/actions with real timestamps, plus coverage and quality notes | `render_summary`, `_render_stages` |

### 10.5 Indexing (Step 4, first half)

| # | Step | Code |
| --- | --- | --- |
| 1 | The chat page lists videos and reports index state honestly (`NOT_INDEXED`, `READY`, `STALE`, `EMBEDDING_MISMATCH`, `UNAVAILABLE`) | `chat_page.stored_videos`, `RagService.index_status` |
| 2 | "Build index" (or "Rebuild") is pressed | `_render_index_controls` → `build_index` |
| 3 | The transcript is normalised and fingerprinted | `normalize_transcript`, `compute_fingerprint` |
| 4 | It is chunked with timestamps preserved | `TranscriptChunker.chunk_transcript` |
| 5 | The newest stored summary (if any) is added as labelled context | `RagService._summary_chunks` |
| 6 | Chunks are embedded locally in batches and written to a per-video Chroma collection | `ChromaVectorStore.build_index` |
| 7 | The registry records collection, model id, fingerprint, counts and time | `IndexRegistry.put` |
| 8 | The page reruns and displays the new state; a stage callback reports real progress | `_run_index_build`, `RagService._notify` |

### 10.6 Asking a question (Step 4, second half)

| # | Step | Code |
| --- | --- | --- |
| 1 | The question is typed (or a suggestion is clicked) | `_render_conversation`, `_render_suggestions` |
| 2 | History for **this video** is loaded | `get_history(state, video_id)` |
| 3 | The service validates question, transcript, index and provider | `RagService.ask` |
| 4 | Retrieval scoped to this video, with conversation-aware query widening | `Retriever.retrieve`, `contextual_query` |
| 5 | Passages are labelled `S1…Sn` and the prompt is assembled with untrusted-data framing | `rag.prompts.render_sources`, `build_answer_user_prompt` |
| 6 | The LCEL chain calls Nemotron through the single `LLMService` | `GroundedAnswerGenerator.generate` |
| 7 | JSON is extracted (one repair retry if needed) and citations are validated | `extract_json_object`, `_validate` |
| 8 | The answer + citations are stored in the video's conversation and rendered with real timestamps | `_assistant_message`, `_render_citations` |
| 9 | A failure is shown honestly with an explicit retry that does not duplicate the question | `_render_failed_question`, `test_retry_reuses_the_question_without_duplicating_it` |

### 10.7 Report export (Step 5)

| # | Step | Code |
| --- | --- | --- |
| 1 | The user selects a video; sections are enabled/disabled from real availability | `reports_page._render_video_selector`, `_render_overview` |
| 2 | Sections, title/author/organisation/notes and the timestamps toggle are collected | `_render_content_controls` → `ReportOptions` |
| 3 | A short preview is shown | `_render_preview` |
| 4 | An available format is chosen | `_available_formats` |
| 5 | Generate → the report is built from stored data, then rendered | `_generate` → `build_report` → `build_export` |
| 6 | Bytes are cached under a fingerprint of every input | `export_cache_key` |
| 7 | The file is offered for download with the correct MIME type and a safe filename | `_render_download` |

### 10.8 What depends on what

| Depends on | Dependency | Consequence when it is missing |
| --- | --- | --- |
| Any processing | Valid configuration, writable directories | Settings page explains the problem; the shell still opens |
| Step 2 ingestion | FFmpeg/ffprobe | Stopped at stage 1 with install instructions |
| Step 2 transcription | Whisper + torch installed; a downloadable model | `WhisperUnavailableError` naming the install step |
| Steps 3 and 4 answering | `NVIDIA_API_KEY` | `MISSING_CREDENTIALS`, no request sent |
| Step 4 indexing | `sentence-transformers` + a downloadable embedding model | `IndexOutcome.EMBEDDING_UNAVAILABLE` |
| Step 4 answering | A **ready** index for that video | `NO_INDEX` / `STALE`; the page offers Build or Rebuild |
| Step 5 | A stored transcript; a summary and/or chat only for those sections | Sections are disabled with reasons |
| Step 5 PDF/DOCX/XLSX/PPTX | `reportlab`, `python-docx`, `openpyxl`, `python-pptx` | That format reports itself unavailable (`format_unavailable_reason`) |

**Independent work:** transcription, storage, indexing, retrieval, answer validation and all exports
work without the provider key. Only summary generation and answer generation need NVIDIA.

---

## Chapter 11 — Important functions, classes, variables and imports

This reference lists the *important* callables with their real location and role. The complete
auto-generated inventory of all 1,290 classes/functions (with line numbers, signatures and first
docstring lines) can be reproduced at any time with:

```
./.venv/Scripts/python scripts/inventory_for_docs.py > docs/_inventory.txt
```

### 11.1 Entry point, configuration and utilities

| Symbol | Location | Purpose | Parameters / returns | Raises | Called by |
| --- | --- | --- | --- | --- | --- |
| `bootstrap` | `app.py:23` | Load `.env`, build settings, create directories, configure logging | `→ Settings` | swallows config/OS errors deliberately | `app.main` |
| `main` | `app.py:50` | Page config, styles, sidebar, page dispatch | `→ None` | page error caught and rendered as a banner | Streamlit |
| `Settings` | `config/settings.py:120` | All validated configuration + path properties | `BaseSettings` | `ValidationError` (caught by `get_settings`) | every module |
| `load_environment` | `config/settings.py:622` | Load `.env` without overriding the process environment | `env_file: Path | None → bool` | — | `app.bootstrap`, `get_settings` |
| `get_settings` | `config/settings.py:647` | Cached settings; invalid input → defaults + recorded error | `→ Settings` | — | everywhere |
| `get_settings_load_error` / `reset_settings_cache` | `config/settings.py:670/675` | Report the last configuration problem / clear the cache | `→ str | None` / `→ None` | — | Settings page, tests |
| `describe_validation_error` | `config/settings.py:637` | Turn a pydantic error into a short non-secret hint | `ValidationError → str` | — | `get_settings` |
| `redact_secrets` / `RedactionFilter` | `src/utils/logging_config.py:29/48` | Remove keys/authorization headers from log records | `str → str` / `LogRecord → bool` | — | logging pipeline |
| `configure_logging` | `src/utils/logging_config.py:66` | Console + rotating file logging | `level, log_dir, debug, force → Path | None` | — | `app.bootstrap` |
| `get_logger` | `src/utils/logging_config.py:130` | Module logger | `name → Logger` | — | every module |
| `sanitize_filename` / `normalise_extension` / `is_supported_media_file` / `validate_upload_size` / `guess_source_type` / `looks_like_url` / `human_readable_size` | `src/utils/file_utils.py:78/41/48/60/94/104/27` | Filename, extension, size and type helpers | see signatures | — | `source_service`, `url_ingestion_service`, `transcription_view` |
| `extract_json_object` | `src/utils/json_utils.py:68` | Find the first valid JSON object, ignoring braces inside strings | `text, limit → dict` | `ModelOutputError` | Steps 3 and 4 |
| `coerce_text` / `coerce_optional_text` / `coerce_str_list` / `coerce_float` / `coerce_seconds` | `src/utils/json_utils.py:100/118/132/161/180` | Tolerant coercion of model output | see signatures | — | Steps 3 and 4 validation |
| `format_timestamp` / `format_duration` | `src/utils/time_utils.py:8/23` | `MM:SS`/`HH:MM:SS` and human durations; honest when unknown | `float | None → str` | — | UI, exporters |

### 11.2 Provider and language-model services

| Symbol | Location | Purpose | Notes |
| --- | --- | --- | --- |
| `create_nvidia_client` | `src/services/nvidia_client.py:97` | Build the OpenAI-compatible client (key from settings only) | `NvidiaConfigurationError` when unset |
| `get_nvidia_client` / `reset_nvidia_client_cache` | `…:126/131` | Cached single client | One per process |
| `is_nvidia_configured` / `describe_client_config` | `…:136/141` | Non-secret status | Used by health checks |
| `classify_error_type` | `…:71` | Stable, non-sensitive error type string | Shared by Steps 1/3/4 |
| `LLMService.generate` | `src/services/llm_service.py:108` | One chat completion: messages, temperature, token budget, timeout | Retries parameter quirks; returns `LLMResponse` |
| `LLMService.stream_generate` | `…:164` | Streaming variant | **Present but unused** |
| `LLMService.test_connection` | `…:210` | Explicit connection test | Never raises; returns `ConnectionTestResult` |
| `LLMService._apply_parameter_fallback` | `…:329` | Retry with `max_completion_tokens`, drop unsupported `temperature` | Logged |
| `LLMService._translate_error` | `…:360` | Provider exception → application error | Safe messages only |

### 11.3 Step 2 services

| Symbol | Location | Purpose | Raises |
| --- | --- | --- | --- |
| `validate_upload` | `src/services/source_service.py:61` | Validate filename/size; return safe name + `SourceType` | `SourceValidationError` |
| `save_upload` | `…:111` | Stream to disk with a running byte limit; remove partials | `SourceValidationError` |
| `cleanup_stored_file` / `is_inside` | `…:178/169` | Delete only inside the managed directory / containment test | — |
| `validate_url_syntax` / `validate_url` | `src/services/url_ingestion_service.py:192/334` | Syntax, then syntax + public destination | `UnsupportedUrlError`, `BlockedDestinationError`, `DownloadFailedError` |
| `address_is_blocked` / `assert_public_destination` | `…:266/297` | SSRF block-list including IPv4-mapped/6to4/NAT64 | `BlockedDestinationError` |
| `resolve_download_strategy` / `strategy_explanation` | `…:325/364` | `direct` vs `platform`, explained in the UI | — |
| `build_url_source` | `…:349` | `ValidatedSource` for a URL without downloading | same as `validate_url` |
| `download_media` | `…:405` | Acquire media into a scratch directory (cache-aware per URL) | `DownloadFailedError`, `DownloadTooLargeError`, `DownloadTimeoutError`, `RecordingTooLongError` |
| `_download_direct` / `_stream_response` | `…:448/520` | Bounded streaming with redirect re-validation | size/timeout errors |
| `_download_with_ytdlp` / `_cookie_options` | `…:598/706` | Platform download with optional authorized cookies | `DownloadFailedError` |
| `_translate_ytdlp_error` | `…:776` | Map yt-dlp failures to accurate messages | returns the right error type |
| `detect_media_tooling` / `require_media_tooling` | `src/services/media_service.py:80/91` | Locate (cached) or require FFmpeg/ffprobe | `MediaToolingError` |
| `probe_media` / `_media_info_from_payload` | `…:130/176` | ffprobe → `MediaInfo` | `MediaInspectionError` |
| `enforce_duration_limit` | `…:234` | Reject too-long media | `MediaDurationError` |
| `extract_audio` / `prepare_audio_for_transcription` | `…:246/320` | 16 kHz mono WAV; pass-through when already audio | `AudioExtractionError`, `NoAudioStreamError`, `MediaProcessingTimeout` |
| `TranscriptionService.transcribe` | `src/services/transcription_service.py:232` | Whisper → validated segments | `EmptyTranscriptError`, `InvalidTranscriptionError`, `TranscriptionFailedError`, `WhisperUnavailableError`, `UnknownLanguageError`, `ModelLoadError` |
| `TranscriptionService.resolve_device` / `load_model` / `validate_language` | `…:146/172/216` | Device fallback, cached checkpoint, language validation | `ModelLoadError`, `UnknownLanguageError` |
| `unload_whisper_models` / `cuda_available_cached` / `whispered_language_names` | `…:75/82/87` | Free memory, probe CUDA once, expose languages | — |
| `VideoPipeline.run` | `src/services/video_pipeline.py:240` | Six-stage orchestration; always cleans the job directory | never raises; returns `PipelineResult` |
| `VideoPipeline._translate` | `…:407` | Exception → `(PipelineOutcome, user message)` | — |
| `JobRegistry.*` | `…:101-190` | Duplicate/stale-lock guard, completed-transcript memory | — |
| `job_key_for` | `…:93` | Stable key from kind+reference+size(+strategy) | — |
| `TranscriptStorage.save/load/list_metadata/delete` | `src/services/transcript_storage.py:72/112/137/151` | Atomic JSON per transcript | `TranscriptStorageError`, `TranscriptNotFoundError` |
| `TranscriptService.build_record/save/load/list_transcripts/delete` | `src/services/transcript_service.py:60/90/94/98/102` | Domain service over the storage | `TranscriptStorageError` |
| `video_id_for` | `…:32` | Stable per-source id used by Steps 4–5 | — |
| `TranscriptService.to_txt/to_json/download_filename` | `…:131/156/160` | Transcript downloads | — |

### 11.4 Step 3 services

| Symbol | Location | Purpose | Raises |
| --- | --- | --- | --- |
| `summarize_transcript` | `src/services/summarization_service.py:632` | Service-free entry point | never raises |
| `SummarizationService.summarize` | `…:119` | Single-pass or chunk-and-merge, stage reporting | never raises; sets `SummaryResult.outcome` |
| `_summarize_single` / `_summarize_chunked` | `…:286/377` | The two strategies | — |
| `_transcript_problem` / `_failure_result` / `_reserved_tokens` | `…:560/588/551` | Pre-flight checks and honest failures | — |
| `SummaryJsonClient.request_json` | `src/services/summary_generation.py:127` | Ask for JSON, extract, repair once, bounded retries | `SummaryGenerationError(error_type=…)` |
| `SummaryGenerationError` / `_retryable` / `_truncation_hint` | `…:89/75/223` | Failure type, retry policy, budget advice | — |
| `TranscriptPreprocessor.prepare` | `src/services/transcript_preprocessor.py:164` | Render + chunk to fit the request budget | — |
| `estimate_tokens` / `tokenizer_is_exact` / `render_transcript` | `…:51/46/80` | Token estimate, exactness flag, prompt text | — |
| `ChunkSummarizer.summarize_chunk` | `src/services/chunk_summarizer.py:83` | One chunk → `ChunkSummary` + model-call count | `SummaryGenerationError` |
| `SummaryMerger.merge` / `_merge_group` | `src/services/summary_merger.py:47/113` | Merge sections (grouped when many) | `SummaryGenerationError` |
| `build_structured_summary` | `src/services/summary_validation.py:294` | Raw JSON → typed summary + quality notes | — |
| `build_takeaways/topics/chapters/action_items/decisions/points` | `…:110/147/173/200/252/271` | Per-section validated builders | — |
| `deduplicate` / `quality_notes` | `…:94/393` | Duplicate removal; deterministic honesty notes | — |
| `validator_for` / `TimestampValidator.sanitize` / `is_grounded` | `src/services/timestamp_service.py:184/70/202` | Timestamp validation and text grounding | — |
| `SummaryStorage.save/load/list_metadata/latest_for_transcript/delete` | `src/services/summary_storage.py:80/127/152/180/208` | Atomic JSON per summary; reuse lookup | `SummaryStorageError`, `SummaryNotFoundError` |

### 11.5 Step 4 services

| Symbol | Location | Purpose | Raises |
| --- | --- | --- | --- |
| `normalize_transcript` / `compute_fingerprint` / `normalize_text` | `src/rag/transcript_adapter.py:111/98/39` | Retrieval-shape transcript, staleness hash, whitespace collapse | `TranscriptNormalizationError` |
| `TranscriptChunker.chunk_transcript` | `src/rag/chunking.py:265` | Ordered timestamped chunks with overlap | `ChunkingError` |
| `TranscriptChunker.chunk_summary` / `summary_text` | `…:308/341` | Summary as labelled context | — |
| `_segment_units` / `_pack` / `_build_chunk` / `_overlap_tail` | `…:77/171/196/129` | The chunking algorithm | — |
| `build_embeddings` / `LocalSentenceTransformerEmbeddings` | `src/rag/embeddings.py:145/89` | Local embedding provider | `EmbeddingError` |
| `embedding_model_id` / `load_sentence_transformer` / `reset_embedding_cache` | `…:40/51/84` | Model identity, cached load, cache reset | `EmbeddingError` |
| `ChromaVectorStore.build_index` | `src/rag/vector_store.py:324` | Embed + store + activate | `VectorStoreError`, `EmbeddingError` |
| `ChromaVectorStore.status` / `search` / `count` / `delete` | `…:406/450/520/530` | Honest state, per-video search, count, removal | `IndexNotReadyError`, `VectorStoreError` |
| `IndexRegistry.load/get/put/remove/all` | `…:162/183/187/193/201` | Atomic JSON registry | — |
| `collection_name_for` / `_persistent_client` / `reset_vector_store_cache` | `…:266/237/261` | Naming and client lifecycle | — |
| `Retriever.retrieve` / `_within_context_budget` | `src/rag/retriever.py:118/189` | Ranked, de-duplicated, budgeted evidence with `S#` refs | `RetrievalError`, `IndexNotReadyError` |
| `contextual_query` / `needs_conversation_context` | `…:90/70` | Follow-up widening | — |
| `render_sources` / `build_answer_user_prompt` / `build_repair_prompt` | `src/rag/prompts.py:79/108/136` | Evidence rendering and the answer contract | — |
| `GroundedAnswerGenerator.generate` | `src/rag/answer_generator.py:116` | Answer with validated citations | `AnswerGenerationError(error_type=…)` |
| `_build_chain` / `_call_model` / `_validate` / `_invoke` | `…:185/192/236/316` | LCEL chain, single-model-call rule, citation validation | — |
| `quote_appears_in` / `normalize_reference` / `_strip_unknown_inline_references` | `…:80/74/331` | Quote verification, ref normalisation, stray marker cleanup | — |
| `RagService.index_transcript` | `src/rag/service.py:151` | Build or reuse an index; stage callbacks | never raises; `IndexResult` |
| `RagService.ask` | `…:266` | Question → grounded answer | never raises; `RagResult` |
| `RagService.index_status` / `delete_index` / `list_videos` / `load_transcript` | `…:135/261/126/130` | State, removal, listings | `TranscriptStorageError` |
| `_build_answer` / `_summary_chunks` / `_outcome_for_answer_error` | `…:400/425/459` | Answer assembly, summary context, outcome mapping | — |

### 11.6 Step 5 functions

| Symbol | Location | Purpose |
| --- | --- | --- |
| `build_report` | `src/reports/report_builder.py:401` | Assemble the canonical report from real data |
| `section_availability` / `default_report_title` / `safe_source_url` | `…:347/82/90` | Availability mapping, title derivation, URL sanitising |
| `_normalise_chat_messages` / `_summary_model` / `_transcript_entries` | `…:123/227/335` | Adapters from Steps 2–4 into report models |
| `build_export` / `get_format` / `format_unavailable_reason` | `src/reports/export_formats.py:167/148/156` | Render a format, look one up, explain unavailability |
| `report_filename` / `safe_stem` / `safe_extension` | `src/reports/filename_utils.py:68/54/34` | Safe, bounded, unique download names |
| `build_pdf` / `resolve_font` / `_PageDecorator` | `src/reports/pdf_exporter.py:460/123/214` | PDF, font selection, header/footer page numbers |
| `build_docx` / `_configure_document` / `_add_field` | `src/reports/docx_exporter.py:323/80/62` | Word document, styles, PAGE/NUMPAGES fields |
| `render_text` | `src/reports/text_exporter.py:209` | Wrapped plain text |
| `render_markdown` / `escape_block` / `_fence` | `src/reports/markdown_exporter.py:229/32/47` | Escaped Markdown with adaptive fences |
| `render_json` / `report_to_dict` | `src/reports/json_exporter.py:21/16` | Schema-versioned JSON |
| `render_html` / `_safe_href` | `src/reports/html_exporter.py:313/117` | Escaped self-contained HTML with safe links |
| `render_csv_transcript/chat/sources` / `protect_csv_cell` | `src/reports/csv_exporter.py:46/75/100/25` | Tabular exports with formula neutralisation |
| `build_xlsx` / `_set_text` / `_overview` | `src/reports/xlsx_exporter.py:108/41/79` | Workbook, literal text, overview sheet |
| `build_pptx` / `_Deck` | `src/reports/pptx_exporter.py:155/119` | Concise deck with numbering/footers |

### 11.7 UI seam functions (what tests stub)

| Symbol | Location | Purpose |
| --- | --- | --- |
| `render_sidebar` / `render_page` / `resolve_page` / `go_to_page` | `src/ui/layout.py:113/156/74/100` | Navigation and dispatch |
| `render_home_page` / `render_settings_page` / `render_*_page` | `…:217/326/298/307/316` | Page renderers |
| `run_health_checks` / `summarise_checks` / `environment_details` | `src/services/health_service.py:282/302/319` | Diagnostics |
| `execute_transcription` | `src/ui/transcription_view.py:110` | Validate input, guard duplicates, run the pipeline, return `TranscriptionJobOutcome` |
| `run_summarization` / `summary_options` / `estimate_requests` | `src/ui/summarization_page.py:186/157/199` | The only provider call on the page; options; cost preview |
| `service_for` / `build_index` / `ask_question` / `delete_index` / `history_key` | `src/ui/chat_page.py:137/188/201/215/82` | Chat seams and per-video history keys |
| `export_cache_key` / `_generate` / `chat_messages` | `src/ui/reports_page.py:136/536/131` | Stale-bytes fingerprint, report generation, per-video chat reads |

### 11.8 Important variables and imports

| Name | Where | Why it matters |
| --- | --- | --- |
| `PIPELINE_STAGES` | `video_pipeline.py:54` | The exact stage labels the UI displays |
| `_OUTCOME_BY_STAGE` | `video_pipeline.py:64` | Maps an unexpected exception to the right outcome |
| `EXPORT_FORMATS` / `FORMAT_REQUIREMENTS` / `DEFAULT_FORMAT_KEY` | `export_formats.py:33/136/145` | Single source of truth for formats |
| `REPORT_SECTION_ORDER` / `DEFAULT_REPORT_SECTIONS` | `report_models.py:78/83` | Canonical order and a safe default selection |
| `SUMMARY_MODE_CHOICES` / `SUMMARY_LENGTH_CHOICES` | `summary_schemas.py` | Drive both configuration validation and the UI |
| `NAV_PAGES` / `DEFAULT_PAGE` / `ROADMAP_STAGES` | `schemas.py:326/364/366` | Navigation and the Home roadmap |
| `PROJECT_ROOT` / `ENV_FILE` | `settings.py:42/43` | Base for `.env` and all data paths |
| `PAGE_TITLE` / `PAGE_ICON` | `app.py:19/20` | Streamlit page identity |
| `MISSING_CREDENTIALS_MESSAGE` | `nvidia_client.py:19` | One shared, safe "no key" message |
| `FFMPEG_SETUP_MESSAGE` | `media_service.py` | Install instructions shown when FFmpeg is missing |
| Notable imports | `from __future__ import annotations` (every module), `pydantic`/`pydantic_settings` (models/config), `openai` (provider), `streamlit` (UI), `langchain_core`/`langchain_chroma`/`chromadb` (Step 4), `reportlab`/`docx`/`openpyxl`/`pptx` (Step 5), `subprocess`+`shutil` (FFmpeg), `lru_cache` (caching), `threading`/`uuid` (locks and job ids) |
