## Chapter 12 — Data models and data flow

Every model below is a real pydantic class in this workspace. Models are the contract between
stages: because they validate on creation, an invalid value (an end time before a start time, an
unsafe id, a missing title) fails early and loudly instead of corrupting a later stage.

### 12.1 Media metadata

**`VideoMetadata`** — `src/models/schemas.py:50`

| Field | Type | Meaning | Created by | Consumed by |
| --- | --- | --- | --- | --- |
| `video_id` | `str` | Stable per-source identifier used by later steps and retrieval | `transcript_service.video_id_for` | Steps 4, 5 |
| `source_type` | `SourceType` (`local_video`/`local_audio`/`url`/`unknown`) | Where the media came from | `source_service.guess_source_type`, `build_url_source` | pipeline, UI, reports |
| `source_title` | `str \| None` | Recording title when known | pipeline (download title) | UI, reports |
| `source_reference` | `str \| None` | Original filename or source URL (never a secret) | pipeline | UI, reports |
| `duration_seconds` | `float \| None` ≥ 0 | Real duration from ffprobe | `media_service.probe_media` | duration guard, reports |
| `status` | `ProcessingStatus` | `pending`/`in_progress`/`completed`/`failed` | pipeline | UI |
| `created_at` | `datetime` (UTC) | When the record was made | `utc_now()` | listings, reports |

**`MediaInfo`** (`schemas.py:223`) is the ffprobe result: container/format, duration, whether an audio
stream exists, codec names and stream counts — the evidence behind "this file has no audio track".

### 12.2 Transcript models

**`TranscriptSegment`** — `schemas.py:65`

| Field | Type | Note |
| --- | --- | --- |
| `segment_index` | `int` ≥ 0 | Position in the recording |
| `start_seconds` | `float \| None` ≥ 0 | `None` when the backend could not supply it (never `0.0` as a guess) |
| `end_seconds` | `float \| None` ≥ 0 | Validated: must not be earlier than the start |
| `text` | `str` | The spoken words |

`has_timestamps` is true only when both ends are known — this is why the UI can honestly print
"timestamp unavailable" instead of a fake `00:00`.

**`TranscriptMetadata`** (`schemas.py:93`) — the index view: `transcript_id`, `video_id`,
`source_type`, `original_filename`, `source_reference`, `title`, `duration_seconds`, `language`,
`transcription_model`, `processing_status`, `segment_count`, `text_reference`, `error_message`,
`created_at`. It deliberately contains **no** segments and no full text, so listing 50 transcripts
stays cheap.

**`TranscriptRecord`** (`schemas.py:118`) extends it with `segments: list[TranscriptSegment]` and
`full_text: str` — the document stored under `data/processed/transcripts/tr-*.json`.

**`TranscriptionResult`** (`schemas.py:245`) is the validated output of the Whisper service (model,
device, language, duration, processing seconds, segments, full text, plus whether language was
auto-detected).

**`ValidatedSource`** (`schemas.py:200`) is what the pipeline receives: `kind`
(`IngestionKind`: upload/URL), `source_type`, `reference`, `title`, `url`, `path`, `size_bytes`,
`download_strategy`, with a `reference` property for display.

### 12.3 Pipeline reporting models

| Model | Fields | Purpose |
| --- | --- | --- |
| `StageStatus` | `pending`, `in_progress`, `completed`, `failed`, `skipped` | State of one stage |
| `PipelineStageReport` (`schemas.py:279`) | `stage`, `status`, `detail` | One user-visible stage line |
| `PipelineResult` (`schemas.py:287`) | `outcome` (`PipelineOutcome`), `message`, `source`, `stages`, `transcript`, `media` | The whole run; `succeeded` is true only when a validated transcript was stored |
| `PipelineOutcome` | `success`, `invalid_input`, `download_failed`, `media_inspection_failed`, `audio_extraction_failed`, `transcription_failed`, `storage_failed`, … | Why the run ended as it did |

### 12.4 Summary models (Step 3)

**`StructuredSummary`** — `src/models/summary_schemas.py:369` (the document stored per summary)

