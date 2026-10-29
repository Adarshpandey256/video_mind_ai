## Chapter 6 — Step 2: Video/audio ingestion and transcription

### 6.1 The five ideas people confuse — and what this code does with each

| Task | Input → output | Module | Notes |
| --- | --- | --- | --- |
| **Upload** | Bytes from the browser → a file under `data/uploads/` | `source_service.save_upload` | Validation happens *while writing*, so a lying size cannot bypass the limit |
| **Download** | A public URL → a media file in the per-job scratch directory | `url_ingestion_service.download_media` | Two strategies: direct streaming or yt-dlp; both bounded |
| **Extract audio** | Any media file → 16 kHz mono WAV | `media_service.extract_audio` | Whisper works best on this shape; already-extracted audio is passed through |
| **Transcribe** | Audio → text with segment start/end times | `transcription_service.transcribe` | Local Whisper; no network |
| **Store** | A transcription → a JSON document that later steps can reload | `transcript_service.build_record` + `transcript_storage.save` | Atomic write; nothing is stored if validation fails |

**Diagram 6.1 — Step 2 in one line each:**

```
URL ──validate_url ──► ValidatedSource(kind=url) ─┐
                                                  ├─► VideoPipeline.run(source)
File ──save_upload──► ValidatedSource(kind=file) ─┘        │
                                                           ├─1 Validating input : path + FFmpeg present
                                                           ├─2 Acquiring media  : download (URL only)
                                                           ├─3 Inspecting media : ffprobe → MediaInfo + duration limit
                                                           ├─4 Extracting audio : 16 kHz mono WAV, or pass-through
                                                           ├─5 Transcribing     : Whisper → validated segments
                                                           └─6 Saving transcript: TranscriptRecord JSON
                                                     finally: remove the job scratch directory
```

### 6.2 `src/services/source_service.py` — validated uploads (196 lines)

**What/Why:** the browser hands over a filename and a stream; both are hostile until proven
otherwise. This module validates the extension and size, sanitises the name (`..\..\secret` becomes
`secret.<ext>`), guarantees a unique destination, streams the file to disk, and can only delete
files inside the managed directory (`is_inside`).

**Key symbols:** `SourceValidationError`, `StoredUpload` (+`.as_source()`), `validate_upload`,
`unique_upload_path`, `save_upload`, `is_inside`, `cleanup_stored_file`.

Real code, `src/services/source_service.py:111-165`:

```python
def save_upload(
    stream: BinaryIO,
    filename: str,
    settings: Settings | None = None,
    *,
    expected_size: int | None = None,
) -> StoredUpload:
    """Validate and store an upload, streaming it to disk.

    The configured limit is enforced while writing, so an oversized file is
    rejected even if the reported size is wrong. Partial files are always removed
    on failure.
    """
    resolved = settings or get_settings()
    safe_name, source_type = validate_upload(filename, expected_size, resolved)

    directory = ensure_directory(resolved.upload_dir)
    target = unique_upload_path(directory, safe_name)
    limit = resolved.max_upload_size_bytes
    written = 0

    try:
        with target.open("wb") as handle:
            while True:
                chunk = stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise SourceValidationError(
                        f"The upload exceeds the {resolved.max_upload_size_mb} MB limit. "
                        "Trim the recording or raise MAX_UPLOAD_SIZE_MB in your local .env file."
                    )
                handle.write(chunk)
    except BaseException:
        # Never leave a half-written upload behind.
        target.unlink(missing_ok=True)
        raise

    if written == 0:
        target.unlink(missing_ok=True)
        raise SourceValidationError("The uploaded file is empty.")
```

**Data flow:** `(stream, filename, size)` → `validate_upload` → `unique_upload_path` → streamed
write with a running byte count → `StoredUpload(path, safe_filename, source_type, size_bytes)` →
`as_source()` → `ValidatedSource` for the pipeline.

**Callers:** `src/ui/transcription_view.py` (interactive upload) and `scripts/verify_step2_media.py`
(live verification). `cleanup_stored_file` is called by the UI after a successful run: the
application keeps **transcripts**, not copies of your media.

**What can go wrong:** unsupported extension, oversized file, empty file, unsafe filename (handled
by sanitising), duplicate name (handled by a unique suffix), a stream that dies mid-write (partial
file removed).

