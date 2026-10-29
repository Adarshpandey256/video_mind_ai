# VideoMind AI — Development Issue Log

Maintained during Steps 1–5 and the final integration pass.
Last updated: **2026-10-25**.

Status vocabulary: **open** · **investigating** · **fixed** (cause addressed,
regression test added) · **verified** (fixed and re-checked through the affected
interface).

| ID | Short description | Blocks a required feature? | Status |
| --- | --- | --- | --- |
| ISS-001 | YouTube refused an anonymous download with a bot check | Yes, for those videos (Step 2 URL path) | fixed handling; live YouTube e2e verified on a public video |
| ISS-002 | YouTube media download returned HTTP 403 Forbidden | Yes, for that video (Step 2 URL path) | fixed handling; external restriction remains |
| ISS-003 | Media CDN host failed DNS resolution and was reported as a bad link | No (retry succeeded) | verified |
| ISS-004 | Summary truncated after spending the whole output budget on a long transcript | Yes, for that long recording at the default budget | open (root cause established, mitigation documented) |
| ISS-005 | Whisper `base` mis-heard "trunks" as "punks" in the live test | No | open (model accuracy, not a code defect) |
| ISS-006 | Chat history is session-scoped, not persisted across restarts | No | open (documented limitation) |
| ISS-007 | Reuse caption claimed a saved summary was “shown below” before it was opened | No | fixed |
| ISS-008 | Settings diagnostics still reported Steps 3–5 as “still planned” | No | fixed |
| ISS-009 | An interrupted transcription left a permanent “Job already running” lock | Yes, until the app restarted | fixed, recovery verified |

---

## ISS-001 — YouTube bot check blocks anonymous URL ingestion

**Affected feature:** Step 2 — URL ingestion (`src/services/url_ingestion_service.py`,
`src/services/video_pipeline.py`, `src/ui/transcription_view.py`).

**First observed:** 2026-10-25 11:15:00, repeated at 11:17:58, 11:18:32 and 11:19:11
(see `logs/app.log`).

**Exact provider message** (as recorded by the application; the logger truncates the
yt-dlp text to 200 characters):

```
2026-10-25 11:15:00 | INFO | src.services.url_ingestion_service | yt-dlp reported: ERROR: [youtube] nyhRNwTfydU: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pa
2026-10-25 11:15:00 | INFO | src.services.video_pipeline | Processing failed (stage=Acquiring media, outcome=download_failed, error=DownloadFailedError)
```

The same message was returned for `n5ksOqcBWoQ` at 11:17:58, 11:18:32 and 11:19:11.

**Steps to reproduce:**

1. Start the app, open **Home**, choose **From URL**, paste
   `https://www.youtube.com/watch?v=n5ksOqcBWoQ`.
2. Press **Process source**.
3. The pipeline stops at **Acquiring media** with `download_failed`.

**Root cause:** YouTube’s anti-bot check refuses the anonymous request from this
network; it is the platform’s access control, not a defect in validation, DNS or
FFmpeg. `yt-dlp` reported it as a `DownloadError`, and the old translator collapsed
every generic `DownloadError` into “check the link”, which blamed the user’s URL.

**Temporary workaround at the time:** upload an authorized copy of the recording.

**Fix (implemented):**

- A dedicated branch in `_translate_ytdlp_error()` recognises the bot check and returns
  an accurate message that names the two supported authorized-access settings and says
  explicitly that nothing was bypassed.
- Optional authorized access was added: `YTDLP_COOKIES_FILE` (a Netscape `cookies.txt`
  the user exports from their own signed-in browser) or
  `YTDLP_COOKIES_FROM_BROWSER` (a supported local browser name). The file is validated
  before any request; cookies are passed to yt-dlp only, never logged, stored or
  uploaded. Unknown browser names are refused. Both are unset by default, so the app
  remains anonymous unless the user opts in.
- Docs: README “Platform bot checks (authorized access only)” and `.env.example`.
- `cookies*.txt` is git-ignored.

**Verification:**

- Regression tests in `tests/test_url_ingestion.py`:
  `test_bot_check_is_reported_with_authorized_cookie_guidance`,
  `test_cookies_file_is_passed_to_ytdlp`,
  `test_missing_cookies_file_is_a_clear_local_error`,
  `test_browser_cookies_are_opted_into_by_name`,
  `test_unknown_cookie_browser_is_refused`,
  `test_forbidden_download_is_reported_as_an_access_restriction`.