| Field | Type | Meaning |
| --- | --- | --- |
| `summary_id` | `str \| None` | Assigned by `SummaryStorage.save` |
| `source_id` | `str` | Step 2 `video_id` |
| `source_transcript_id` | `str \| None` | Which transcript it summarises |
| `source_title` | `str \| None` | Recording title at generation time |
| `source_type` | `SourceType` | Origin of the media |
| `summary_type` | `SummaryMode` | Which of the eight styles produced it |
| `options_fingerprint` | `str` | Settings fingerprint — makes reuse safe |
| `overall_summary`, `short_summary` | `str`, `str \| None` | The prose |
| `key_takeaways` | `list[KeyTakeaway]` | Insights with optional verified timestamps |
| `main_topics` | `list[Topic]` | Themes + key points |
| `chapters` | `list[Chapter]` | Sections + major points |
| `action_items` | `list[ActionItem]` | Task, owner, deadline (owner/deadline only if stated) |
| `decisions` | `list[Decision]` | Explicit decisions |
| `important_points` | `list[ImportantPoint]` | Grounded points |
| `limitations` | `list[str]` | **What the model or pipeline could not cover** (honesty field) |
| `language`, `generation_model`, `generated_at` | | Provenance |
| `processing_status` | `SummaryStatus` (`completed`/`partial`/`failed`) | Never claims completeness it does not have |
| `coverage` | `SummaryCoverage` | Chunks processed / total, with `is_partial`, `ratio`, `describe()` |
| `model_calls`, `latency_ms`, `used_chunking` | | Cost and strategy evidence |

Item models (all extend `TimestampedItem`, which holds the optional `start_seconds`/`end_seconds`
interval and validates its order): `KeyTakeaway(title, explanation, excerpt)`,
`Topic(title, explanation, key_points)`, `Chapter(title, description, major_points)`,
`ActionItem(task, owner, deadline, status)`, `Decision(decision, context)`,
`ImportantPoint(point, explanation)`.

`SummaryMetadata` is the lightweight listing entry (`from_summary` builds it with a 160-character
preview), and `SummaryOptions` (`mode`, `length`, `language`, custom instruction, timestamps flag,
action-items flag) carries the user's choices together with `fingerprint()`.
`ChunkSummary` is the intermediate per-chunk result with `is_empty` and `render_for_merge()`.

### 12.5 Retrieval models (Step 4)

**`RagChunk`** — `rag_schemas.py:71` (what is embedded and stored)

| Field | Type | Note |
| --- | --- | --- |
| `video_id`, `chunk_id` | `str` | `chunk_id` is deterministic (video + type + position + text hash) |
| `chunk_index` | `int` | Order within the video |
| `document_type` | `DocumentType` (`transcript`/`summary`) | Provenance of the passage |
| `text`, `text_hash` | `str` | Content and its hash |
| `source_name`, `source_type` | | For display and report provenance |
| `start_seconds`, `end_seconds` | `float \| None` | Real recording positions; validated order |
| `segment_start_index`, `segment_end_index` | `int \| None` | Which segments the chunk covers |

`metadata()` produces the Chroma-safe dictionary (`None` values are **omitted**, never stored as 0).

**`RetrievedSource`** — `rag_schemas.py:146`: `source_ref` (`S1`…), `chunk_id`, `video_id`,
`chunk_index`, `document_type`, `text`, `source_name`, `start_seconds`, `end_seconds`, `distance`
(raw cosine distance, lower = closer), `similarity` (0–1). Helpers: `has_timestamps`, `time_label`,
`label`, `excerpt(limit=320)`.

**`RagCitation`** — `rag_schemas.py:195`: `source_ref`, `chunk_id`, `quote` (verified verbatim or
`None`), `source_name`, `document_type`, `start_seconds`, `end_seconds`. `from_source()` is the
**only** way a citation is created, which is why a citation can never carry metadata that was not
retrieved.

**`RagAnswer`** — `rag_schemas.py:233`: `question`, `answer`, `citations`, `sources` (every retrieved
passage, cited or not), `model`, `retrieval_query`, `history_turns`, `insufficient_evidence`,
`notes` (dropped citations etc.), `latency_ms`, `total_tokens`, `created_at`, plus
`cited_sources` which maps citations back to real passages.

**`RagResult`** (`rag_schemas.py:265`): `outcome` (`RagOutcome`), `message`, `answer`, `sources`,
`succeeded`. **`ChatTurn`**: `question`, `answer`, `created_at` — what is sent as history.

**Index models:** `IndexInfo` (`video_id`, `status`, `transcript_id`, `title`, `source_name`,
`chunk_count`, `transcript_chunks`, `summary_chunks`, `embedding_model`, `transcript_hash`,
`collection_name`, `indexed_at`, `message`; helpers `is_ready`, `describe`), `IndexResult`
(`outcome`, `message`, `info`, `chunks_indexed`), `IndexRecord` (the registry row, JSON-serialisable
with `to_dict`/`from_dict`/`to_info`), and `ChatOptions` (per-question `top_k`, `include_summary`,
`temperature`, `max_tokens`).

