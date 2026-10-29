# VideoMind AI — AI Video Assistant

**Turn any supported video into knowledge.**

VideoMind AI is a Streamlit application that will help you understand long videos,
lectures, tutorials, meetings, interviews and webinars without watching the whole
recording: transcription with Whisper, AI summaries with NVIDIA Nemotron, a RAG
chatbot over the transcript, and multi-format report exports.

> **Current status: Steps 1-5 implemented.**
> Step 1 (project structure, secure configuration, NVIDIA client, logging, health
> checks, interface shell, tests), Step 2 (local uploads, supported-URL acquisition,
> FFmpeg audio extraction, local Whisper transcription, timestamped transcript
> models, JSON transcript storage and the transcription interface), Step 3
> (NVIDIA Nemotron summarisation, eight summary styles, long-transcript chunking and
> merging, validated timestamps, structured summary storage and the summarisation
> page), Step 4 (timestamp-aware chunking, local embeddings, a persistent ChromaDB
> index per video, LangChain retrieval, citation-checked answers and the **Ask Your
> Video** chatbot) and Step 5 (a shared report model, selectable content, and real
> PDF, Word, TXT, Markdown, JSON, HTML, CSV, Excel and PowerPoint exports on the
> **Reports** page) are implemented and runnable. See
> [section 6](#6-step-5--generate-and-export-reports) for the export workflow.

---

## 1. Windows setup

Python 3.11+ is supported; this project was developed and tested on **Python 3.12.2**
with PowerShell / Windows Terminal.

```powershell
# 1. Create and activate an isolated virtual environment
py -3.12 -m venv .venv
.venv\Scripts\activate

# 2. Install the dependencies (Steps 1-5: Streamlit, OpenAI SDK, Whisper, yt-dlp,
#    LangChain/ChromaDB/embeddings and the document exporters).
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Install FFmpeg (external binary - required by audio extraction and Whisper)
winget install Gyan.FFmpeg
#    ...then close and reopen the terminal so PATH is refreshed
ffmpeg -version

# 4. Create your local environment file
Copy-Item .env.example .env

# 5. Open .env and set NVIDIA_API_KEY to your own newly generated key (Step 3 needs it)
notepad .env

# 6. Run the application
streamlit run app.py
```

Step 2 (ingestion and transcription) and Step 5 (exporting stored content) work
**without** an NVIDIA key. The key is only needed from Step 3 onwards, for
summarisation and chat answers.

If `py -3.12` is unavailable, run `py -0p` to list the installed interpreters and
use any supported version (3.11 or newer), for example `py -3.11 -m venv .venv`.
Do not silently downgrade the pinned dependency versions: re-resolve them with
`pip install -r requirements.txt` inside the new environment and note the versions
that resolved.

Streamlit then serves the app at <http://localhost:8501>.

### One-click start on Windows

This project is a single Streamlit application: the same process serves the backend
logic **and** the frontend UI. There is no separate Node/React frontend to build or
start, and no second API server to run.

```
run_backend.bat  ──►  Streamlit server on :8501  ──►  browser UI at http://localhost:8501
```

**1. Start the backend** — double-click `run_backend.bat`, or run it from a terminal:

```powershell
.\run_backend.bat
```

The script `cd`s into the project folder, checks that `.venv\Scripts\python.exe`
exists, warns if `.env` is missing, then runs
`.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501 --server.headless true`.
Keep its window open; `Ctrl+C` stops the server. Use another port with
`set STREAMLIT_PORT=8502` (or `$env:STREAMLIT_PORT=8502` in PowerShell) before
launching the script.

**2. Start the frontend** — open the UI in your browser with either command:

```powershell
run_frontend.bat                              # double-clickable launcher
start "" "http://localhost:8501"              # same thing from cmd
Start-Process "http://localhost:8501"         # same thing from PowerShell
```

Or simply visit <http://localhost:8501> yourself. The backend window prints this
command every time it starts. If the page cannot connect, the backend is not running
(or is on a different port).

The `.bat` files contain no secrets: the API key stays in `.env` and is read by Python.

### Where the API key goes

* `NVIDIA_API_KEY` is read from the **local environment only** (`.env` or your shell).
* `.env` is listed in `.gitignore` and is never committed.
* The key is held in a `SecretStr`, never rendered in the interface, never stored in
  Streamlit session state and never sent to the browser.
* Log records are filtered so keys and authorisation headers are redacted.
* If a key has ever been pasted into a chat, a screenshot, a notebook or a commit,
  revoke it in your NVIDIA account and create a new one.

---

## 2. Verify the NVIDIA connection

1. Start the app and open **Settings → NVIDIA connection**.
2. The panel shows the endpoint, the configured model and whether a key is present
   (the value itself is never displayed).
3. Click **Test NVIDIA Connection**. One short request is sent; the result, latency
   and a short preview of the answer are stored in the session and shown on the page.
4. No provider request happens on startup or on a page re-render — only when you
   click the button.

Required endpoint and defaults:

| Setting | Default |
| --- | --- |
| Endpoint | `https://integrate.api.nvidia.com/v1` |
| Model | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Environment variable | `NVIDIA_API_KEY` |

If the provider rejects a model name, change `NVIDIA_MODEL` in `.env` to a model that
is available in your NVIDIA account. Reasoning-style parameters are never mandatory
and reasoning traces are never displayed — only the answer content is shown.

---

## 3. Step 2 — ingest and transcribe a video or audio file

The media input page is **Home** (Step 1 called it "Overview, media input and
processing status"), so the sidebar navigation is unchanged. The page offers two
clearly separated input methods, and neither one starts any work until you press
**Transcribe Video**.

### Supported formats

| Category | Extensions |
| --- | --- |
| Video | `mp4`, `mkv`, `mov`, `avi`, `webm`, `m4v`, `flv`, `wmv`, `mpeg`, `mpg` |
| Audio | `mp3`, `wav`, `m4a`, `aac`, `flac`, `ogg`, `opus`, `wma` |

The allowlist is one setting (`ALLOWED_MEDIA_EXTENSIONS` in `.env`, validated in
`config/settings.py`). An allowed extension is necessary but **not** sufficient:
after upload the file is probed with FFprobe, so a renamed or corrupt file is
rejected with an inspection error instead of being treated as valid media.

Which of these actually decode depends on your FFmpeg build. `Gyan.FFmpeg` (the
winget package above) covers all of them; a minimal or stripped build may not.
Only formats covered by your build are truly supported — verify with
`ffmpeg -codecs` if in doubt.

### Upload limits

| Limit | Default | Setting |
| --- | --- | --- |
| Upload size | 500 MB | `MAX_UPLOAD_SIZE_MB` (mirrored in `.streamlit/config.toml`) |
| Downloaded size | 1024 MB | `MAX_DOWNLOAD_SIZE_MB` |
| Media duration | 7200 s (2 h) | `MAX_VIDEO_DURATION_SECONDS` |
| URL download timeout | 120 s | `URL_DOWNLOAD_TIMEOUT_SECONDS` |
| FFmpeg/FFprobe timeout | 600 s | `MEDIA_PROCESSING_TIMEOUT_SECONDS` |

If a file exceeds the upload or duration limit the page tells you the limit and
suggests a smaller file (for example trimming the recording or exporting a lower
bitrate) rather than failing silently.

### Processing a video file

1. Choose **Upload video or audio**, then pick a file. The page shows the original
   filename, the size, the detected category (video/audio) and the format check.
2. Press **Transcribe Video**. Watch the stage list: *Validating input → Acquiring
   media → Inspecting media → Extracting audio → Transcribing → Saving transcript*.
   Stages that do not apply to your input are shown as skipped.
3. When it finishes you get the title, duration, language, Whisper model, the full
   text, and the timestamped segments (`[MM:SS – MM:SS]`, or `HH:MM:SS` past an
   hour). Long transcripts are grouped into labelled blocks of 40 segments, each
   headed by its timestamp and a short preview, so you can jump around.
4. Download the result as **TXT** or **JSON** from the same page.

### Processing an audio file

Identical flow — upload the audio file and press the button. Audio inputs skip the
"Extracting audio" conversion step (they are already audio) and are transcribed
directly, producing the exact same transcript structure. You never need to wrap an
audio recording in a video container first.

### Acquiring media from a URL

1. Choose **Video URL**, paste a link, and press the validation button. Nothing is
   downloaded yet: the page reports which ingestion path would be attempted.
2. Press **Transcribe Video** to actually acquire and process it.

Nothing that is downloaded is kept: the file is written into the job's own scratch
directory under `data/temp/` and removed when the run ends, on success and on
failure alike. Re-processing the same source in one session is refused by the
duplicate-job guard before any network request is made.

Two acquisition paths are implemented:

* **Direct media URL** — a link whose path ends in a supported extension is streamed
  with a bounded HTTP GET (no redirects to private hosts, size cap enforced,
  `Content-Length` checked up front, partial files removed on failure).
* **Supported media pages** — anything else that is a valid `http(s)` URL is handed to
  **yt-dlp**, which supports the public sites its own extractors cover (YouTube and
  many other platforms). This path is best-effort: it depends on the installed
  yt-dlp version and on the source platform's rules.

Rejected outright: non-`http(s)` schemes (`file:`, `ftp:`, `data:` …), free text and
invalid host names (for example `not a url` or a host without a dot), embedded
credentials, and any host resolving to a loopback, private, link-local, reserved or
cloud-metadata address (SSRF protection). Redirect destinations are re-checked
against the same rules.

IPv4-mapped (`::ffff:127.0.0.1`), 6to4 (`2002::/16`) and NAT64 (`64:ff9b::/96`)
addresses are unwrapped before the decision, so an address that merely *wraps* a
private target is refused while a NAT64 host carrying a public address (common on
IPv6-only DNS resolvers) is not rejected for its wrapper alone.

A platform download can still fail for reasons outside the app's control: private,
region-locked, age-restricted or deleted videos, bot checks, or a site that the
installed yt-dlp version does not cover. The pipeline reports that as
`download_failed` with a readable reason and suggests uploading the file instead.

**Source restrictions.** Only public or otherwise authorized content can be
acquired. The app never bypasses DRM, paywalls, sign-in walls or access controls,
and it cannot download private or deleted media. A **Google Meet** or Zoom meeting
link never grants access to its recording — export or download the recording
legitimately and upload the file instead; the page explains this when a URL cannot
be processed. Set `URL_DOWNLOADS_ENABLED=false` to disable URL acquisition entirely
and keep the app upload-only.

**Platform bot checks (authorized access only).** Some platforms, YouTube in
particular, refuse anonymous downloads with a message like *“Sign in to confirm
you’re not a bot”* or an HTTP 403. That is the platform’s access control, so the app
reports it as `download_failed` and never tries to work around it. If — and only if —
you have authorized access through your own account, you can let yt-dlp reuse that
existing session:

```powershell
# Option A: export your own cookies once (while signed in), then point the app at it
yt-dlp --cookies-from-browser edge --cookies cookies.txt
# .env
YTDLP_COOKIES_FILE=C:\path\to\cookies.txt

# Option B: let yt-dlp read your local browser profile (close the browser first)
# .env
YTDLP_COOKIES_FROM_BROWSER=edge
```

`YTDLP_COOKIES_FILE` wins when both are set; supported browser names are `brave`,
`chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`, `vivaldi` and `whale`.
Cookies stay on your machine: the app passes the choice to yt-dlp, never logs the
values, never uploads them, and still refuses private, DRM-protected or otherwise
unauthorized material. A cookies file is a credential — treat it like `.env`
(`cookies*.txt` is already git-ignored).

### FFmpeg on Windows

FFmpeg is an external binary, not a Python package — installing `openai-whisper`
does not install it.

```powershell
winget install Gyan.FFmpeg
# reopen the terminal so PATH is refreshed, then verify:
ffmpeg -version
ffprobe -version
```

If you cannot change PATH, point the app at the executables explicitly in `.env`:
`FFMPEG_BINARY=C:\ffmpeg\bin\ffmpeg.exe` and
`FFPROBE_BINARY=C:\ffmpeg\bin\ffprobe.exe`. The app checks for both on every health
refresh and shows an actionable setup message in **Settings → Diagnostics** when
they are missing — the rest of the dashboard keeps working.

### Whisper on CPU, and optionally on GPU

The local **openai-whisper** implementation is used (it is installed by
`requirements.txt`). It is the speech-recognition model — never OpenAI's hosted
transcription API, and never your NVIDIA key.

| Model | Speed | Memory | Rough accuracy | Typical use |
| --- | --- | --- | --- | --- |
| `tiny` / `tiny.en` | fastest | lowest | lowest | quick drafts, long files |
| `base` / `base.en` (default) | fast | low | good | everyday CPU transcription |
| `small` / `small.en` | moderate | moderate | better | accented or noisy speech |
| `medium` | slow | high | high | when quality matters |
| `large-v1/v2/v3`, `large` | slowest | highest | highest | best quality, GPU recommended |
| `turbo` | fast | moderate | high | good quality/speed balance |

The `.en` variants are English-only and slightly more accurate for English. Set the
checkpoint with `WHISPER_MODEL` in `.env` (validated against the supported list; an
unknown value logs a warning and falls back to `base`).

**Device selection.** `WHISPER_DEVICE=auto` (default) uses CUDA when the installed
PyTorch build reports a working CUDA device, and CPU otherwise. `cpu` and `cuda`
force a choice. CPU always works — it is simply slower (roughly real-time or better
for `base` on a modern laptop, much slower for `large`). Use `large` on CPU only for
short recordings.

For GPU transcription install the CUDA wheel of PyTorch that matches your driver
before Whisper, e.g. `pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126`,
then confirm with `python -c "import torch; print(torch.cuda.is_available())"`. If it
prints `False`, the app runs on CPU and says so — it never claims GPU acceleration
it does not have. The pinned `torch==2.14.1` in `requirements.txt` is the CPU build.

**Model downloads.** Weights are fetched from the internet the *first* time a given
checkpoint is used and cached under `~/.cache/whisper`. Nothing is downloaded at
startup or at import time. On a machine without internet access, pre-download the
model once and copy the cache directory across.

**Language.** Leave `WHISPER_LANGUAGE` empty (or `auto`) for automatic detection, or
set a code such as `en`, `es`, `de`, `hi`. The page also offers a per-run choice
between automatic detection and a specific language. The language that was used is
recorded on the transcript.

### Where transcripts are stored

One JSON document per transcript, in `data/processed/transcripts/<transcript_id>.json`
(git-ignored). Each document holds the metadata, the timestamped segments, the full
text and the source reference — never the media file itself and never a secret. See
[section 11](#11-step-2-transcript-contract-consumed-by-steps-3-4-and-5) for the exact
shape. Extracted audio and
downloads live in `data/temp/job-<id>/` and are deleted as soon as the job finishes,
successfully or not. Your original upload is kept under `data/uploads/` so you can
re-transcribe it.

### Troubleshooting Step 2

| Problem | Fix |
| --- | --- |
| `FFmpeg is not available` / `TOOLING_MISSING` | `winget install Gyan.FFmpeg`, reopen the terminal, verify `ffmpeg -version`, restart the app. |
| `openai-whisper is not installed` | Activate the venv and run `pip install -r requirements.txt`. |
| Unsupported format | Re-export as one of the allowlisted extensions, or extend `ALLOWED_MEDIA_EXTENSIONS` if your FFmpeg build supports the format. |
| `This file contains no audio track` | The video is silent (e.g. a screen recording without a mic). Provide a version with audio, or an external audio file. |
| Corrupt / unreadable media | The container is damaged or uses a codec your FFmpeg build lacks. Re-encode the file and retry. |
| `confirm you're not a bot` or HTTP 403 | The platform refused an anonymous download. If you have authorized access, set `YTDLP_COOKIES_FILE` or `YTDLP_COOKIES_FROM_BROWSER` (see [Platform bot checks](#acquiring-media-from-a-url)); otherwise download the recording legitimately and upload it. |
| URL acquisition fails | The content may be private, deleted, region-locked or unsupported. Download it legitimately and upload the file instead. |
| `URL downloads to private or reserved addresses are not allowed` | That is the SSRF guard; only public destinations can be fetched. |
| Download timed out | Raise `URL_DOWNLOAD_TIMEOUT_SECONDS`, or upload the file. |
| Transcription is very slow | Expected on CPU. Use a smaller `WHISPER_MODEL` (`tiny`/`base`) or install a CUDA PyTorch build. |
| First transcription pauses a long time | Whisper is downloading the checkpoint. This happens once per model. |
| Out of memory with a large model | Use `small`/`base`, or a GPU. Shorter recordings also help. |
| Transcript saved but empty | The audio has no intelligible speech. Check the source; a transcript cannot be invented from silence. |

Full diagnostics are in **Settings → Diagnostics** and in `logs/app.log` (rotating,
secrets redacted). The pipeline never shows stack traces or internal paths in the UI.

### Current Step 2 limitations

* Transcription is **whole-file, single-shot** — no chunking. Very long recordings
  (multi-hour) need proportionally more memory; the duration limit is the guard.
* Stage-level progress only. Whisper reports no reliable per-chunk completion, so no
  percentage is fabricated.
* URL support is limited to what yt-dlp and direct downloads can reach; there is no
  guarantee for any specific site. Access is anonymous by default, so a platform can
  refuse a video (private, deleted, bot check). Only the user's *own* authorized
  cookies can be configured (`YTDLP_COOKIES_FILE` / `YTDLP_COOKIES_FROM_BROWSER`);
  the app never bypasses an access control.
* Small models mis-hear proper nouns and technical terms (`VideoMind` came back as
  *Video Monday* with `tiny`). Use `small`/`medium`, or set `WHISPER_LANGUAGE`, when
  names matter. Nothing is corrected or invented behind your back.
* One job at a time per session; concurrent jobs are intentionally not allowed yet.
* Live NVIDIA connectivity is unverified in this repository until you supply your own
  key; Step 3 reports a missing or invalid key honestly instead of failing obscurely.

### Manual acceptance checklist

Run `streamlit run app.py`, open the **Home** page, and walk down the list. No NVIDIA
key is required for any of it.

| # | Step | Expected |
| --- | --- | --- |
| 1 | Start the app, open **Home** | Two input methods shown; no work started automatically |
| 2 | Upload a supported video containing speech | Filename, size, category and format check appear |
| 3 | Press **Transcribe Video** | Stage list advances; Whisper produces a transcript |
| 4 | Inspect the result | Title, duration, language, model and full text shown |
| 5 | Check the segments | `[MM:SS – MM:SS]` (or `HH:MM:SS`) with the spoken text |
| 6 | Download TXT and JSON | Files match what is on screen |
| 7 | Repeat with an MP3/WAV | Same transcript structure; extraction shown as skipped |
| 8 | Enter a malformed URL (e.g. `not a url`) | Rejected before any download, with a clear reason |
| 9 | Enter a `file:///...` URL | Rejected as an unsupported scheme |
| 10 | Enter a supported public URL, press Transcribe | Acquired and transcribed, or a clear failure reason |
| 11 | Upload a video with no audio track | "This file contains no audio track" — app keeps working |
| 12 | Upload an unsupported extension | Rejected by the format check |
| 13 | Press the button twice quickly | Second submission is guarded; no duplicate job |
| 14 | Restart the app | Previously saved transcripts are still loadable |
| 15 | Open **Settings → Diagnostics** | FFmpeg, Whisper and URL-download status reported honestly |
| 16 | Check **Settings → NVIDIA connection** | Step 1 panel unchanged and still functional |

---

## 4. Step 3 — generate a summary from a transcript

Step 3 consumes the transcript Step 2 produced. It never touches FFmpeg, yt-dlp or
Whisper, and it works only through the transcript service. Open **Summarization** in
the sidebar; if no transcript exists yet the page explains that and links back to the
transcription workflow. **Nothing runs on page load** — a summary is generated only
when you press the button, and the Step 2 transcript is never modified, not even when
a run fails.

### Summary styles

| Style | What it gives you |
| --- | --- |
| Quick summary | The main idea and the most important conclusion, in a few lines |
| Detailed summary | Context, concepts, supporting facts and conclusions in depth |
| Bullet-point summary | A readable structure of headings and hierarchical bullets |
| Key takeaways | The highest-value insights, each with a source location |
| Topic breakdown | The major themes and concepts, with their key points |
| Meeting summary | Discussion topics, decisions, action items, owners and follow-ups |
| Chapter-style summary | A chapter breakdown with source time boundaries |
| Custom summary | Your own instruction, applied to the transcript |

Each style combines with a **detail level** (brief / standard / detailed) and with
these controls:

* **Output language** — an optional ISO code such as `en`, `de`, `hi`. Empty follows
  the transcript language; quotes always stay in the original language.
* **Include source timestamps** — turning this off means no timestamps are requested
  at all.
* **Extract action items** — turning this off means action items are never asked for.
* **Extra instruction** — a preference of up to `SUMMARY_MAX_CUSTOM_INSTRUCTION_CHARS`
  characters that steers emphasis and tone. It is sent inside the transcript block as
  data, so it can never change configuration or make the assistant invent facts. In
  **Custom summary** mode an instruction is required, and the Generate button stays
  disabled until you provide one.
* **Reuse a saved summary** — when the transcript and the settings are unchanged, the
  stored summary is shown and no new request is made. With reuse on, the primary
  button becomes **Regenerate anyway**.

Every result is validated before it is displayed, and the page states the model, the
request count, the processing time, the coverage of the source and the summary id.
A run that generated nothing shows the reason plus the real pipeline stages, and never
a fabricated summary.

### Long recordings

A transcript that does not fit one safe request is processed in bounded sections:

1. The rendered transcript is measured with a real tokenizer (`tiktoken`) when one is
   available, otherwise with a deliberately conservative estimate.
2. Segments are split on natural boundaries into chunks of at most
   `SUMMARY_CHUNK_MAX_TOKENS` tokens (minus the prompt and output reserve) with
   `SUMMARY_CHUNK_OVERLAP_TOKENS` of repeated context.
3. Each section is summarised on its own with the same style and detail level.
4. The section summaries are merged in a single request; above ten sections one extra
   merge level is inserted so the final request stays bounded. The merged result is
   validated exactly like a single-pass result, and duplicates are collapsed.
5. Every failed section is recorded: the result is labelled **partial**, the page
   warns that the recording is not fully covered, the coverage table lists the failed
   sections, and `limitations` carries the same statement for exports.

If **no** section can be summarised, the run fails with the real provider reason — it
never guesses. In the same spirit, a recording longer than
`SUMMARY_MAX_TRANSCRIPT_MINUTES` and a split that would exceed `SUMMARY_MAX_CHUNKS`
are refused with a clear message instead of producing a misleading partial result.

### Timestamps

The model is asked for timestamps from the transcript, and every one it returns is
checked against the real source:

* a time is snapped to a real segment position when it is within
  `SUMMARY_TIMESTAMP_TOLERANCE_SECONDS`, and dropped when it is not;
* an excerpt is kept only when it genuinely occurs in the transcript, so a quotation
  is never shown that the recording does not contain;
* an action-item owner or deadline is kept only when the transcript states it;
* anything corrected or removed is reported in `limitations`, which the page shows in
  **Warnings and limitations**;
* when a transcript has no usable timestamps, the prompt asks for null timestamps and
  the interface prints `timestamp unavailable` — no time is ever estimated.

### Where summaries are stored

One JSON document per summary in `data/processed/summaries/<summary_id>.json`, written
atomically and wrapped in a version envelope
(`{"schema_version": 1, "summary": { … }}`). The document holds only metadata and the
structured summary — never the transcript text, never a media path and never a
credential. The **Saved summaries** section lists what is stored for the selected
transcript; opening one makes no model request, and deleting one requires an explicit
click.

### Configuring Step 3

Every optional setting has a safe default; add only what you want to change. The
names are the upper-cased field names from `config/settings.py`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SUMMARY_MODE` | `quick` | Default summary style when nothing is chosen |
| `SUMMARY_LENGTH` | `standard` | Default detail level |
| `SUMMARY_LANGUAGE` | empty | Default output language (empty follows the transcript) |
| `SUMMARY_TEMPERATURE` | `0.2` | Sampling temperature for factual output |
| `SUMMARY_MAX_TOKENS` | `16384` | Output budget for a single summary |
| `SUMMARY_TIMEOUT_SECONDS` | `180` | Per-request timeout |
| `SUMMARY_MAX_RETRIES` | `2` | Retries for retryable provider failures |
| `SUMMARY_CHUNK_MAX_TOKENS` | `6000` | Transcript tokens per request before chunking |
| `SUMMARY_CHUNK_OVERLAP_TOKENS` | `200` | Repeated context between chunks |
| `SUMMARY_INTERMEDIATE_MAX_TOKENS` | `900` | Output budget for a section summary |
| `SUMMARY_MERGE_MAX_TOKENS` | `4096` | Output budget for the merge request |
| `SUMMARY_MAX_CHUNKS` | `40` | Largest accepted number of sections |
| `SUMMARY_MAX_TRANSCRIPT_MINUTES` | `480` | Longest recording that will be summarised |
| `SUMMARY_MAX_CUSTOM_INSTRUCTION_CHARS` | `400` | Instruction length limit |
| `SUMMARY_TIMESTAMP_TOLERANCE_SECONDS` | `3` | Snapping tolerance for model timestamps |

### What can go wrong (and what you will see)

| Situation | What the application does |
| --- | --- |
| `NVIDIA_API_KEY` missing or invalid | Explains that no summary can be generated, tells you to add the key to `.env` and restart, and leaves the transcript intact |
| Network failure or timeout | Reports the connection or timeout reason, mentions `NVIDIA_BASE_URL` / `SUMMARY_TIMEOUT_SECONDS`, and keeps the transcript |
| Rate limiting | Reports the rate limit and asks you to retry shortly |
| Answer that is not valid JSON | Re-asks once for the same content as pure JSON, then reports a validation failure |
| Budget spent before any answer (the common case with the default reasoning model) | Reports that the output budget was exhausted and names `SUMMARY_MAX_TOKENS`; it is classified as a truncation, so it is **not** retried with the same budget |
| Result that cannot be validated | Reports a validation failure instead of showing incomplete content |
| Summary saved but the disk write failed | Shows the summary and states clearly that it could not be stored |

### Measured behaviour of the default model (live endpoint)

The default `nvidia/nemotron-3.5-lightning-30b-a3b` is a reasoning model: it writes a
private reasoning trace before any answer, and that trace is what consumes the output
budget. Three real runs on 2026-10-15 against a genuine Step 2 transcript (10 segments,
33 seconds, ~147 transcript tokens) measured:

| `SUMMARY_MAX_TOKENS` | Result | Wall-clock |
| --- | --- | --- |
| 3072 | No answer at all — `finish_reason=length`, reported honestly as a truncation | ~59 s |
| 8192 | Same, still no answer | ~109 s |
| **16384 (default)** | **Validated summary produced and stored** | ~269 s |

A second live run on the same defaults in **Meeting summary** style also succeeded
(173 s, 10 705 tokens reported by the provider, `finish_reason=stop`) and correctly
returned empty action items and decisions — with an explanation — because that
transcript is not a meeting, instead of inventing them.

The default budget is therefore 16384 — the smallest budget verified to produce a real
summary, and one the provider reported using ~10.7 k tokens of, so it is not a token
limit the model simply cannot reach. Practical consequences: a single summary can take several minutes with this
model, `SUMMARY_TIMEOUT_SECONDS` bounds the socket read timeout rather than the total
generation time (the successful 16384 run took 269 s with a 180 s setting), and the
provider bills for the reasoning tokens. Lower `SUMMARY_MAX_TOKENS` only if you switch
to a model that does not reason. Long recordings are also affected: because reasoning
overhead is per request, each section costs its own budget.

### Manual acceptance checklist

The first ten rows need no NVIDIA key; the last three need a valid `NVIDIA_API_KEY`.

| # | Step | Expected |
| --- | --- | --- |
| 1 | Open **Summarization** with no transcript stored | Guidance plus a link back to the transcription workflow; no controls |
| 2 | Transcribe a short recording on **Home**, then open **Summarization** | Transcript picker, settings, and a note that it fits one request |
| 3 | Change **Summary style** and **Detail level** | The description line changes; nothing is generated |
| 4 | Choose **Custom summary** with an empty instruction | "Instruction required" and a disabled Generate button |
| 5 | Tick off **Reuse a saved summary** and press **Generate summary** | Exactly one service call; the reason is reported if the key is missing |
| 6 | Load a long recording (or lower `SUMMARY_CHUNK_MAX_TOKENS`) | The page states how many requests will be used |
| 7 | Press **Generate summary** without a key | An error notice naming `NVIDIA_API_KEY`; the transcript is untouched |
| 8 | Reopen a stored summary | The summary appears immediately with "No new model request was made." |
| 9 | Delete a stored summary | The document is removed and the notice says so |
| 10 | Open **Settings → Diagnostics** | Step 1 and Step 2 panels unchanged and still functional |
| 11 | Generate three different styles | Each result matches its style and is validated (allow a few minutes per summary with the default reasoning model) |
| 12 | Summarise a recording long enough to chunk | Coverage is reported as 100% (or a warning naming the sections that failed) |
| 13 | Compare a timestamp in the summary with the transcript | It points at the real source position, or is shown as unavailable |

### Step 3 limitations

* The model is not shown the audio, only the transcript text, so it cannot correct a
  transcription error.
* Chunked summaries are merged by the model: wording can differ from a single-pass
  summary of the same recording, and coverage is reported rather than assumed.
* Action items and decisions appear only when the transcript states them; the
  transcript is not diarised, so no statement is attributed to a named person.
* Only language models available to your account can be used; the model name comes
  from `NVIDIA_MODEL` and is never chosen by the application.
* A summary is a summary: it is a validated interpretation of the transcript, not a
  verbatim record.
* Responses are requested as a single validated JSON object, so streaming token by
  token is deliberately not used: progress is reported through the real pipeline
  stages (validate → prepare → generate/section → merge → verify → store) instead, and
  a run cannot be cancelled mid-request — it is bounded by `SUMMARY_MAX_TOKENS`,
  `SUMMARY_TIMEOUT_SECONDS` and `SUMMARY_MAX_RETRIES` instead.
* The default reasoning model needs a large output budget and can take minutes per
  summary (see the measurement table above); a lighter model will be faster if your
  account provides one.

---

## 5. Step 4 — ask your video (grounded RAG chat)

The **Ask Your Video** page answers questions from the stored transcript of one
recording. It is a real retrieval pipeline, not a prompt stuffed with the whole
transcript:

```
TranscriptRecord (Step 2)
        │  normalise: drop blank segments, copy timestamps, refuse unsafe ids
        ▼
Timestamp-aware chunks (~1200 characters, sentence boundaries, 200 overlap)
        ▼
Local embeddings (sentence-transformers/all-MiniLM-L6-v2)
        ▼
ChromaDB collection for this video id  +  chroma_data/index_registry.json
        ▼
Question (+ recent turns when it is a follow-up) → top-k passages
        ▼
Grounded prompt → NVIDIA Nemotron → JSON answer
        ▼
Citations checked in Python → answer + sources + timestamps
```

### Why it is split this way

* `src/rag/` never imports Streamlit, and `src/ui/chat_page.py` never touches
  embeddings or ChromaDB. The service (`RagService`) is the only entry point, so the
  same calls can be reused by tests and by Step 5.
* The transcript is **not modified**. Indexing is additive: it writes vectors to
  `chroma_data/` and leaves the JSON transcript and the summary untouched, so the
  Step 2/3 pages keep working exactly as before.
* The embedding model is local. Indexing sends nothing to NVIDIA; only a question you
  actually ask is sent, together with the retrieved passages.

### The index lifecycle

The page always shows the real state of the index:

| Status | Meaning | What the page offers |
| --- | --- | --- |
| **Not indexed** | No collection exists for this `video_id` yet. | **Build index** |
| **Ready** | The collection matches the current transcript and embedding model. | Rebuild, Delete, chat |
| **Out of date (stale)** | The transcript changed after the index was built (its content fingerprint no longer matches). | **Rebuild index** only |
| **Embedding model changed** | The index was built with a different `EMBEDDING_MODEL_NAME`. | **Rebuild index** only |
| **Vector store unavailable** | ChromaDB could not be opened (permissions, disk, corruption). | Nothing; the error is shown |

* Building twice is safe: an unchanged index is **reused**, not re-embedded.
* A rebuild writes a fresh collection and only switches the registry entry after the
  write succeeds, so a failed run never destroys a working index.
* **Delete index** asks for confirmation, removes only this video's vectors and chunk
  metadata, and clears that video's conversation. Transcripts and summaries stay.
* A stale index refuses questions instead of silently answering from old text.

### What an answer contains

Every answer is generated from the passages shown underneath it:

* **Sources** — each cited passage as `[S1] title · 00:10 - 00:25`, with the quote the
  model used. `timestamp unavailable` is shown honestly when the transcript has no
  timeline; a summary chunk never claims a position in the recording.
* **Retrieved passages** — every passage sent to the model (cited or not), with its
  excerpt, so you can judge what the answer was based on.
* **Notes** — anything the pipeline removed or could not verify.

Validation happens in Python before anything is displayed: a citation whose `S#` was
never retrieved is dropped, a quote that does not appear in its passage is removed,
and an answer with no resolvable citation is reported as a failure instead of being
shown as if it were grounded. If the model says the transcript does not contain the
answer, the page says so and does not invent one.

### Follow-up questions, history and video isolation

* The conversation is stored **per `video_id`** in session state. Switching videos
  shows that video's conversation only; clearing affects one video only.
* A follow-up such as *“Why?”* is retrieved with the previous question appended, so it
  keeps its subject. Retrieval is still restricted to the selected video's collection.
* At most `RAG_MAX_HISTORY_TURNS` previous turns are sent to the model.
* Questions are capped at `RAG_MAX_QUESTION_CHARS` characters.

### First run: the embedding model download

The first index build downloads `all-MiniLM-L6-v2` (~90 MB) into the Hugging Face cache
and loads it once per process. Later builds reuse it. Only **Build/Rebuild index**
needs that model; asking a question needs the model plus a configured `NVIDIA_API_KEY`.

### Where the vectors live

`chroma_data/` (git-ignored) holds one ChromaDB collection per video plus
`index_registry.json`, which records the transcript fingerprint, the embedding model,
the chunk counts and the build time. Delete that folder to reset every index; the
application simply reports *Not indexed* again. Set `CHROMA_PERSIST_DIRECTORY` to keep
vectors somewhere else.

### Configuring Step 4

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMBEDDING_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model |
| `EMBEDDING_DEVICE` | `auto` | `auto`, `cpu` or `cuda` |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks embedded per batch |
| `RETRIEVAL_CHUNK_SIZE` | `1200` | Target chunk size in characters |
| `RETRIEVAL_CHUNK_OVERLAP` | `200` | Repeated characters between chunks |
| `RETRIEVAL_TOP_K` | `4` | Passages retrieved per question |
| `RETRIEVAL_MAX_CONTEXT_CHARS` | `6000` | Upper bound on retrieved text |
| `RETRIEVAL_INCLUDE_SUMMARY` | `true` | Also index the stored summary as context |
| `RAG_ANSWER_MAX_TOKENS` | `8192` | Answer budget (shared with private reasoning) |
| `RAG_TEMPERATURE` | `0.1` | Answer sampling temperature |
| `RAG_MAX_HISTORY_TURNS` | `6` | Previous turns sent with a follow-up |
| `RAG_MAX_QUESTION_CHARS` | `1000` | Question length limit |
| `CHROMA_PERSIST_DIRECTORY` | `chroma_data/` | Vector database location |

Sizing and consistency rules:

* `RETRIEVAL_CHUNK_OVERLAP` must stay below `RETRIEVAL_CHUNK_SIZE`; an invalid pair is
  corrected to one quarter of the size and reported in Settings → Diagnostics.
* `RETRIEVAL_MIN_CHUNK_SIZE` controls the smallest trailing chunk that is kept;
  smaller remainders are folded into the previous chunk instead of becoming a stub.
* `RAG_ANSWER_MAX_TOKENS` is shared with private reasoning on the default model, so it
  must stay generous for the same reason `SUMMARY_MAX_TOKENS` must.

### Using the chatbot

1. Process a recording on the **Home** page (upload or URL) and let transcription and
   the summary finish.
2. Open **Ask Your Video** in the sidebar.
3. Pick the recording in the **Video** selector (newest first).
4. Press **Build index** and wait for *Index ready*.
5. Type a question (or press one of the suggested questions) in the chat box.
6. Read the answer, then **Sources** and **Retrieved passages** to check it.
7. Ask a follow-up; the conversation stays with this video.
8. Use **Clear conversation** to reset this video only, or **Delete index** to remove
   its vectors.

### Manual acceptance checklist

| # | Action | Expected result |
| --- | --- | --- |
| 1 | Open **Ask Your Video** with no transcripts | A clear "no transcript is available yet" message and a link to the Home page |
| 2 | Select a recording and build the index | Progress stages appear, then **Index ready** with the chunk count and model |
| 3 | Press **Build index** again | *Existing index reused* — no duplicate work |
| 4 | Ask a question the recording covers | An answer with **Sources** showing a real `mm:ss` range and an excerpt |
| 5 | Ask a follow-up such as *"Why?"* | The answer keeps the subject of the previous question |
| 6 | Ask about something the recording never mentions | A clear "no passage was close enough" message; no invented answer |
| 7 | Remove `NVIDIA_API_KEY` and ask | Questions are refused with the credential message; the index still works |
| 8 | Switch to another recording | A different conversation; no messages leak between videos |
| 9 | Clear the conversation | Only that video's messages disappear |
| 10 | Delete the index | Confirmation first, then *Index deleted*; transcripts and summaries remain |
| 11 | Re-transcribe the same source (content changes) | Status becomes **Out of date** and questions are refused until you rebuild |

### Step 4 limitations

* Retrieval is lexical-semantic, not a knowledge graph: a question whose wording shares
  nothing with the transcript may return **no evidence**. Re-ask with words from the
  recording, or lower `RETRIEVAL_MIN_CHUNK_SIZE` / raise `RETRIEVAL_TOP_K` carefully.
* The default embedding model is English-centric. Hindi and other non-English
  transcripts index and answer correctly only as far as that model represents them;
  set `EMBEDDING_MODEL_NAME` to a multilingual model for better recall.
* One video per conversation. Cross-video questions ("compare this lecture with last
  week's") are not supported and are not faked.
* The default reasoning model can take a while per answer; the page shows retrieval
  and generation stages instead of appearing frozen.
* ChromaDB is used in local persistent mode: one application process owns the
  directory, so do not point two running instances at the same `chroma_data/`.
* Answers are not streamed, and speaker labels are not available unless the
  transcript carries them (Whisper does not diarise).

### Troubleshooting Step 4

| Problem | Fix |
| --- | --- |
| **Build index** fails with an embedding error | The model could not be downloaded or loaded. Check the internet connection for the first run, or pre-download the model; `EMBEDDING_DEVICE=cpu` avoids a broken CUDA setup. |
| Indexing is slow | Reduce `EMBEDDING_BATCH_SIZE`, or use a smaller model via `EMBEDDING_MODEL_NAME`. |
| The page reports **out of date** | The transcript changed. Press **Rebuild index**. |
| The page reports **Embedding model changed** | `EMBEDDING_MODEL_NAME` was changed. Rebuild, or restore the previous value. |
| **Vector store unavailable** | `chroma_data/` is not writable or is corrupt. Fix permissions, or delete the folder to reset every index. |
| Question returns **no passage was close enough** | Rephrase using words from the recording; the pipeline will not answer without evidence. |
| Answer is slow | Normal for the default reasoning model. `RAG_TIMEOUT_SECONDS` bounds each read, not the total generation. |
| Answer says the transcript does not contain it | Retrieval found passages but the model judged them insufficient. That is reported rather than invented. |
| `No NVIDIA API key is configured` while asking | Add `NVIDIA_API_KEY` to `.env` and restart. The index is unaffected. |

---

## 6. Step 5 — generate and export reports

The **Reports** page turns the data you already produced into a downloadable
document. It reads the stored transcript, the newest stored summary and this
session's chatbot turns; it never transcribes, summarises or calls NVIDIA again.

```
Stored transcript (Step 2) ─┐
Stored summary (Step 3)     ├─► ReportBuilder ─► VideoMindReport ─► renderer ─► bytes ─► download
Chat turns in session (4)  ─┘   (validate,          (one shared      (one file per
                                 select, omit)       model)           format)
```

The report layer is deliberately split, exactly like the earlier steps:

* `src/reports/report_models.py` — the validated `VideoMindReport` contract, the
  section enum and the option model. Every format renders this one object, so PDF
  and JSON contain the same facts.
* `src/reports/report_builder.py` — validates stored data, applies the selection,
  records why an unavailable section was omitted and never invents content.
* `src/reports/*_exporter.py` — one renderer per format. Renderers receive a report
  and return bytes/text; they never read Streamlit, the filesystem or a provider.
* `src/ui/reports_page.py` — the Streamlit page: video picker, content controls,
  preview and download. No document layout lives here.

### Implemented formats

Every entry below is produced by a real renderer and covered by tests.

| Format | Best for | What the file contains |
| --- | --- | --- |
| **PDF** (`.pdf`) | Printing and sharing | Paginated A4 report, cover metadata, styled headings, tables, page numbers and a footer. Uses ReportLab Platypus, so paragraphs wrap and long reports page-break correctly. |
| **Word** (`.docx`) | Editing | Real Office Open XML with heading styles, bullet lists, metadata table, header/footer and page-number field. |
| **Plain text** (`.txt`) | Quick reading, copy/paste | Title, metadata and sections with simple headings and `[mm:ss - mm:ss]` transcript labels. No markup artifacts. |
| **Markdown** (`.md`) | GitHub, Obsidian, notes | Semantic headings, lists, tables where useful, escaped content and a YAML-free metadata block. |
| **JSON** (`.json`) | Automation and archival | Documented schema (`schema_version: "1.0"`), stable field names, UTF-8, the selected sections only, no secrets or runtime objects. |
| **HTML** (`.html`) | Offline viewing | One self-contained file with embedded CSS, responsive layout and print styles. All user content is escaped; no remote scripts or assets. |
| **CSV — transcript** | Spreadsheets, data analysis | One row per segment: ids, numeric and formatted start/end times and text (no speaker column: the transcript model has no diarisation). |
| **CSV — chat Q&A** | Reviewing questions | One row per question/answer with timestamps and citation ids. |
| **CSV — sources** | Auditing evidence | One row per retrieved source passage, labelled as transcript or summary evidence. |
| **Excel** (`.xlsx`) | Structured review | A real workbook with only the worksheets your selection supports (`Overview`, `Transcript`, `Key Takeaways`, `Chat Q&A`, `Sources`), headers, column widths, frozen headers and wrapped text. |
| **PowerPoint** (`.pptx`) | Short presentation | Title slide, metadata, summary, depth-limited takeaways, topics, Q&A and a sources slide. Long text is split across slides instead of overflowing. |

CSV is intentionally three exports: a transcript, a Q&A log and a citation list have
different columns and mixing them into one file would produce meaningless rows.

### Opening the page and choosing content

1. Process a recording on **Home** (or reuse an existing transcript).
2. Open **Reports** in the sidebar.
3. Pick the recording in **Video** (newest first). The page shows the real duration,
   language, segment count, summary style and number of chat answers.
4. Tick the sections you want under **Choose content**. Only sections with real data
   are enabled; an unavailable one is greyed out with the reason underneath (for
   example *No chatbot questions were asked about this video in this session*).
5. Adjust **Report title**, and optionally **Prepared by**, **Organisation or course**
   and **Notes**.
6. Leave **Show transcript timestamps** on unless you need a clean reading text.
7. Choose a **Format**, review the **Preview**, then press **Generate <format>**.
8. Press **Download <format>** to save the file. Choose another format and press
   Generate again to export the same content again — nothing is reprocessed.

Default selection: video information, AI summary, key takeaways, topic breakdown and
processing information. The transcript, chapters, action items, chat Q&A and sources
are opt-in, so a two-hour recording does not silently produce a 300-page default PDF.

Only formats that can represent the current selection are offered: the three CSV views
require their section, and nothing is listed that is not implemented.

### Previewing before download

The preview shows the summary excerpt, counts for takeaways/topics/chat answers, the
transcript size, and the first few transcript segments or questions in collapsible
panels. The full transcript is never rendered into the Streamlit page just to preview
a report.

### Full transcript, large recordings and limits

* Selecting **Transcript** includes **every segment**, in order, with original text
  and timestamps. Nothing is truncated silently.
* A report above `REPORT_MAX_CHARACTERS` (default 5,000,000 characters) is refused
  before rendering, with an explicit message. Lower the limit if you want a guard rail;
  raise it if you genuinely need a larger file.
* Files are rendered in memory and handed to Streamlit's download component; no
  permanent copy is written. Generated exports are not stored in `reports/` unless a
  future report-history feature is added.
* Rendering a very large PDF/DOCX takes a few seconds; the page shows a spinner and
  keeps the result until your selections change.

### Timestamps, citations and honesty rules

* Timestamps come from the transcript and are formatted by the shared
  `src/utils/time_utils.py` helper (`mm:ss`, or `hh:mm:ss` when needed). A segment
  without a time is shown as *timestamp unavailable* — never as `00:00`.
* Chat Q&A keeps the exact question and the exact stored answer. Each citation keeps
  its source id, title, excerpt and timestamp range, and is labelled
  **transcript** or **summary** so a summary-derived claim is never presented as a
  quotation.
* If an answer stored no citations, the report says so instead of inventing them.
* A selected section with no data is omitted and named in the report's *not included*
  section, and the page shows a warning. No placeholder prose is generated.
* A report is always built from the video selected in the **Video** box; the id is part
  of the cache fingerprint, so switching videos can never reuse another video's file.

### File names

The filename is derived from the report title and the format, for example
`Machine_learning_basics_report_20261024_2039.pdf` (or `_transcript.csv`,
`_chat.csv`, `_sources.csv`, `_slides.pptx`). Unsafe characters, path separators and
directory traversal are stripped, whitespace is normalised, the length is bounded and a
minute-level timestamp keeps repeated downloads distinct. The chosen title is treated
as text, never as a path.

### Unicode and multilingual reports

English, Hindi and mixed-language text is preserved in every format. PDF fonts are
chosen in this order:

1. `REPORT_PDF_FONT_PATH`, when set;
2. a system Unicode font (Nirmala UI on Windows, Noto/DejaVu on Linux, Arial Unicode on
   macOS);
3. built-in Helvetica, which cannot draw Devanagari — the export then returns a visible
   warning telling you to configure a font, instead of silently printing `?`.

DOCX, XLSX, PPTX, HTML, JSON, Markdown, TXT and CSV keep Unicode natively. No font
files are redistributed with the project.

### Safety

* HTML is escaped (including transcript text that looks like markup or script) and
  links are restricted to safe schemes.
* CSV prefixes dangerous leading characters (`=`, `+`, `-`, `@`, tab, CR) with an
  apostrophe so a spreadsheet does not execute transcript text as a formula; the
  original report data is unchanged.
* XLSX cells are written as explicit text rather than formulas.
* API keys, environment values, stack traces and internal paths never enter a report.
* A source URL is included only when it is a URL source and the metadata section is
  selected; credentials, fragments and tracking/signed query parameters are stripped
  first (the report notes when the URL was shortened).

### Configuring Step 5

| Variable | Default | Purpose |
| --- | --- | --- |
| `REPORT_PDF_FONT_PATH` | auto-detect | Optional TrueType font for PDF |
| `REPORT_PDF_FONT_SIZE` | `10` | PDF body size in points |
| `REPORT_MAX_CHARACTERS` | `5000000` | Refuse, rather than truncate, a larger report |
| `REPORT_MAX_NOTE_CHARS` | `2000` | Limit for notes (author/organisation are 200) |

Install the document libraries with the rest of the project:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
```

The four Step 5 packages (`reportlab`, `python-docx`, `openpyxl`, `python-pptx`) are
already listed there. `pypdf` is test-only. No new model download and no NVIDIA key is
needed: a report can be exported on a machine with no provider configured at all.

### Manual acceptance checklist

| # | Action | Expected result |
| --- | --- | --- |
| 1 | Open **Reports** with no transcript | A clear "no transcript is available yet" message |
| 2 | Select a processed recording | Real metadata, summary and counts appear |
| 3 | Generate with the defaults | A PDF downloads; its text matches the preview |
| 4 | Tick **Transcript** | Every segment is in TXT/PDF, with real timestamps |
| 5 | Ask a question, then tick **Chatbot questions and answers** | The exact question, answer and citations are exported |
| 6 | Generate PDF, then JSON | The same sections and text are present in both |
| 7 | Change the title or a section | The old download disappears until you generate again |
| 8 | Switch to another recording | The title and data change; the previous file is not offered |
| 9 | Export with no NVIDIA key | Everything still works |
| 10 | Set `REPORT_MAX_CHARACTERS=1000` and export a long transcript | A clear "above the configured limit" message, no crash, no truncated file |

### Troubleshooting Step 5

| Problem | Fix |
| --- | --- |
| Download button is missing | Nothing has been generated for the current selections yet. Press **Generate**. |
| PDF shows `?` or blank boxes for Hindi text | No Unicode font was found. Set `REPORT_PDF_FONT_PATH` to a Devanagari font (for example `C:\Windows\Fonts\Nirmala.ttc`) and generate again. |
| "above the configured limit" | The report is larger than `REPORT_MAX_CHARACTERS`. Raise it, or export without the full transcript. |
| A section is greyed out | Its data does not exist for this video (no summary, no chat, no chapter breakdown). Generate it on the earlier page first. |
| CSV opens with an extra apostrophe | Expected: dangerous leading characters are escaped so spreadsheets do not execute them. |
| Export failed unexpectedly | Check `logs/app.log`; the page hides technical details but logs them. Stored data is untouched. |

### Step 5 limitations

* Exporting never creates a new summary. If you want a different summary style in the
  report, generate it on the **Summarise** page first (the newest stored summary is
  used).
* No report history: exports are downloaded, not retained. Regenerating is cheap
  because no AI call is involved.
* PPTX is deliberately concise; the full transcript belongs in PDF/DOCX/TXT/CSV.
* PDF text extraction of Devanagari is font-dependent; rendering may also lack complex
  conjunct shaping in some font/reportlab combinations. The warning system reports
  the fallback instead of claiming success.
* Reports are single-video documents; combining several recordings is not supported.

---

## 7. Project layout and responsibilities

```
video-mind-ai/
├─ app.py                        Streamlit entry point (page config, bootstrap, routing)
├─ .streamlit/config.toml        Streamlit server settings (upload limit mirrors settings.py)
├─ requirements.txt              Pinned Step 1-5 dependencies
├─ requirements-later-stages.txt Record of the dependency decisions per stage
├─ .env.example                  Placeholder environment template (safe to commit)
├─ .gitignore                    Excludes secrets, media, logs and generated reports
├─ pytest.ini                    Test configuration (project root on sys.path)
├─ README.md                     This document
├─ config/
│  └─ settings.py                Central validated settings (single source of truth)
├─ src/
│  ├─ models/schemas.py          Typed models: source, media, transcript, pipeline, summary, chat, health
│  ├─ models/summary_schemas.py  Step 3 summary contract (options, structured summary, metadata, result)
│  ├─ models/rag_schemas.py      Step 4 contract (chunks, retrieved sources, citations, answer, index state)
│  ├─ services/
│  │  ├─ nvidia_client.py        Cached OpenAI-compatible client + error hierarchy
│  │  ├─ llm_service.py          Generation, streaming and explicit connection test
│  │  ├─ health_service.py       Local diagnostics (no network, no model downloads)
│  │  ├─ source_service.py       Upload streaming, size/extension validation, safe filenames
│  │  ├─ media_service.py        FFmpeg/FFprobe detection, media probing, audio extraction
│  │  ├─ url_ingestion_service.py URL validation, SSRF guards, yt-dlp / direct downloads
│  │  ├─ transcription_service.py Lazy local Whisper loading and transcription
│  │  ├─ transcript_storage.py   Safe JSON persistence of transcript documents
│  │  ├─ transcript_service.py   Build/load/list transcripts + TXT/JSON exports (Step 3 boundary)
│  │  ├─ transcript_preprocessor.py Render a transcript and bound its size (chunking + token estimate)
│  │  ├─ timestamp_service.py    Validate model timestamps against the real transcript
│  │  ├─ summary_generation.py   Bounded, validated JSON requests to the configured model
│  │  ├─ chunk_summarizer.py     Summarise one transcript section into a chunk summary
│  │  ├─ summary_merger.py       Merge section summaries into the final summary JSON
│  │  ├─ summary_validation.py   Rebuild model JSON into validated typed objects
│  │  ├─ summarization_service.py The Step 3 public interface (summarize_transcript)
│  │  ├─ summary_storage.py      Atomic JSON persistence of validated summaries
│  │  └─ video_pipeline.py       Orchestration and duplicate-job guarding
│  ├─ reports/
│  │  ├─ report_models.py       Shared VideoMindReport model, sections, options, validation
│  │  ├─ report_builder.py      Builds a report from stored data and records omissions
│  │  ├─ export_types.py        ExportFormat / ReportExport value objects
│  │  ├─ export_formats.py      The single tested list of formats the UI may offer
│  │  ├─ filename_utils.py      Safe, bounded, unique download filenames
│  │  ├─ text_exporter.py       TXT
│  │  ├─ markdown_exporter.py   Markdown
│  │  ├─ json_exporter.py       Versioned JSON schema
│  │  ├─ html_exporter.py       Self-contained, escaped, print-friendly HTML
│  │  ├─ pdf_exporter.py        Paginated ReportLab document with Unicode font choice
│  │  ├─ docx_exporter.py       Editable Word document with real styles
│  │  ├─ csv_exporter.py        Transcript / chat / sources CSV with injection guards
│  │  ├─ xlsx_exporter.py       Workbook with one worksheet per content type
│  │  └─ pptx_exporter.py       Concise presentation with split slides
│  ├─ rag/
│  │  ├─ transcript_adapter.py   Normalise a Step 2 transcript (timestamps kept, blanks dropped, fingerprint)
│  │  ├─ chunking.py            Timestamp-aware transcript/summary chunking (deterministic chunk ids)
│  │  ├─ embeddings.py          Local sentence-transformers embedder (lazy, cached, batched)
│  │  ├─ vector_store.py        Persistent ChromaDB collections per video + index registry/staleness
│  │  ├─ retriever.py           Top-k retrieval, follow-up query widening, context budget
│  │  ├─ prompts.py             Grounded answer prompt and JSON contract
│  │  ├─ answer_generator.py    LangChain runnable + Python-side citation/quote validation
│  │  └─ service.py             RagService: index_status / index_transcript / ask / delete_index
│  ├─ prompts/summary_prompts.py Step 3 prompt construction and JSON contract
│  ├─ ui/
│  │  ├─ styles.py               Visual identity and custom CSS
│  │  ├─ components.py           Reusable presentational building blocks
│  │  ├─ transcription_view.py   Upload / URL workflow, progress and transcript display
│  │  ├─ summarization_page.py   Step 3 workflow, controls, result display and saved summaries
│  │  ├─ chat_page.py            Step 4 chatbot: video picker, index controls, conversation, citations
│  │  ├─ reports_page.py         Step 5 report controls, preview, generation and download
│  │  └─ layout.py               Sidebar navigation, routing and page renderers
│  └─ utils/
│     ├─ logging_config.py       Console + rotating file logging with redaction
│     ├─ file_utils.py           Size/extension validation, safe filenames
│     └─ time_utils.py           Timestamp formatting and unavailable-timestamp rules
├─ scripts/
│  └─ verify_step2_media.py      Real-media integration verifier (FFmpeg + Whisper, not part of pytest)
├─ tests/                        Automated tests (provider, network and Whisper are mocked)
├─ data/uploads/, data/processed Working directories (created at runtime, git-ignored)
├─ data/temp/                    Per-job scratch space (audio, downloads; removed per job)
├─ chroma_data/                  Step 4 vector database + index_registry.json (git-ignored)
├─ reports/                      Reserved for saved reports; downloads are rendered in memory
└─ logs/                         Application logs (created at runtime, git-ignored)
```

Design rules kept in mind for the next steps:

* The interface never talks to a provider directly — it calls services.
* The interface never runs FFmpeg or Whisper itself: it hands a validated source
  to the pipeline and renders the structured result.
* Secrets never travel through session state, widgets or log lines.
* One setting per concern, defined once in `config/settings.py`.
* No heavy model loading at import time, so start-up stays fast; the Whisper model
  and the embedding model are loaded lazily on first use and reused afterwards.

---

## 8. Development roadmap

| Step | Scope | Status |
| --- | --- | --- |
| 1 | Foundation, secure configuration, NVIDIA Nemotron client, health checks, tests | **Implemented** |
| 2 | Local uploads, supported-URL ingestion, FFmpeg audio extraction, Whisper transcription, transcript storage | **Implemented** |
| 3 | NVIDIA Nemotron summaries, eight summary styles, long-transcript chunking and merging, validated timestamps, structured summary storage | **Implemented** |
| 4 | Timestamp-aware chunking, local embeddings, persistent ChromaDB index per video, LangChain retrieval, citation-checked answers, RAG chat | **Implemented** |
| 5 | Shared report model, selectable content, PDF/DOCX/TXT/Markdown/JSON/HTML/CSV/XLSX/PPTX exports, end-to-end integration | **Implemented** |

The maintained tracking documents live in `docs/`:

* [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — the feature checklist, the
test inventory and the live/mocked verification matrix.
* [`docs/DEVELOPMENT_ISSUES.md`](docs/DEVELOPMENT_ISSUES.md) — every issue found
during development, with the real error text, reproduction steps, whether it blocked
downstream work, the fix and its regression test.
* [`docs/FINAL_REPORT.md`](docs/FINAL_REPORT.md) — the 2026-10-25 integration pass:
automated results, live checks, confirmed fixes, remaining issues and exact commands.

---

## 9. Running the tests

```powershell
.venv\Scripts\activate
python -m pytest
```

* Provider calls are mocked: **no API key and no internet connection are required**.
* Network downloads, FFmpeg invocations and Whisper transcription are all mocked in
  the unit tests, so the suite runs offline and never loads a model.
* Real media is verified separately by `scripts/verify_step2_media.py`: it builds a
  speech fixture (Windows speech synthesis), a video containing that speech and a
  video with no audio track, then runs the **real** pipeline — FFprobe, FFmpeg and
  Whisper on the CPU — and checks the transcript, the JSON round-trip and cleanup.

  ```powershell
  .venv\Scripts\python.exe scripts\verify_step2_media.py
  ```

  It prints one line per check and exits non-zero if anything fails. It is not part of
  `pytest` because it needs FFmpeg and downloads the Whisper checkpoint on first use.
* The interface is exercised with Streamlit's `AppTest`, including the case where no
  credentials exist and the Step 2 workflow end to end.
* Step 3 is covered the same way: prompt construction and the JSON contract, the
  transcript preprocessor (rendering, token budgets, chunk boundaries and overlap),
  output validation (timestamps snapped or dropped, unverifiable excerpts and
  unstated owners/deadlines removed, duplicates collapsed), the summarisation service
  (single-pass and chunked runs, merge behaviour, partial-coverage reporting, every
  failure state, credential handling, storage round-trips) and the summarisation page
  (controls, no-work-on-load, one service call per click, saved-summary reuse and
  deletion) through `AppTest`.
* Step 4 is covered without a download or a network call: a deterministic test
  embedder exercises the **real** ChromaDB store, chunker and retriever, while a stub
  generator stands in for NVIDIA. The tests pin transcript normalisation (timestamps
  kept, blanks dropped, unsafe ids refused, fingerprints stable), chunking, index
  persistence across processes, staleness and embedding-model mismatch, cross-video
  isolation, citation/quote validation, every failure outcome of `RagService`, and the
  chatbot page (no work on load, one service call per action, citations rendered with
  real timestamps, retry, per-video history, clear and delete).
* Step 5 is covered format by format, with no key and no model: the report model
  (required fields, selection, optional data, Unicode, citation and timestamp
  preservation), the builder (availability, omission reasons, active-video scoping,
  character limits), and every renderer. The generated files are reopened rather than
  trusted by extension: PDFs with `pypdf`, DOCX with `python-docx`, XLSX with
  `openpyxl`, PPTX with `python-pptx`. Tests also pin HTML escaping, CSV quoting and
  formula-injection protection, long transcripts, missing timestamps, Hindi/English
  text, empty summaries and citation provenance. `AppTest` exercises the Reports page:
  unavailable sections, generation and download bytes, stale-byte invalidation when the
  title/sections/video change, an oversized report and exporting with no provider.
* The NVIDIA client and model are always mocked, so no test needs a key, a network
  connection or a paid request. A genuine end-to-end run against the live endpoint
  needs your `NVIDIA_API_KEY` and is a manual step (see the checklist in
  [section 4](#4-step-3--generate-a-summary-from-a-transcript) and
  [section 5](#5-step-4--ask-your-video-grounded-rag-chat)).
* Coverage: settings defaults and validation, client construction, generation parsing,
  error translation, navigation state, health checks, file validation, upload
  validation and naming, FFprobe/FFmpeg behaviour, URL/SSRF rules, transcript models
  and JSON storage, the pipeline's stage/outcome mapping, the transcription UI, the
  summary and chat pipelines, and every report exporter and the Reports page.

---

## 10. Troubleshooting

| Problem | Fix |
| --- | --- |
| `NVIDIA credentials are not configured` | Copy `.env.example` to `.env`, set `NVIDIA_API_KEY`, restart the app. |
| Summarisation says **no summary can be generated** | `NVIDIA_API_KEY` is missing or invalid. Add it to `.env` and restart; the transcript is unaffected. |
| A summary run reports the **output budget was exhausted** | The default model reasons before answering and spent the whole budget. Raise `SUMMARY_MAX_TOKENS` in `.env` (the default 16384 is the smallest budget measured to work with `nvidia/nemotron-3.5-lightning-30b-a3b`). |
| A summary takes minutes | Normal for the default reasoning model (a 33-second transcript took ~269 s). It is not stuck; the stage list keeps updating. |
| A summary run exceeds `SUMMARY_TIMEOUT_SECONDS` | That setting bounds the socket read timeout, not the total generation. Raise it if you see real read timeouts. |
| A summary run reports **rate limiting** | Wait a moment and retry; keep `SUMMARY_MAX_RETRIES` at 2 or higher. |
| A summary run reports the **endpoint could not be reached** | Check your connection and `NVIDIA_BASE_URL`; raise `SUMMARY_TIMEOUT_SECONDS` for slow links. |
| The summary is labelled **incomplete / partial** | Some sections failed. Retry, or raise `SUMMARY_TIMEOUT_SECONDS` / lower `SUMMARY_CHUNK_MAX_TOKENS` so each request is smaller. |
| A long recording is **refused as too long** | Raise `SUMMARY_MAX_TRANSCRIPT_MINUTES` (default 480) or split the source. |
| **No source timestamps** appear | The transcript has no usable timestamps, or **Include source timestamps** is off. Nothing is estimated on purpose. |
| An owner or deadline disappeared from an action item | The transcript does not state it, so it was removed and reported in **Warnings and limitations**. |
| **Reuse a saved summary** hides the Generate button | That is intentional: an identical run is already stored. Press **Regenerate anyway**, or untick the checkbox. |
| A summary is shown but was not saved | The disk write failed (permissions or space). Fix the `data/` directory and generate again. |
| Connection test fails with an authentication error | The key is invalid or revoked. Create a new key and update `.env`. |
| Connection test reports an **empty answer**, or a run reports that the **token budget was used up** | The configured model is a reasoning model: it spends part of `LLM_MAX_TOKENS` on private reasoning before writing any answer. Raise `LLM_MAX_TOKENS` in `.env` (2048 is a comfortable start). The connection itself is fine. |
| `The configured model is not available at this endpoint` | Set `NVIDIA_MODEL` in `.env` to a model available in your account. |
| Request timed out | Check your connection or raise `NVIDIA_TIMEOUT_SECONDS`. |
| `Missing packages: ...` in the diagnostics panel | Activate the virtual environment and run `pip install -r requirements.txt`. |
| `streamlit: command not found` | Activate the virtual environment first, or run `python -m streamlit run app.py`. |
| Port 8501 already in use | Run `streamlit run app.py --server.port 8502`. |
| Invalid environment value (e.g. `MAX_UPLOAD_SIZE_MB=abc`) | The app still starts with safe defaults and reports the problem in Settings → Diagnostics. Fix the value in `.env` and restart. |
| FFmpeg reported as missing | `winget install Gyan.FFmpeg`, then reopen the terminal. Needed from Step 2 onwards. |
| Uploads larger than 500 MB are refused by Streamlit | Raise `maxUploadSize` in `.streamlit/config.toml` **and** `MAX_UPLOAD_SIZE_MB` in `.env` together, then restart. |
| The Reports page says **no transcript is available yet** | Process a recording on **Home** first; reports read stored transcripts, they do not create them. |
| A report section is greyed out | That data does not exist for this video yet (summary, chapters, chat turns). Generate it on the earlier page. |
| PDF shows `?` for Hindi text | Configure `REPORT_PDF_FONT_PATH` (for example `C:\Windows\Fonts\Nirmala.ttc`) and generate again. |
| **Above the configured limit** when exporting | The report exceeds `REPORT_MAX_CHARACTERS`; raise it or leave the full transcript out. Nothing was truncated. |
| The download button disappeared | You changed the video, title, sections or format. Press **Generate** again; stale bytes are always dropped. |

Transcription/FFmpeg/URL-specific problems are covered in
[section 3](#3-step-2--ingest-and-transcribe-a-video-or-audio-file). Diagnostics live
in **Settings → Diagnostics** and in `logs/app.log` (rotating, secrets redacted).

---

## 11. Step 2 transcript contract (consumed by Steps 3, 4 and 5)

Step 3 (NVIDIA-powered summarisation) and Step 4 (RAG chat) consume Step 2 output
through a single service boundary. Neither touches FFmpeg, yt-dlp or Whisper.

### Entry point

```python
from src.services.video_pipeline import VideoPipeline
from src.models.schemas import TranscriptionOptions

pipeline = VideoPipeline()                 # uses config.settings.get_settings()
result = pipeline.run(                     # -> PipelineResult
    validated_source,                      # ValidatedSource from source_service/url_ingestion
    options=TranscriptionOptions(language=None),   # None = auto-detect
    progress=my_callback,                  # optional Callable[[str], None]
    store=True,                            # persist the transcript JSON
)
```

`PipelineResult` carries:

* `outcome` — a `PipelineOutcome`. **Step 3 must branch on this**, never assume
  success: `success`, `invalid_input`, `download_failed`, `media_inspection_failed`,
  `audio_extraction_failed`, `transcription_failed`, `transcript_validation_failed`,
  `storage_failed`, `tooling_missing`.
* `message` — a user-safe sentence, already stripped of internals.
* `stages` — one `PipelineStageReport` per stage with `completed` / `failed` /
  `skipped` and a short detail string.
* `transcript` — a `TranscriptRecord` on success, otherwise `None` (§13 contract).

### Reading transcripts back

```python
from src.services.transcript_service import TranscriptService

service = TranscriptService()
service.list_transcripts()                 # -> list[TranscriptMetadata], newest first
record = service.load(transcript_id)       # -> TranscriptRecord
record.full_text                           # plain text for the prompt
record.segments                            # ordered [TranscriptSegment]
service.to_txt(record) / service.to_json(record)   # export helpers
service.delete(transcript_id)
```

`TranscriptService` is the only persistence boundary; its `TranscriptStorage`
backend is JSON today and can be replaced later without changing callers.

### Transcript schema (synthetic example)

The record below is what `TranscriptService.load()` returns. On disk it is wrapped in
a small envelope so the format can evolve safely:
`{"schema_version": 1, "transcript": { … }}` (`SCHEMA_VERSION` in
`src/services/transcript_storage.py`).

```json
{
  "transcript_id": "tr-3f9c1a7b4e2d",
  "video_id": "vid-8c1e5b2a90f47d63",
  "source_type": "local_video",
  "original_filename": "lecture-week-3.mp4",
  "source_reference": "lecture-week-3.mp4",
  "title": "lecture-week-3",
  "duration_seconds": 182.4,
  "language": "en",
  "transcription_model": "base",
  "processing_status": "completed",
  "created_at": "2026-10-12T09:31:07+00:00",
  "segment_count": 2,
  "segments": [
    { "segment_index": 0, "start_seconds": 0.0, "end_seconds": 4.8, "text": "Welcome back to week three." },
    { "segment_index": 1, "start_seconds": 4.8, "end_seconds": 9.1, "text": "Today we cover gradient descent." }
  ],
  "full_text": "Welcome back to week three. Today we cover gradient descent."
}
```

Schema rules Step 3 can rely on: `transcript_id` and `video_id` are unique and
stable; `source_type` is one of `local_video`, `local_audio` or `url`;
`source_reference` is the original filename or the source URL — never an internal
path; `created_at` is timezone-aware UTC; timestamps are non-negative floats with
`end_seconds >= start_seconds`; `segments` are in chronological order with
contiguous `segment_index`; `full_text` is constructed from the segments; `language`
and `duration_seconds` are `null` when the backend did not report them; `segments`
and `full_text` are omitted from the metadata index (`list_transcripts()`); no
secret, no file bytes and no media path is ever stored.

`video_id` is a deterministic hash of the source reference — the key Step 4 uses for
its vector store. Step 4 stores no transcript of its own: it reads this record and
refuses to index one whose `video_id` is missing or unsafe, so a passage can never be
attributed to a different recording.

---

## 12. Interfaces the report layer uses

The report builder uses exactly these interfaces and never calls the
provider directly.

### Generating and reading a summary

```python
from src.services.summarization_service import summarize_transcript
from src.models.summary_schemas import SummaryOptions

result = summarize_transcript(
    transcript=record,                          # TranscriptRecord from Step 2
    options=SummaryOptions(mode="meeting"),      # or None for the configured default
)

result.outcome        # SummaryOutcome - branch on this, never assume success
result.message        # user-safe sentence, already stripped of internals
result.summary        # StructuredSummary | None
result.stages         # list[SummaryStageReport] (stage, status, detail)
result.is_partial     # True when the source was only partly covered
```

`SummaryOutcome` covers `success`, `partial`, `missing_credentials`,
`provider_failed`, `validation_failed`, `storage_failed`, `invalid_transcript` and
`transcript_too_long`. A run that returns no summary also returns the real reason, so
the interface (and the report page) must show it rather than silently continuing.

### Reading stored summaries

```python
from src.services.summary_storage import SummaryStorage

storage = SummaryStorage(settings.summary_dir)          # data/processed/summaries
storage.list_metadata(transcript_id=record.transcript_id)   # newest first, no long text
storage.latest_for_transcript(record.transcript_id, options_fingerprint=...)
summary = storage.load(summary_id)                       # -> StructuredSummary
```

`StructuredSummary` carries `summary_id`, `source_id` (the Step 2 `video_id`),
`source_transcript_id`, `source_transcript_reference`, `summary_type`,
`options_fingerprint`, `overall_summary`, `short_summary`, `key_takeaways`,
`main_topics`, `chapters`, `action_items`, `decisions`, `important_points`,
`limitations`, `language`, `generation_model`, `generated_at`,
`source_duration_seconds`, `processing_status`, `coverage`, `model_calls`,
`latency_ms` and `used_chunking`. `all_points()` returns every textual point, which is
what a retrieval index or an export can use.

### Reading the RAG data

Step 4 is callable without Streamlit, so an exporter can attach the indexed chunks and
the citations of a conversation to a report:

```python
from src.rag.service import RagService
from src.models.rag_schemas import ChatOptions, ChatTurn

service = RagService(settings)
service.index_status(record)                 # -> IndexInfo (ready / stale / mismatch)
service.index_transcript(record)             # -> IndexResult (never destructive on failure)
result = service.ask("What did the speaker conclude?", record, history=[...])
result.answer.sources                        # every retrieved RetrievedSource
result.answer.cited_sources                  # only the passages the answer cited
result.answer.answer                         # validated answer text
service.retriever.retrieve(record.video_id, "pricing")   # raw retrieval, no model call
```

`RagChunk` / `RetrievedSource` / `RagCitation` / `RagAnswer` / `IndexInfo` are Pydantic
models, so they serialise directly (`.model_dump(mode="json")`). Timestamps are
optional and only ever copied from Step 2 segments.

### Building and rendering a report

The report layer consumes the stored records (never a live provider call):

```python
from src.reports.report_models import ReportOptions, ReportSection
from src.reports.report_builder import build_report, section_availability
from src.reports.export_formats import build_export, EXPORT_FORMATS

availability = section_availability(record, summary, chat_messages)
report = build_report(
    record,
    summary,
    chat_messages,                       # this session's turns for this video only
    ReportOptions(
        title="Lecture report",
        sections=[ReportSection.METADATA, ReportSection.SUMMARY, ReportSection.TRANSCRIPT],
    ),
    max_characters=settings.report_max_characters,
)
report.sections                          # what was included
report.omitted_sections                  # what was not, and why

export = build_export(report, "pdf", pdf_font_path=settings.report_pdf_font_path)
export.data, export.filename, export.mime, export.warnings
```

`EXPORT_FORMATS` is the same list the page renders, so a format cannot be advertised
without a renderer behind it. Renderers are pure functions of the report: they do not
read Streamlit or the filesystem, which is what makes them testable.

### Sharing the chunk boundaries

Step 4 indexes the same transcript that Step 3 summarised; its chunker keeps the Step 2
segment boundaries so a retrieved passage, a summary item and a report citation refer to
the same span of the recording:

```python
from src.rag.chunking import TranscriptChunker
from src.rag.transcript_adapter import normalize_transcript

chunks = TranscriptChunker(settings).chunk_transcript(normalize_transcript(record))
chunks[0].chunk_id          # deterministic: video + document type + position + text hash
chunks[0].time_label        # "00:00 - 00:12" or "timestamp unavailable"
```

Step 3's own section boundaries remain available through
`TranscriptPreprocessor.prepare(record, include_timestamps=True)` when an export needs the
summary's sections rather than the retrieval chunks.

Rules that hold across all steps: timestamps are optional everywhere and are never
invented; failures are reported with a reason instead of fabricating content; services
never read Streamlit session state, so every service above is callable from tests,
scripts and the report layer.

---

## 13. Privacy notes

* Step 2 transcription is **fully local**: FFmpeg and Whisper run on your machine, and
  no audio, transcript or metadata leaves it. Media input is validated and stored as a
  file on disk; only its metadata goes into Streamlit session state (never the bytes).
* The only network access in Step 2 is the Whisper model download (once per checkpoint)
  and, if you use it, URL acquisition of a link you supplied.
* From Step 3 onwards, transcript text you choose to process is sent to the configured
  NVIDIA endpoint. Nothing is sent automatically.
* Step 4 indexing is **fully local**: the embedding model runs on your machine and the
  vectors stay in `chroma_data/`. Only a question you actually ask is sent to NVIDIA,
  together with the retrieved passages — never the whole transcript, and never
  automatically.
* The chat conversation lives in Streamlit session state (per video) and is written to
  the vector store only as passages and metadata, never as a transcript of your chat.
* Step 5 report generation is **fully local and deterministic**: it reads stored files
  and the current chat session and renders the chosen format in memory. It sends
  nothing to NVIDIA and writes no copy of the report; only the browser download
  leaves the process. Chat answers appear in a report only when you tick that section.
* A report never contains API keys, environment values, internal paths, stack traces or
  hidden prompts, and it never includes another video's transcript or conversation.
* Source URLs are stripped of credentials and signed/tracking query parameters before
  they are written into a report.
* Uploaded media, extracted audio, transcripts, vector data and generated reports stay
  on your machine in `data/`, `chroma_data/` and `reports/` (all git-ignored).