- Live, first through the service and then through the actual Streamlit UI on
  2026-10-25: `https://www.youtube.com/watch?v=jNQXAC9IVRw` was downloaded
  anonymously (474 478 bytes), inspected, extracted to 16 kHz mono, transcribed with
  Whisper `base` on CPU, and stored as `tr-20261025T070926-488d4736` (4 timestamped
  segments). The UI showed every stage and the saved transcript. This is a complete
  URL → media → Whisper → persistence run, not a mock.
- A 4K YouTube URL (`aqz-KE-bpKQ`) also downloaded successfully after the ISS-003
  retry (722 512 892 bytes), confirming larger media works within the 1 GiB limit.
- The two originally failing videos were **not** retried with the user’s real
  cookies, so they are **not** marked working; they need authorized cookies or an
  uploaded copy.

**Status:** fixed (diagnosis, error handling, authorized access path, tests). The
platform restriction itself remains for anonymous access.

---

## ISS-002 — YouTube download returned HTTP 403 Forbidden

**Affected feature:** Step 2 — URL ingestion.

**Observed:** 2026-10-23 21:19:31 (`logs/app.log`).

```
2026-10-23 21:19:31 | INFO | src.services.url_ingestion_service | yt-dlp reported: ERROR: unable to download video data: HTTP Error 403: Forbidden
2026-10-23 21:19:31 | INFO | src.services.video_pipeline | Processing failed (stage=Acquiring media, outcome=download_failed, error=DownloadFailedError)
```

**Steps to reproduce:** paste the affected YouTube URL on **Home → From URL** and run
the pipeline. The page is read, but the media request is refused.

**Root cause:** the platform refused the media transfer (access control / bot
mitigation). The application had no 403-specific branch, so the failure surfaced as
the generic “could not be downloaded” message.

**Fix:** explicit 403/Forbidden mapping that explains the restriction and points at
the authorized-cookie settings or an uploaded copy. Regression test:
`test_forbidden_download_is_reported_as_an_access_restriction`.

**Status:** fixed handling; the external restriction remains for anonymous access.

---

## ISS-003 — Media CDN DNS failure was blamed on the link

**Affected feature:** Step 2 — platform download (`_translate_ytdlp_error`).

**Observed:** 2026-10-25, first live probe of
`https://www.youtube.com/watch?v=aqz-KE-bpKQ`. Page metadata was read successfully,
then the media CDN host failed to resolve:

```
Traceback (most recent call last):
  ...
  File "C:\ai video assistant\.venv\Lib\site-packages\yt_dlp\downloader\common.py", line 416, in <lambda>
    error=IDENTITY if not fatal else lambda e: self.report_error(f'\r[download] Got error: {e}'),
  File "C:\ai video assistant\.venv\Lib\site-packages\yt_dlp\YoutubeDL.py", line 1103, in trouble
    raise DownloadError(message, exc_info)
yt_dlp.utils.DownloadError: ERROR: 
[download] Got error: HTTPSConnection(host='rr2---sn-gwpa-a3vr.googlevideo.com', port=443):
Failed to resolve 'rr2---sn-gwpa-a3vr.googlevideo.com' ([Errno 11001] getaddrinfo failed).
Giving up after 2 retries

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File ".../yt_probe.py", line 11, in <module>
    media = u.download_media(url, settings, directory=Path("data/temp/yt-probe"))
  File "C:\ai video assistant\src\services\url_ingestion_service.py", line 676, in _download_with_ytdlp
    raise _translate_ytdlp_error(error) from error
src.services.url_ingestion_service.DownloadFailedError:
The media could not be downloaded from this page. Check the link, or upload the recording instead.
```

**Steps to reproduce:** on a network where the googlevideo CDN host cannot be
resolved, download any YouTube URL through `download_media`. Metadata succeeds; the
media phase fails.

**Root cause:** transient/local DNS resolution failure for the CDN host (the host
resolved normally with `nslookup` afterwards). The translator had no DNS branch, so a
network problem was presented as a URL problem.

**Fix:** DNS-specific branches (`getaddrinfo failed`, `failed to resolve`,
`name or service not known`, `nodename nor servname`) return a message that says the
page was read, the media host did not resolve, and the connection/VPN/DNS should be
checked. Regression test: `test_media_host_dns_failure_is_not_blamed_on_the_link`.
Despite the provider line being at the download stage, the test is an integration
test of the error mapper, not of the network.

**Verification (live):**

- Immediate retry of the same URL succeeded: 722 512 892 bytes, title
  “Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film”.
- A second, short video (`jNQXAC9IVRw`) downloaded and transcribed end-to-end.

**Status:** verified.

---

## ISS-004 — Summary truncated on a long transcript

**Affected feature:** Step 3 — summarisation (`src/services/summary_generation.py`,
`src/services/summarization_service.py`).

