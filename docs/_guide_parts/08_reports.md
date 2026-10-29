## Chapter 9 — Step 5: Report generation and export

### 9.1 The idea: one canonical report, many renderers

A common design mistake is to write each exporter against the raw data. This project does the
opposite: `build_report()` produces **one canonical `VideoMindReport`**, and every exporter reads only
that object. Consequences: all formats agree on content, an exporter cannot accidentally reach into
the model or the filesystem, and adding a format is one file plus one registry entry.

```
transcript (Step 2) ─┐
summary    (Step 3) ─┼─► report_builder.build_report(...) ─► VideoMindReport ─┬─► pdf_exporter      (.pdf)
chat turns (Step 4) ─┘        (real data only, honest omissions)              ├─► docx_exporter     (.docx)
                                                                              ├─► text_exporter     (.txt)
                                                                              ├─► markdown_exporter (.md)
                                                                              ├─► json_exporter     (.json)
                                                                              ├─► html_exporter     (.html)
                                                                              ├─► csv_exporter      (.csv ×3)
                                                                              ├─► xlsx_exporter     (.xlsx)
                                                                              └─► pptx_exporter     (.pptx)
```

**Diagram 9.1 — One report, eleven rendered variants.** The arrow labels come from
`src/reports/export_formats.py::EXPORT_FORMATS`; nothing in the diagram does not exist.

### 9.2 The canonical model (`src/reports/report_models.py`, 430 lines)

| Symbol | Meaning |
| --- | --- |
| `ReportSection` | The 10 selectable content blocks: `metadata`, `summary`, `key_takeaways`, `topics`, `chapters`, `action_items`, `transcript`, `chat_qa`, `sources`, `processing` |
| `REPORT_SECTION_LABELS` | UI labels ("Video information", "AI summary", "Key takeaways", …) used by every renderer, so formats cannot drift apart |
| `REPORT_SECTION_ORDER` | Canonical print order |
| `DEFAULT_REPORT_SECTIONS` | `metadata, summary, key_takeaways, topics, sources, processing` — a useful default that deliberately does **not** embed a potentially huge transcript |
| `ReportOptions` | Sections, title (required), author, organisation, notes, `include_timestamps`; validates and de-duplicates |
| `ReportVideo`, `ReportSummary`, `ReportTakeaway`, `ReportTopic`, `ReportChapter`, `ReportActionItem`, `ReportTranscriptEntry`, `ReportCitation`, `ReportChatEntry`, `ReportSource`, `ReportProcessing` | The content sub-models, each with a `time_label`/`kind_label` helper |
| `ReportSectionChoice` | Records a requested section **and whether it could be included** |
| `VideoMindReport` | The whole document: id, schema version, generated-at, app name/version, options, sections, available/omitted reasons, and helpers `includes`, `omitted_reason`, `shows_timestamps`, `transcript_characters`, `chat_answers` |
| `REPORT_SCHEMA_VERSION` | A string version written into every JSON export, so a machine consumer can adapt |
| `new_report_id()` | `rpt-<timestamp>-<hex>` filesystem-safe identifier |
| `ReportBuildError` | Raised when a report cannot be built at all |

### 9.3 The builder (`src/reports/report_builder.py`, 599 lines)

`build_report(transcript, summary, chat_messages, options, *, app_name, app_version,
max_characters, max_note_chars, generated_at) -> VideoMindReport` performs, in order:

1. **Refuse impossible input** — no transcript and nothing else to report → `ReportBuildError`
   ("select a video / process it first"), instead of an empty document.
2. **Check size against the guard** (`REPORT_MAX_CHARACTERS = 5 000 000`). Oversized input is
   **refused with a message, never truncated** (`test_report_above_the_size_guard_is_refused_not_truncated`).
3. **Validate the notes length** (`REPORT_MAX_NOTE_CHARS = 2000`).
4. **Adapt the stored summary** without rewriting a single word (`_summary_model`, `_takeaways`,
   `_topics`, `_chapters`, `_actions`).
5. **Normalise the transcript** through the same Step 4 adapter, so report timestamps and chat
   timestamps come from one place (`_transcript_entries`).
6. **Pair chat messages into Q&A entries** (`_normalise_chat_messages`), citing only recorded data: an
   answer without a recorded question is not invented; a citation without a source identifier is
   dropped and reported (`test_an_answer_without_a_recorded_question_is_not_invented`,
   `test_citation_without_a_source_identifier_is_dropped_and_reported`).