### 12.6 Report models (Step 5)

| Model | Fields | Purpose |
| --- | --- | --- |
| `ReportOptions` | `sections`, `title`, `author`, `organisation`, `notes`, `include_timestamps` | Validated user choices |
| `ReportSectionChoice` | section + included flag/reason | Records a request and its outcome |
| `ReportVideo` | `video_id`, `transcript_id`, `title`, `original_filename`, `source_type`, `source_url`, `duration_seconds`, `language`, `transcription_model`, `processing_status`, `created_at`, `segment_count`, `has_timestamps` (+`duration_label`) | Recording metadata |
| `ReportSummary` | summary text and structure (+`has_text`) | Step 3 content as included |
| `ReportTakeaway`/`ReportTopic`/`ReportChapter`/`ReportActionItem` | title/text + `time_label` | Summary sections |
| `ReportTranscriptEntry` | `segment_id`, `start_seconds`, `end_seconds`, `text` | One transcript row |
| `ReportCitation` | `source_ref`, `source_name`, `source_kind`, `time_label`, `quote`, `chunk_id` | A validated chat citation |
| `ReportChatEntry` | `question`, `answer`, `citations`, `notes`, `sources_available` | One Q&A |
| `ReportSource` | `source_ref`, `label`, `source_kind`, `time_label`, `excerpt`, `chunk_id` | A retrieved passage |
| `ReportProcessing` | how the content was produced | Reader-facing provenance |
| `VideoMindReport` | id, `REPORT_SCHEMA_VERSION`, app name/version, `generated_at`, options, all sections, availability/omissions; helpers `includes`, `omitted_reason`, `shows_timestamps`, `transcript_characters`, `chat_answers` | The canonical document every exporter reads |

### 12.7 How data transforms, stage by stage

```
User input (file bytes | URL string)
   ├─ source_service.save_upload        → StoredUpload        → ValidatedSource
   └─ url_ingestion_service             → DownloadedMedia     → ValidatedSource
ValidatedSource ──media_service.probe_media──────────────► MediaInfo
                ──TranscriptionService.transcribe─────────► TranscriptionResult (segments)
                ──TranscriptService.build_record──────────► TranscriptRecord (JSON on disk)
TranscriptRecord ──transcript_preprocessor───────────────► PreparedTranscript (text + chunks)
                 ──SummaryJsonClient + validation─────────► StructuredSummary (JSON on disk)
TranscriptRecord ──transcript_adapter────────────────────► NormalizedTranscript (+ fingerprint)
                 ──chunking──────────────────────────────► [RagChunk] ──embeddings──► vectors
                 ──vector_store.build_index───────────────► Chroma collection + IndexRecord
Question ──retriever.retrieve────────────────────────────► [RetrievedSource] (S1..Sn)
         ──answer_generator.generate─────────────────────► RagAnswer(+citations) → RagResult
TranscriptRecord + StructuredSummary + chat messages
         ──report_builder.build_report───────────────────► VideoMindReport
         ──export_formats.build_export───────────────────► RenderResult(bytes) → ReportExport
```

**Diagram 12.1 — Data transformation chain.** Each arrow is a real function call; each box is a
pydantic model, except the DB rows and the final bytes.

### 12.8 Deliberate data-model decisions (good viva material)

| Decision | Reason |
| --- | --- |
| Timestamps are `float \| None`, never defaulted to `0.0` | A fake zero would point at the start of the recording and look real |
| Interval models validate "end ≥ start" | Impossible data cannot reach the model or the report |
| `TranscriptMetadata` excludes segments and text | Listing stays cheap; only the record carries the payload |
| `chunk_id` is deterministic | Rebuilding an unchanged transcript cannot flood the vector store |
| Citations are only created by `RagCitation.from_source` | The model cannot inject invented metadata |
| `options_fingerprint` on a summary | Deciding whether a saved summary may be reused becomes explicit |
| `limitations` and `coverage` live inside the summary | Honesty is part of the data, not a UI afterthought |
| `REPORT_SCHEMA_VERSION` in every JSON export | A machine consumer can adapt instead of silently mis-reading |
| Outcome enums everywhere | Callers branch on *why*, not on "an object came back" |