**Testing:** `tests/test_source_service.py` (14 tests), including
`test_streaming_enforces_the_limit_even_when_the_size_is_understated` and
`test_cleanup_only_deletes_managed_files`.

### 6.3 `src/services/url_ingestion_service.py` — URL acquisition with guards (844 lines)

**What/Why:** accepting a URL means your server will fetch whatever the user typed. This module is
the defence: syntax validation, scheme allow-list, embedded-credential rejection, DNS resolution to
a **public** address only, explicit blocking of loopback/private/link-local/multicast/reserved space
(including IPv4-mapped, 6to4 and NAT64 forms), redirect re-validation, a byte cap while streaming, a
timeout, and a duration check after the page metadata is read.

**Two strategies** (`resolve_download_strategy` → `strategy_explanation` shows it in the UI):

| Strategy | When | Function |
| --- | --- | --- |
| `direct` | The URL points at a media file (or content type says so) | `_download_direct` → `_stream_response` |
| `platform` | The URL is a platform page (YouTube and friends) | `_download_with_ytdlp` |

**Error translation — the heart of the honest reporting.** Every failure becomes a typed exception
with a message that says what actually happened. Real code, `src/services/url_ingestion_service.py:780-795`:

```python
    # Checked before the sign-in branch: the platform's bot-check hint mentions
    # "--cookies", but the cause is proof-of-humanity, not a private recording.
    if "not a bot" in message or ("confirm" in message and "bot" in message):
        return DownloadFailedError(
            "YouTube refused the anonymous download with its \"confirm you're not a bot\" "
            "check. Nothing was bypassed. If you have authorized access to this "
            "recording, export your own cookies and set YTDLP_COOKIES_FILE (or set "
            "YTDLP_COOKIES_FROM_BROWSER=chrome/edge/firefox), then retry. Otherwise "
            "upload an authorized copy of the recording."
        )
    if "http error 403" in message or "forbidden" in message:
        return DownloadFailedError(
            "The source refused the download with HTTP 403 Forbidden. This is usually "
            "a platform access restriction rather than a local problem. ..."
        )
```