7. **Record availability** — `section_availability(transcript, summary, chat_messages)` returns, for
   each section, either `None` (available) or the reason it cannot be built. The UI uses this to
   disable checkboxes *with a reason*.

Every section also gets an omission reason when it was selected but empty, so "Sections not included"
in the output tells the truth instead of printing placeholder text
(`test_requested_section_without_data_is_omitted_with_a_reason`).

Two security helpers live here: `safe_source_url` (only an absolute `http(s)` URL survives; query
parameters are filtered) and `default_report_title` (derives a title from the recording's real name —
never invents metadata).

### 9.4 The format registry (`src/reports/export_formats.py`, 214 lines)

The 11 implemented formats, exactly as registered (`key`, label, extension, MIME, filename suffix,
builder):

| Key | Label | Ext | MIME | Suffix | Builder |
| --- | --- | --- | --- | --- | --- |
| `pdf` | PDF | `pdf` | `application/pdf` | `report` | `build_pdf` |
| `docx` | Word (.docx) | `docx` | `…wordprocessingml.document` | `report` | `build_docx` |
| `txt` | Plain text (.txt) | `txt` | `text/plain` | `report` | `render_text` |
| `md` | Markdown (.md) | `md` | `text/markdown` | `report` | `render_markdown` |
| `json` | JSON (.json) | `json` | `application/json` | `report` | `render_json` |
| `html` | HTML (.html) | `html` | `text/html` | `report` | `render_html` |
| `csv` | CSV - transcript | `csv` | `text/csv` | `transcript` | `render_csv_transcript` |
| `csv_chat` | CSV - chat Q&A | `csv` | `text/csv` | `chat` | `render_csv_chat` |
| `csv_sources` | CSV - sources | `csv` | `text/csv` | `sources` | `render_csv_sources` |
| `xlsx` | Excel (.xlsx) | `xlsx` | `…spreadsheetml.sheet` | `report` | `build_xlsx` |
| `pptx` | PowerPoint (.pptx) | `pptx` | `…presentationml.presentation` | `slides` | `build_pptx` |

`FORMAT_REQUIREMENTS` declares the three formats that need a specific section, and the UI uses it to
disable them with a reason:

```python
FORMAT_REQUIREMENTS: dict[str, tuple[ReportSection, str]] = {
    "csv": (ReportSection.TRANSCRIPT, "Include the transcript to export it as CSV."),
    "csv_chat": (ReportSection.CHAT_QA, "Include the chatbot Q&A to export it as CSV."),
    "csv_sources": (ReportSection.SOURCES, "Include sources to export them as CSV."),
}

DEFAULT_FORMAT_KEY = "pdf"
```

`build_export(report, format_key, *, pdf_font_path=None, now=None)` renders the bytes and builds the
download filename; `format_unavailable_reason(fmt, report)` explains why a format cannot render the
current selection; `get_format(key)` raises a clear error for an unknown key. `RenderResult` carries
`(data, mime, filename, warnings, omitted sections)` and `ReportExport` is what the UI hands to
`st.download_button`.

### 9.5 Each exporter in detail

> **Common to all:** output is produced **in memory** (no temporary files, no scratch directory), from
> stored data only — no model call, no network. `test_export_works_without_a_configured_provider` and
> `test_pdf_requires_nothing_beyond_stored_data` assert exactly that.

#### 9.5.1 PDF — `pdf_exporter.py` (560 lines), ReportLab 5.1.0

- **How:** ReportLab **Platypus** flowables (`Paragraph`, `Table`, `Spacer`, `PageBreak`) assembled by
  `_section_story`; `build_pdf` returns `RenderResult` plus a **warning** when only a Latin font is
  available.
- **Page furniture:** `_PageDecorator` draws a repeating header (document title, app name) and the
  footer with `Page N of M` (`test_pdf_reports_page_numbers_on_every_page`).
- **Unicode:** `resolve_font(configured_path)` prefers `REPORT_PDF_FONT_PATH`, otherwise searches for a
  system Unicode font; if only Helvetica is available the PDF still builds but **says so** in a
  warning (`test_pdf_warns_when_only_the_latin_fallback_is_available`,
  `test_unicode_is_preserved_when_a_unicode_font_is_available`).