**Observed:** 2026-10-25 10:51:41, on a stored 113-segment transcript, with the
default configuration (`SUMMARY_MAX_TOKENS=16384`, model
`nvidia/nemotron-3.5-lightning-30b-a3b`).

```
2026-10-25 10:51:41 | INFO  | httpx2 | HTTP Request: POST https://integrate.api.nvidia.com/v1/chat/completions "HTTP/1.1 200 OK"
2026-10-25 10:51:41 | WARNING | src.services.summary_generation | Summarisation request failed (stage=summary, type=truncated, elapsed_ms=147266)
2026-10-25 10:51:41 | WARNING | src.services.summarization_service | Summarisation failed (stage=generate, type=truncated)
2026-10-25 10:51:41 | INFO  | src.ui.summarization_page | Summary run finished (outcome=provider_failed, partial=False)
```

**Steps to reproduce:** summarise a long transcript (here: 113 segments, several
minutes of speech) in **Quick summary**, leaving `SUMMARY_MAX_TOKENS=16384`. The
provider returns HTTP 200 with `finish_reason=length` and no answer.

**Root cause:** the default model is a reasoning model. It writes a private reasoning
trace before the answer and that trace shares the output budget. For this transcript
the reasoning alone reached 16 384 tokens, so no JSON answer was produced. This is a
model/budget property, not a parsing bug: the shorter 4-segment live transcript
succeeded with the same settings (14 002 total tokens, `finish_reason=stop`,
118.9 s).

**Blocking?** It blocks summarising that specific long recording at the default
budget, but not Steps 4–5, which can work from the transcript alone. The failure is
reported honestly and no partial summary is stored.

**Workaround / mitigation:**

- Raise `SUMMARY_MAX_TOKENS` (allowed up to 32 768) and retry.
- Or force chunking by lowering `SUMMARY_CHUNK_MAX_TOKENS` below the transcript’s
  estimated token count, so each request reasons over less text.
- Or use a non-reasoning model through `NVIDIA_MODEL` (must be available to the
  account).
- The UI message already names `SUMMARY_MAX_TOKENS` and never retries with the same
  exhausted budget.

**Proposed fix (not implemented):** on a single-pass truncation, automatically fall
back to the existing chunked pipeline (which would be labelled `used_chunking=True`
and report coverage), instead of requiring a manual configuration change. This needs
a live budget measurement per chunk before it can be claimed as working, so it is
tracked rather than rushed.

**Regression coverage:** `tests/test_summarization_service.py` and
`tests/test_summary_prompts.py` cover the truncated error type, the absence of a
retry with the same budget, and the user-facing message. No test claims a successful
summary of the 113-segment transcript.

**Status:** open (cause established, honest failure verified live, mitigation
documented).

---

## ISS-005 — Whisper base mis-heard a word in the live test

**Affected feature:** Step 2 — transcription accuracy.

**Observed:** 2026-10-25 live YouTube run, transcript
`tr-20261025T060418-8f222835`: “they have really, really, really long **punks**”
(the source says “trunks”).

**Root cause:** the `base` checkpoint’s accuracy; audio only, no language context.
Not a timestamp, storage or pipeline defect.

**Action:** no code change. The model name and size are shown, and the README already
documents that small checkpoints mis-hear proper nouns/technical terms. The live
summary’s `limitations` correctly flagged the unclear term instead of inventing a
meaning.

**Status:** open (expected model limitation; use `small`/`medium` when accuracy
matters).

---

## ISS-006 — Chat history is session-scoped

**Affected feature:** Step 4 — per-video chat history (`src/ui/chat_page.py`).

**Observed:** history is stored in `st.session_state`, keyed by `video_id`. It
survives video switches within the session but is gone after the Streamlit process
restarts; nothing is written to disk.

**Impact:** non-blocking. Answers are still grounded and citations still validated;
the transcript and vector index are persistent.

**Proposed improvement:** persist turns next to the summary in `data/processed/`, with
an explicit delete action. Not implemented because it changes the data model and was
outside the requested five steps.

**Status:** open (documented limitation).

---

## ISS-007 — Reuse caption promised a preview that was not rendered

**Affected feature:** Step 3 UI — `src/ui/summarization_page.py`
(`_render_generate_area`).

**Observed:** 2026-10-25 during the browser integration pass. With a stored summary
matching the current transcript/settings, the page displayed:

> A saved summary from these exact settings is shown below. Regenerate only if you
> want the model to run again.

but no summary body was rendered. Only the **Saved summaries** picker appeared
further down; the summary was displayed after pressing **Open this summary** (which
loads it from disk with “No new model request was made.”).

**Root cause:** wording only. The reuse check correctly prevents a duplicate model
call and offers **Regenerate anyway**, but the caption described a preview the result
area does not render until an explicit open. No data was lost and no model call was
made.