followed by branches for cookies/decryption, private or sign-in-only recordings, unavailable/removed
media, duration limit, size limit, unsupported URL, **DNS failure** ("the page itself was read, but
the media host did not resolve"), timeout, and finally a generic message.

**Authorized access (optional, off by default):** `YTDLP_COOKIES_FILE` (a Netscape `cookies.txt`
you export from your own signed-in browser) or `YTDLP_COOKIES_FROM_BROWSER=<browser>`. `_cookie_options`
validates the file exists, refuses unknown browser names, and never logs or uploads the contents.
`cookies*.txt` is in `.gitignore`.

**What can go wrong (all mapped, tested):** malformed URL, free text like "not a url", unsupported
scheme (`file://`), credentials embedded in the URL (`http://user:pass@host`), obvious local
hostnames (`localhost`, `.local`), private/metadata addresses (`169.254.169.254`), redirect to a
blocked address, redirect loop, declared size over limit, streamed size over limit (download
stopped and partial file removed), timeout, network failure, bot check, 403, private video,
unavailable video, missing yt-dlp, missing cookie file.

**Testing:** `tests/test_url_ingestion.py` — **67 tests**, the largest module in the suite.

### 6.4 `src/services/media_service.py` — inspection and audio extraction (348 lines)

**What/Why:** FFmpeg is the only reliable way to read the audio out of (almost) any container.
`probe_media` runs `ffprobe -print_format json` and converts the payload to a typed `MediaInfo`
(duration, container, streams, whether an audio track exists, video/audio codecs). `extract_audio`
runs FFmpeg to produce 16 kHz mono PCM WAV. `prepare_audio_for_transcription` decides between
pass-through (already a usable audio file) and extraction, returning `(path, extracted_flag)`.

**Key symbols:** `MediaToolingError`, `MediaInspectionError`, `MediaDurationError`,
`AudioExtractionError`, `NoAudioStreamError`, `MediaProcessingTimeout`, `MediaTooling`,
`detect_media_tooling` (cached), `require_media_tooling`, `probe_media`,
`_media_info_from_payload`, `enforce_duration_limit`, `extract_audio`,
`prepare_audio_for_transcription`, `FFMPEG_SETUP_MESSAGE`.

**Design decisions worth defending in a viva:**

- Commands are **argument lists** with `shell=False` and a timeout — user input can never become a
  shell command.
- Cover-art streams and video-only files are handled explicitly; a file with no audio stream raises
  `NoAudioStreamError` *before* Whisper is asked to do anything.
- FFmpeg's stderr is captured and logged as a short diagnostic only (never shown raw to the user).
- Tooling discovery is cached, but a missing FFmpeg produces an install-instruction message rather
  than a crash.

**What can go wrong:** FFmpeg not installed, file missing/unreadable, unparsable ffprobe output,
recording longer than `MAX_VIDEO_DURATION_SECONDS`, no audio stream, extraction failure (partial WAV
removed), FFmpeg exceeding `MEDIA_PROCESSING_TIMEOUT_SECONDS`.

**Testing:** `tests/test_media_service.py` (18 tests) — including a real-tooling detection test and
a fake-`subprocess` extraction test that asserts the exact argument list.

### 6.5 `src/services/transcription_service.py` — local Whisper (404 lines)

**What/Why:** speech-to-text with timestamps, run locally so audio never leaves the machine.

**How it works** — real code, `src/services/transcription_service.py:232-292` (abridged in the
middle, marked with `...`):

```python
    def transcribe(
        self,
        audio_path: Path | str,
        options: TranscriptionOptions | None = None,
        *,
        progress: Callable[[str], None] | None = None,
        audio_duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file and return a validated result."""
        resolved_options = options or TranscriptionOptions()
        audio = Path(audio_path)
        if not audio.is_file():
            raise TranscriptionServiceError("The prepared audio file could not be found.")

        model_name = (resolved_options.model_name or self._settings.whisper_model).strip().lower()
        device = self.resolve_device(resolved_options.device)
        language = self.validate_language(
            resolved_options.language if resolved_options.language is not None
            else self._settings.whisper_language
        )

        _notify(progress, f"Loading Whisper model '{model_name}' (device: {device})...")
        model = self.load_model(model_name, device)
        ...
        try:
            raw = model.transcribe(
                str(audio),
                language=language,
                task="transcribe",
                fp16=(device == "cuda"),
                verbose=False,
            )
        except MemoryError as error:
            raise TranscriptionFailedError(
                "Transcription ran out of memory. Try a smaller WHISPER_MODEL such as 'tiny' or "
                "'base', or close other applications."
            ) from error
        except Exception as error:  # noqa: BLE001 - any backend failure becomes a safe error
            logger.info("Whisper transcription failed: %s", type(error).__name__)
            raise TranscriptionFailedError(
                "Transcription failed while processing the audio. Try again, or use a smaller "
                "Whisper model."
            ) from error
        elapsed = round(time.perf_counter() - started, 2)

        _notify(progress, "Validating transcript...")
        return self._build_result(
            raw,
            model_name=model_name,
            device=device,
            requested_language=language,
            processing_seconds=elapsed,
            audio_duration_seconds=audio_duration_seconds,
        )
```

**Device resolution:** `resolve_device("auto")` uses CUDA only when PyTorch reports it; asking for
CUDA on a CPU-only machine falls back to CPU honestly (tested). Models are cached per
`(model, device)` and can be released with `unload_whisper_models()`.

**Validation (`_build_result` / `_validate_segments`)** is where Whisper's raw output becomes
trustworthy:

| Rule | Consequence |
| --- | --- |
| Segments sorted chronologically | Out-of-order backend output is corrected |
| Negative or non-numeric timestamps | Kept as `None` ("timestamp unavailable") rather than `0.0` |
| End before start | The impossible interval is rejected |
| Blank segments | Skipped without losing the words around them |
| No speech at all | `EmptyTranscriptError` — the job fails honestly instead of storing an empty transcript |
| Unexpected payload shape | `InvalidTranscriptionError` |

**Language handling:** `whispered_language_names()` exposes Whisper's supported codes;
`validate_language` rejects unknown codes with a clear message, and `None` means automatic
detection.

**Callers:** `VideoPipeline.run` (step 5) and `src/ui/transcription_view.py` (readiness check:
`media_stack_ready`, `_readiness`).

**Testing:** `tests/test_transcription_service.py` (28 tests, `FakeModel` backend, no real audio
needed) plus `test_whisper_is_installed_in_this_environment`, which guards the claim that Whisper is
actually installed here.

### 6.6 `src/services/video_pipeline.py` — the orchestrator (469 lines)

**What/Why:** one object owns the whole Step 2 workflow, so no page has to coordinate six services.

**The stages are constants, and the UI displays exactly these labels**
(`src/services/video_pipeline.py:51-70`):

```python
STAGE_VALIDATE = "Validating input"
STAGE_ACQUIRE = "Acquiring media"
STAGE_INSPECT = "Inspecting media"
STAGE_EXTRACT = "Extracting audio"
STAGE_TRANSCRIBE = "Transcribing"
STAGE_STORE = "Saving transcript"
STAGE_CLEANUP = "Cleaning up"

#: Stages the interface displays, in order.
PIPELINE_STAGES: tuple[str, ...] = (
    STAGE_VALIDATE, STAGE_ACQUIRE, STAGE_INSPECT,
    STAGE_EXTRACT, STAGE_TRANSCRIBE, STAGE_STORE,
)

#: Which outcome to report when an unexpected error appears in a given stage.
_OUTCOME_BY_STAGE = {
    STAGE_VALIDATE: PipelineOutcome.INVALID_INPUT,
    STAGE_ACQUIRE: PipelineOutcome.DOWNLOAD_FAILED,
    STAGE_INSPECT: PipelineOutcome.MEDIA_INSPECTION_FAILED,
    STAGE_EXTRACT: PipelineOutcome.AUDIO_EXTRACTION_FAILED,
    STAGE_TRANSCRIBE: PipelineOutcome.TRANSCRIPTION_FAILED,
    STAGE_STORE: PipelineOutcome.STORAGE_FAILED,
    STAGE_CLEANUP: PipelineOutcome.SUCCESS,
}
```

**How `run()` flows** (`src/services/video_pipeline.py:240-380`):

```python
    def run(self, source, *, options=None, progress=None, store=True) -> PipelineResult:
        """Process one validated source and return a structured result."""
        stages: list[PipelineStageReport] = []
        current_stage = STAGE_VALIDATE
        # The pipeline owns this directory: everything written into it (downloads,
        # extracted audio) is removed in `finally`, on success and on failure alike.
        job_dir = Path(self._settings.media_temp_dir) / f"job-{uuid.uuid4().hex[:12]}"
        job_dir.mkdir(parents=True, exist_ok=True)
        title = source.title
        ...
        try:
            # 1 - input validation
            media_path = self._resolve_source_path(source)
            tooling = self._tooling(self._settings)
            if not getattr(tooling, "available", False):
                raise media_service.MediaToolingError(media_service.FFMPEG_SETUP_MESSAGE)
            report(STAGE_VALIDATE, StageStatus.COMPLETED, detail=_short(source.reference))

            # 2 - acquisition
            if source.kind is IngestionKind.URL:
                downloaded = self._download(source.url or "", self._settings,
                                            progress=progress, directory=job_dir)
                media_path = Path(downloaded.path)
                title = downloaded.title or title
            else:
                report(STAGE_ACQUIRE, StageStatus.COMPLETED, detail="Local upload (no download)")

            # 3 - inspection  (probe + duration limit)
            # 4 - audio preparation (also detects a missing audio track)
            # 5 - transcription, 6 - storage
        finally:
            self._cleanup_job(job_dir)
```

Notice three deliberate properties: (1) the scratch directory is created here and removed in
`finally`, so a failed run leaves no media behind; (2) an unavailable FFmpeg is detected at the
*first* stage rather than after a long download; (3) every stage appends a `PipelineStageReport`,
so the UI can render the true checklist.

**`PipelineResult`** carries the transcript, the stages, and the outcome; `succeeded` is true only
when a validated transcript was stored. Failures never store a partial transcript
(`test_no_transcript_is_stored_after_a_failure`).

**`JobRegistry`** guards the UI against duplicates and stale locks:

| Method | Purpose |
| --- | --- |
| `start(job_key)` / `is_running` / `running_for` | Mark a job in flight; measure how long it has been running |
| `finish(job_key, transcript_id)` / `completed_transcript` | Remember a successful result so the same source is not reprocessed |
| `reset_stale(max_seconds)` | Clear a lock left by an interrupted run (browser closed mid-worker) |
| `reset_running()` / `running_keys()` | Offer an explicit recovery action in the UI (`_render_stuck_job_reset`) |
| `forget` / `forget_transcript` | Allow reprocessing after deletion |

`job_key_for(source)` builds a stable key from kind + reference + size (+ strategy), so the same
source is recognised across Streamlit reruns.

**Testing:** `tests/test_video_pipeline.py` (27 tests) covers the happy path, every stage failure,
download cleanup on success *and* failure, missing FFmpeg, blocked URLs, duration limits, missing
audio, invalid transcripts, storage failure, unexpected errors, and the registry behaviours
including `test_job_registry_clears_a_lock_left_by_an_interrupted_run`.

### 6.7 `transcript_service.py` + `transcript_storage.py` — persistence

**`transcript_storage.py` (163 lines):** one JSON document per transcript under
`data/processed/transcripts/`, written **atomically** (temporary file + `os.replace`) so a crash
cannot leave a half-written transcript. It validates identifiers against a safe pattern before
building a path (`path_for`), skips unreadable documents when listing
(`test_list_metadata_is_newest_first_and_skips_damaged_files`), and raises
`TranscriptNotFoundError` for a missing id.

**`transcript_service.py` (163 lines):** `build_record` combines the validated source, the
transcription result and media information into a `TranscriptRecord`; `video_id_for(source_reference)`
derives a **stable per-source id** (`sha1`-based) that Steps 4 and 5 use to scope an index or a
conversation — this is the technical reason two videos can never share retrieval results. Also
`to_txt`/`to_json` (with `UNAVAILABLE_TIMESTAMP` handling) and `prune_old_transcripts`
(disabled by default; enabled via `TRANSCRIPT_RETENTION_DAYS`).

**Testing:** `tests/test_transcript_storage.py` (22 tests): round-trip, unique ids, unsafe
identifiers, invalid JSON, incomplete documents, storage failure, newest-first listing, delete,
pruning and export content.

### 6.8 `src/ui/transcription_view.py` — the Home page workflow (688 lines)

**What:** the complete Step 2 experience. Local validation feedback appears as you type; the media
stack readiness (`_readiness`, `media_stack_ready`) is shown before you start; the acquisition path
(`strategy_explanation`) is previewed for URLs; per-request options (`_render_options`) let you
override the Whisper model, device and language; `execute_transcription` validates, checks the
duplicate lock, runs the pipeline with live progress, and stores a `TranscriptionJobOutcome`.
Afterwards `render_transcript_detail` shows metadata, TXT/JSON downloads, timestamped segment
blocks and the full text — grouped in blocks (`_segment_blocks`) so a long transcript stays
readable, with all text escaped (`_escape`).

**Why it is written this way:** the page never guesses. `render_job_stages` displays the pipeline's
own `PipelineStageReport` list, so "Transcribing ✅ / Saving transcript ⏳" is the truth from the
service, not a timer.

**Errors surfaced to the user:** blocked URL, unsupported URL, missing FFmpeg, unavailable/private
media, bot check, 403, DNS failure, no audio track, empty transcript, transcription failure, storage
failure, duplicate job, and a stuck lock with an explicit reset button.

### 6.9 Session transcripts, live evidence and what remains unverified

| Claim | Status | Evidence |
| --- | --- | --- |
| Upload → validate → FFmpeg → Whisper → store works | Implemented, mocked-only in tests; **live-verified via `scripts/verify_step2_media.py`** (per `docs/PROJECT_STATUS.md`) | `tests/test_video_pipeline.py`; `logs/verify-step2.log` |
| Real Whisper transcription on this machine | Live-verified 2026-10-25 | `docs/PROJECT_STATUS.md`: CPU `base` model, timestamped segments |
| URL → download → transcribe, public YouTube video | Live-verified 2026-10-25 (`jNQXAC9IVRw`, 474 478 bytes → transcript `tr-20261025T070926-488d4736`, 4 segments) | `docs/DEVELOPMENT_ISSUES.md` ISS-001 |
| The two originally failing videos | **Not working anonymously** — bot check / 403 (`ISS-001`, `ISS-002`); handling fixed, restriction remains | `docs/DEVELOPMENT_ISSUES.md` |
| Very large media (4K, 722 MB) | Live-verified download after a DNS retry | `ISS-003` |
| Windows-only synthetic fixture script | Script exists (`scripts/verify_step2_media.py`) and uses the Windows speech engine | Source file; a Linux/macOS run would need different fixtures |