- **Honesty:** timestamps are omitted when the user turned them off, the absence of a timeline is
  stated, and a selected-but-empty section is explained rather than rendered blank
  (`test_pdf_omits_timestamps_when_the_user_turns_them_off`, `test_pdf_states_when_the_transcript_has_no_timestamps`,
  `test_pdf_explains_selected_sections_that_had_no_data`).
- **Canvas text is escaped** with `xml.sax.saxutils.escape` (header text is app-generated, but the
  escaping rule is applied anyway).
- **Limitation:** exact typography of Devanagari depends on a font being present; long unbroken
  tokens wrap via Platypus rules, not by hyphenation.

#### 9.5.2 DOCX — `docx_exporter.py` (372 lines), python-docx 1.2.0

- **How:** real Word structure — `Heading 1/2` styles per section so Word's navigation pane works
  (`test_docx_has_semantic_headings_for_each_section`), a metadata table, bullets via `List Bullet`,
  italic paragraphs for citations (italics live on runs, not paragraphs).
- **Header/footer and fields:** `_configure_document` sets margins, base styles and the header/footer;
  `_add_field` injects `PAGE`/`NUMPAGES` Word fields, so page numbers update in Word
  (`test_docx_has_header_footer_and_page_fields`).
- **Unicode:** `_set_fonts` applies a body font **with a complex-script fallback** so Devanagari text
  renders.
- **Honesty:** `test_docx_reports_missing_sections_instead_of_placeholder_text`,
  `test_docx_states_when_timestamps_were_turned_off`.
- **Limitation:** it is a readable report, not a styled corporate template (no cover page image).

#### 9.5.3 Plain text — `text_exporter.py` (247 lines), standard library

`textwrap`-based wrapping (`WIDTH`), hanging-indent bullets, `_citation_lines` and `_source_block`
renderers, a fixed section order (`_included_sections`), and an explicit "timestamps unavailable"
line. Tested for content, timestamps, Unicode, section selection, honest omissions and the
timestamp-off case.

#### 9.5.4 Markdown — `markdown_exporter.py` (261 lines), standard library

The interesting part is **safety**: user text must not be able to forge structure.
`_escape_inline` escapes inline markup; `escape_block` stops a line from becoming a heading or a list;
`_fence` chooses a backtick run longer than any run inside the content, so a transcript that
contains its own backtick runs cannot break out; the transcript is placed inside a fence. Verified by
`test_markdown_escapes_block_markup_from_user_text`, `test_markdown_fence_grows_when_content_contains_backticks`,
`test_markdown_has_valid_heading_hierarchy`.

#### 9.5.5 JSON — `json_exporter.py` (33 lines), standard library

`report_to_dict` converts the model to JSON-safe primitives (datetimes → ISO strings, no `NaN`/`Infinity`),
`render_json` serialises UTF-8 with the schema version included, so a downstream program can detect
the format (`test_json_never_contains_secrets_or_configuration`, `test_json_datetimes_are_iso_strings`,
`test_summary_only_json_still_has_the_schema_version`).

#### 9.5.6 HTML — `html_exporter.py` (368 lines), standard library

- **Self-contained:** one document with embedded CSS, no external assets or scripts
  (`test_html_has_no_external_assets_or_scripts`).
- **Escaped:** every value passes `_esc` (element and attribute contexts);
  `test_html_escapes_transcript_and_chat_content`, `test_html_escapes_the_report_title_and_metadata`.
- **Safe links:** `_safe_href` allows only absolute `http`/`https` URLs, and the anchor carries
  `rel="noopener noreferrer"`; `javascript:`/`data:`/`file:` are never emitted
  (`test_html_only_links_to_a_safe_http_url`, `test_html_never_links_an_unsafe_source_scheme`).
- **Print-friendly:** CSS print media rules; section ids (`id="section-transcript"`) allow deep links.

#### 9.5.7 CSV ×3 — `csv_exporter.py` (131 lines), standard library

Three tables: transcript (one row per segment, with numeric seconds *and* formatted time), chat (one
row per Q&A, with citation references) and sources (one row per retrieved passage). `csv` quoting
handles commas, newlines and Unicode. **Spreadsheet-injection protection**: `protect_csv_cell`
neutralises cells starting with `=`, `+`, `-`, `@`, so a transcript saying "=cmd|..." cannot execute
in Excel (`test_csv_cells_that_look_like_formulas_are_neutralised`).