**Fix:** the caption now says the saved summary “already exists”, points at **Open
this summary**, and only then offers regeneration. Regression test:
`test_reuse_caption_points_to_the_open_action_instead_of_claiming_preview` in
`tests/test_summarization_ui.py` (asserts the result area is still empty before the
open action).

**Status:** fixed; UI test passes.

---

## ISS-008 — Settings diagnostics reported implemented steps as “still planned”

**Affected feature:** Step 1 diagnostics — `src/services/health_service.py`,
shown on **Settings**.

**Observed:** 2026-10-25 in the browser integration pass. The panel said:

> **Later-stage features** — pending — “Transcription is implemented (Step 2). Still
> planned: summarisation (Step 3), RAG chat (Step 4) and report export (Step 5).
> Optional packages already importable: chromadb, sentence_transformers, reportlab,
> docx.”

At that point the same running app had a stored live summary, a ready Chroma index,
a live grounded answer and an exported PDF, so the diagnostics contradicted the
application’s real state.

**Root cause:** `check_future_stages()` was written during Step 1/2 and hard-coded a
`PENDING` status plus the old roadmap text. It was never updated when Steps 3–5 were
implemented. No functional feature was broken, but a diagnostic that lies about
implementation status is a real defect.

**Fix:** replaced it with `check_later_stage_dependencies()`, which:

- checks the packages actually used by Steps 3–5 (`langchain-core`,
  `langchain-chroma`, `chromadb`, `sentence-transformers`, `reportlab`,
  `python-docx`, `openpyxl`, `python-pptx`);
- reports **ok** (“implemented, and their dependencies are installed”) when all are
  present, and **warning** naming the missing packages otherwise;
- never returns `PENDING` and never says “still planned”.

**Regression test:** `test_health_checks_are_honest_without_credentials` in
`tests/test_app_foundation.py` now asserts the check is `OK` in the project
environment and that “implemented” appears while “Still planned” does not.

**Status:** fixed; the check is re-verified below in the browser pass.

---

## ISS-009 — An interrupted run left a permanent job lock

**Affected feature:** Step 2 UI — `src/ui/transcription_view.py`, `JobRegistry` in
`src/services/video_pipeline.py`.

**Observed:** 2026-10-25 during the browser pass, after the preview connection
dropped while a YouTube transcription was running. The downloaded media had been
fetched (`Platform download completed (bytes=474478)`), no transcript was stored and
no `job-*` directory remained, but every later click returned:

> **Job already running** — “This source is already being processed. Please wait for
> the current job to finish.”

and the status panel said **Nothing was processed**. The lock never cleared, so that
source could not be processed again until the app restarted.

**Root cause:** `JobRegistry` only tracked a boolean per job key. `execute_transcription`
clears it in its normal/except paths, but when the Streamlit run thread is torn down
mid-job (browser closed, process killed) no Python frame is left to run `finally`, so
the session kept the lock. There was no age check and no recovery action. Separately,
the status label for the `duplicate` and `running` outcomes was the generic “Nothing
was processed”, which was inaccurate for a reused transcript.

**Fix:**

- `JobRegistry` now records `time.monotonic()` per running key and exposes
  `running_for()`, `running_keys()`, `reset_stale(max_seconds)` and `reset_running()`.
- Before refusing a start, `execute_transcription` clears locks older than
  `MEDIA_PROCESSING_TIMEOUT_SECONDS + URL_DOWNLOAD_TIMEOUT_SECONDS + 60`, so a very
  long genuine job is never stolen from, but a dead one eventually frees itself.
- The running message now reports the elapsed time and points at the recovery control.
- The page renders **Reset stuck job lock** from the persistent session state (not
  from the click handler, which would disappear on the next rerun). The button clears
  running locks only; completed transcripts are kept. It is explicit and labelled as
  an interruption recovery, not an automatic duplicate override.
- Distinct status labels: “Existing transcript reused” (complete), “Job already
  running” and “Nothing was processed” (invalid input).

**Regression tests:**

- `test_job_registry_clears_a_lock_left_by_an_interrupted_run`
- `test_job_registry_can_reset_all_running_locks_on_request`
- `test_stuck_job_lock_can_be_reset_from_the_page` (AppTest: the guard still blocks,
  the visible reset clears it, and the next click runs the pipeline exactly once)

**Verification:** the live stuck state reproduced the defect before the fix; after a
restart with the new code, a full YouTube → transcription run completed through the
browser and stored `tr-20261025T070926-488d4736`. The reset control itself was
verified with `AppTest` rather than by killing a browser session mid-run.

**Status:** fixed; recovery path covered by tests.