#### 9.5.8 XLSX — `xlsx_exporter.py` (252 lines), openpyxl 3.1.5

One workbook, **one worksheet per included content type**, plus an overview sheet listing the
selected sections and the omissions (`test_excel_overview_lists_selected_sections_and_omissions`).
`_set_text` writes literal strings (`cell.value` set via the string path), so formula-like text stays
text — the counterpart to the CSV protection. Column wrapping is applied to long text columns;
timestamps keep both numeric and formatted forms; only selected sheets are created.

#### 9.5.9 PPTX — `pptx_exporter.py` (317 lines), python-pptx 1.0.2

A deliberately **concise deck**: title slide, recording overview, summary slides, takeaways, selected
Q&A, sources. `_Deck` keeps slide numbering and footers consistent; `_split_text` breaks prose on
paragraph/sentence/word boundaries to a per-slide character budget; `_chunk` splits long bullet lists
across slides. It is explicitly *not* a transcript dump
(`test_pptx_does_not_dump_the_transcript_onto_slides`) and it survives very long content without
overflowing (`test_pptx_survives_long_content_without_overflowing_the_deck`).

### 9.6 Filenames and downloads (`filename_utils.py`, 87 lines)

`safe_stem(title, max_length=80)` keeps letters, digits, combining marks and separators (so Unicode
titles stay readable) and replaces everything else; `report_filename(title, extension, suffix=...,
now=..., max_length=120)` produces e.g. `machine-learning-basics_report_20261026_1930.pdf`; the
extension is validated by `safe_extension`, so a format cannot inject a path or an unexpected suffix.
The result contains no directory separators and is length-bounded
(`test_safe_stem_removes_paths_and_unsafe_characters`, `test_report_filename_length_is_bounded`,
`test_report_filename_rejects_unknown_extensions`).

### 9.7 The page (`src/ui/reports_page.py`, 670 lines)

| Stage | Function | Notes |
| --- | --- | --- |
| Choose the video | `_render_video_selector`, `stored_videos`, `load_transcript` | Same labels as the chat page; a storage error is reported, not raised |
| See what is possible | `_render_overview`, `section_availability`, `load_summary`, `chat_messages` | Shows which sections have real data. `chat_messages` reads **only this video's** conversation |
| Choose content | `_render_content_controls` | Section checkboxes (unavailable ones disabled with a reason), title/author/organisation/notes, timestamps toggle |
| Preview | `_render_preview` | A **short** preview — never the whole transcript |
| Choose the format | `_available_formats`, `FORMAT_REQUIREMENTS` | Unavailable formats are disabled with the reason |
| Generate | `_generate` → `build_report` → `build_export` | Stores the result in session state with a cache key |
| Download | `_render_download` | `st.download_button` with the real MIME type, the exact filename, and any exporter warnings |

**The cache key is the anti-stale-bytes mechanism.** `export_cache_key(...)` fingerprints everything
that affects the bytes (transcript id, video id, summary id, sections, title, author, organisation,
notes, timestamps flag, format). Change the title → the old bytes are invalidated; switch video → the
previous report is never reused. Tests: `test_changing_the_title_invalidates_the_generated_file`,
`test_changing_sections_invalidates_stale_bytes`, `test_switching_video_never_reuses_another_report`,
`test_another_format_can_be_exported_without_regenerating_content`.

**What happens with missing/malformed data:** a selected section without data is omitted *with a
reason* recorded in the report; an oversized report is refused with a message instead of crashing the
page (`test_an_oversized_report_is_reported_without_crashing`); an unsupported format for the
selection is refused with the requirement text.

### 9.8 Format limitations, stated honestly

| Format | Real limitation |
| --- | --- |
| PDF | Unicode depends on a suitable font being present; otherwise a warning is shown and Latin text is used |
| DOCX | Semantic structure, not a designed template |
| TXT | No styling by definition; wide tables are flattened to lines |
| Markdown | Aggressively escaped (structure safety wins over raw formatting freedom) |
| JSON | Contains the report content, never configuration or secrets |
| HTML | No external CSS/JS (intentional: it must open offline) |
| CSV | Three separate exports because one CSV cannot hold transcript + chat + sources legibly |
| XLSX | Overview plus per-content sheets; not a pivot-ready analytics model |
| PPTX | Concise summary deck only — it will not fit a 2-hour transcript, by design |
