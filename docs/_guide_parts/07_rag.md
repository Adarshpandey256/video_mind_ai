## Chapter 8 — Step 4: The retrieval-augmented chatbot (RAG)

This is the chapter to master, because the chatbot is the part of the project that looks like magic
and is in fact a very disciplined pipeline.

### 8.1 What RAG means, in plain English

A plain LLM answers from what it memorised during training. Ask it about *your* 40-minute lecture and
it will either refuse, or — worse — invent something plausible. **Retrieval-Augmented Generation**
fixes that in three moves:

1. **Retrieve** the passages of *your* document that are relevant to the question (a search problem).
2. **Augment** the prompt: put those passages in front of the model as the only evidence.
3. **Generate** the answer from that evidence, and require the model to point at which passage it used.

**Why not just put the whole transcript in the prompt?** Three reasons: the context window is finite;
long prompts cost more and are slower; and models attend worse to the middle of a very long prompt.
Retrieval sends *only what matters*.

### 8.2 The concepts behind it

| Concept | Plain meaning | Where it lives here |
| --- | --- | --- |
| **Embedding** | A list of numbers (a vector) representing the *meaning* of a piece of text. Two texts about the same idea end up close together, even with different words | `src/rag/embeddings.py` — `LocalSentenceTransformerEmbeddings` |
| **Vector database** | A store optimised to hold vectors and answer "which stored vectors are nearest this one?" | `chromadb` (1.5.9), persistent under `chroma_data/` |
| **Semantic search** | Searching by meaning rather than by exact keyword | `ChromaVectorStore.search` (cosine similarity) |
| **Chunking** | Splitting a long document into passages small enough to embed and cite | `src/rag/chunking.py` |
| **Chunk overlap** | Repeating the tail of one chunk at the start of the next so a sentence split across the boundary is still findable | `_overlap_tail`, `RETRIEVAL_CHUNK_OVERLAP = 200` characters |
| **Top-k** | How many passages to fetch | `RETRIEVAL_TOP_K = 4` |
| **Context budget** | A hard cap on how much retrieved text goes into the prompt | `RETRIEVAL_MAX_CONTEXT_CHARS = 6000` |
| **Citation** | A reference from the answer back to a real passage and its timestamp | `RagCitation.from_source` |
| **Hallucination** | Confident text that is not supported by the source | Mitigated by grounding + validation (never *eliminated*) |

### 8.3 What ChromaDB and LangChain actually do here (no more, no less)

| Library | What this project uses from it | What it does **not** use |
| --- | --- | --- |
| `langchain-core` | `ChatPromptTemplate`, LCEL composition (`RunnableLambda`), `StrOutputParser`, the `Embeddings` interface | Chains for agents, tools, memory abstractions, retrievers-of-retrievers |
| `langchain-chroma` | The `Chroma` VectorStore wrapper (`_open_store`) | Any hosted/remote vector service |
| `chromadb` | `PersistentClient`, `get_or_create_collection`, `delete_collection`, vector counts | Non-default embedding functions (the project passes its own provider) |

This is deliberate: `requirements-later-stages.txt` records that the `langchain` meta-package and
`langchain-community` are **not** dependencies, because "Step 4 imports only `langchain_core` and
`langchain_chroma`".

### 8.4 The embedding model: chosen once, recorded forever

| Setting | Value | Why |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `sentence-transformers` (the only accepted value) | A remote embedding API would be a conscious change, not an accident |
| `EMBEDDING_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | 384-dimensional, fast on CPU, ~90 MB, very good quality for its size |
| `EMBEDDING_DEVICE` | `auto` (falls back to CPU) | Works on any machine |
| `EMBEDDING_BATCH_SIZE` | `32` | Balanced memory/throughput |

`embedding_model_id()` returns a stable identifier that is **stored in the index registry**. If you
switch models later, the old index is reported as `IndexStatus.EMBEDDING_MISMATCH` instead of being
searched with vectors from a different space — a silent-quality bug that this design prevents
(tested by `test_changed_embedding_model_is_reported_as_a_mismatch`).

Models are cached (`load_sentence_transformer`, `lru_cache`) and the cache can be dropped with
`reset_embedding_cache()`; the same function is used to embed documents and queries, which is
mandatory — comparing vectors from two different models is meaningless.

### 8.5 Normalisation: making a Step 2 transcript safe for retrieval

`src/rag/transcript_adapter.py` (177 lines) converts a stored `TranscriptRecord` into a
`NormalizedTranscript`:

| Rule | Reason |
| --- | --- |
| `normalize_text` collapses whitespace without changing words or order | Comparisons and hashes must be stable |
| Blank segments are dropped and counted | They would produce useless chunks |
| Negative or non-numeric timestamps become `None` | A fake `0.0` would look like a real position at the start |
| An impossible interval keeps the real start, drops the end | Some information beats none |
| `full_text` is used only when there are no segments | Prevents duplicated evidence |
| Unsafe `video_id` values are refused | The id becomes part of a collection name and a metadata field |
| `compute_fingerprint(video_id, segments, full_text)` | Detects "the transcript changed since it was indexed" → `IndexStatus.STALE` |

### 8.6 Chunking with timestamps preserved

`src/rag/chunking.py` (384 lines) is the most algorithmically interesting file in Step 4:

```
segments ──_segment_units──► units (sentences, or hard-split over-long segments)
         ──_pack───────────► ordered groups of units, at most RETRIEVAL_CHUNK_SIZE, with overlap
         ──_build_chunk────► RagChunk (text, chunk_id, time range, metadata)
```

Real code, `src/rag/chunking.py:265-305`:

```python
    def chunk_transcript(self, transcript: NormalizedTranscript) -> list[RagChunk]:
        """Split a normalised transcript into ordered, timestamped chunks.

        Raises :class:`ChunkingError` when there is no text to index or when the
        chunk budget is exceeded (rather than silently truncating the video).
        """
        if transcript.is_empty:
            raise ChunkingError("This transcript is empty, so there is nothing to index.")

        units = _segment_units(transcript.segments, self._size)
        if not units:
            raise ChunkingError("This transcript is empty, so there is nothing to index.")

        packed = _pack(units, size=self._size, overlap=self._overlap, minimum=self._minimum)
        if len(packed) > self._settings.rag_max_chunks_per_video:
            raise ChunkingError(
                f"This recording needs {len(packed)} chunks, which exceeds the configured "
                f"limit of {self._settings.rag_max_chunks_per_video}. Raise "
                "RAG_MAX_CHUNKS_PER_VIDEO or RETRIEVAL_CHUNK_SIZE if you really want to index it."
            )

        chunks: list[RagChunk] = []
        for index, group in enumerate(packed):
            chunk = _build_chunk(
                video_id=transcript.video_id,
                index=len(chunks),
                units=group,
                document_type=DocumentType.TRANSCRIPT,
                source_name=transcript.source_name,
                source_type=transcript.source_type,
            )
            if chunks and chunks[-1].text == chunk.text:
                continue  # overlap or repetition produced a duplicate passage
            chunks.append(chunk)
        ...
        return chunks
```

Key properties, each with a test:

| Property | Test |
| --- | --- |
| A short transcript becomes exactly one chunk carrying its real time range | `test_short_transcript_is_one_chunk_with_its_time_range` |
| Boundaries follow sentences where possible | `test_chunk_boundaries_follow_sentences_where_possible` |
| Overlap repeats the tail of the previous chunk (and can be disabled) | `test_overlap_repeats_the_tail_of_the_previous_chunk`, `test_overlap_can_be_disabled` |
| A transcript with no timeline never invents timestamps | `test_transcript_without_timestamps_never_invents_them` |
| Chunk ids are deterministic; rebuilding is reproducible | `test_chunk_ids_are_deterministic` |
| A tiny final chunk is merged instead of left alone | `test_tiny_final_chunk_is_merged_into_the_previous_one` |
| The budget is enforced, not "approximately" respected | `test_chunk_budget_is_enforced_instead_of_truncating` |
| Metadata is Chroma-safe | `test_chunk_metadata_is_chroma_safe` |
| Hindi/Unicode survives | `test_unicode_and_hindi_text_is_preserved` |

`chunk_summary()` indexes the stored Step 3 summary as **labelled context** only — summary chunks
never carry timestamps (the summary is a paraphrase, not a recording position) and they are never
treated as the evidence of record; `summary_text()` renders it into one readable document.

**ChromaDB metadata — why `None` is omitted.** Real code, `src/models/rag_schemas.py:117-145`:

```python
    def metadata(self) -> dict[str, str | int | float | bool]:
        """ChromaDB-safe metadata.

        ChromaDB rejects ``None`` and nested values, so unknown timestamps are
        *omitted* rather than stored as ``0`` (which would look like a real
        position at the start of the recording).
        """
        values = {
            "video_id": self.video_id,
            "chunk_id": self.chunk_id,
            "chunk_index": int(self.chunk_index),
            "document_type": self.document_type.value,
            "source_type": self.source_type.value,
            "text_hash": self.text_hash,
            "has_timestamps": self.has_timestamps,
        }
        if self.source_name:
            values["source_name"] = self.source_name
        if self.start_seconds is not None:
            values["start_seconds"] = float(self.start_seconds)
        ...
        return values
```

### 8.7 The vector store: one collection per video, plus a registry

`src/rag/vector_store.py` (598 lines) is the persistence layer:

| Symbol | Role |
| --- | --- |
| `collection_name_for(video_id, transcript_hash)` | A deterministic, Chroma-safe collection name that changes when the transcript changes |
| `ChromaVectorStore.build_index(...)` | Embed all chunks, write them, then make the new collection active (registry) |
| `ChromaVectorStore.status(video_id, transcript_hash=...)` | Honest state: `NOT_INDEXED`, `READY`, `STALE`, `EMBEDDING_MISMATCH`, `UNAVAILABLE` |
| `ChromaVectorStore.search(video_id, query, k, document_types, transcript_hash)` | Search **one** collection only |
| `count(video_id)` / `delete(video_id)` | Count and remove a video's index |
| `IndexRegistry` | Atomic JSON file mapping `video_id → IndexRecord` (collection, model id, fingerprint, counts, timestamps) |
| `_persistent_client(path)` (cached) | One Chroma client per directory per process |

**Cross-video contamination is prevented structurally, not by convention:** the collection name is
derived from the video id, `search` takes `video_id` and refuses an unknown one
(`IndexNotReadyError`), and the retrieved metadata also carries `video_id`.
`tests/test_rag_vector_store.py::test_search_never_crosses_video_boundaries` and
`tests/test_rag_service.py::test_asking_about_another_video_never_uses_this_videos_index` assert it.

### 8.8 Retrieval: ranking, de-duplication and the context budget

Real code, `src/rag/retriever.py:118-190` (abridged):

```python
        question = (query or "").strip()
        if not question:
            raise RetrievalError("Enter a question first.")

        resolved_top_k = max(1, int(top_k or self._settings.retrieval_top_k))
        with_summary = (...)
        document_types = (
            [DocumentType.TRANSCRIPT, DocumentType.SUMMARY]
            if with_summary
            else [DocumentType.TRANSCRIPT]
        )

        queries = [question]
        for extra in extra_queries or []:     # widened follow-up query
            ...
            queries.append(clean)

        merged: dict[str, RetrievedSource] = {}
        for active_query in queries:
            results = self.store.search(video_id, active_query, k=resolved_top_k,
                                        document_types=document_types,
                                        transcript_hash=transcript_hash)
            for source in results:
                key = source.chunk_id or f"{source.chunk_index}:{source.text[:80]}"
                current = merged.get(key)
                if current is None or _rank(source) < _rank(current):
                    merged[key] = source

        ranked = sorted(merged.values(), key=_rank)
        selected = self._within_context_budget(ranked, top_k=resolved_top_k)
        for index, source in enumerate(selected):
            source.source_ref = f"S{index + 1}"
        return selected
```

Three details worth explaining in a viva:

1. **`_rank`** sorts by cosine *distance* (lower is better) and pushes unknown distances to the end;
   `_similarity_from_distance` turns a distance into a 0–1 similarity for display.
2. **De-duplication by `chunk_id`** means the same passage found by two queries is counted once, with
   the better score kept.
3. **`source_ref = "S1", "S2", …` is assigned *after* selection.** Those labels are what the model is
   told to cite, so the model can never cite a passage that was not actually sent to it.

**Conversation-aware retrieval** (`needs_conversation_context`, `contextual_query`): a short or
referential question ("why?", "and the second one?") is widened with the previous question for the
*search* only — the model still receives the literal question plus a trimmed history
(`RAG_MAX_HISTORY_TURNS = 6`).

### 8.9 Answer generation: LCEL chain + citation validation

`src/rag/answer_generator.py` (379 lines):

```
ChatPromptTemplate ─► _call_model (adapter → LLMService.generate) ─► StrOutputParser
                                                                        │
                       extract_json_object  ◄── raw text ───────────────┘
                              │
                  (malformed) ├─► build_repair_prompt → one more attempt
                              │
                       _validate(data, sources)  → GeneratedAnswer(answer, citations, notes)
```

**Validation is the safety net.** Real code, `src/rag/answer_generator.py:236-283` (abridged):

```python
        answer_text = coerce_text(
            data.get("answer") or data.get("response") or data.get("text") or ""
        )
        if not answer_text:
            raise AnswerGenerationError(
                "The model returned an empty answer. Try asking again.",
                error_type="invalid_output",
            )

        by_ref = {source.source_ref: source for source in sources}
        ...
        for raw in raw_citations:
            ...
            source = by_ref.get(reference)
            if source is None:
                if reference:
                    unknown_refs.add(reference)
                continue
            if reference in cited_refs:
                continue
            verified_quote = quote
            if quote and not quote_appears_in(quote, source.text):
                verified_quote = None
                unverified_quotes.append(reference)
            citations.append(RagCitation.from_source(source, quote=verified_quote))
            cited_refs.add(reference)

        if unknown_refs:
            notes.append(
                "The answer cited source(s) that were not supplied to the model "
                f"({', '.join(sorted(unknown_refs))}); those citations were removed."
            )
```

So: a citation to a non-existent passage (`[S9]` when only 4 were sent) is **removed and reported**;
a quotation that does not literally appear in the passage is **dropped** (the timestamp and passage
remain, the quote does not); duplicate citations collapse; `_strip_unknown_inline_references` also
cleans stray inline markers out of the answer text. `RagAnswer.cited_sources` therefore contains only
passages that really exist. Tests: `test_unknown_citation_is_removed_and_reported`,
`test_quotation_that_is_not_in_the_passage_is_dropped`,
`test_citation_without_a_source_is_never_invented`, `test_two_unusable_answers_fail_instead_of_showing_uncited_text`.

**Truncation and failure messages** are translated, not raw: `_friendly_message(error_type)` turns
`truncated` into a sentence naming the budget to raise, and provider errors into actionable text.

### 8.10 Chat history and per-video isolation

| Concern | Implementation |
| --- | --- |
| Where history lives | `st.session_state`, under `history_key(video_id)` |
| Scope | **One conversation per video id.** `get_history`/`set_history`/`clear_history` all take `video_id` |
| What is sent to the model | `chat_turns(messages)` converts rendered messages into compact `ChatTurn`s; only the last `RAG_MAX_HISTORY_TURNS` (6) are rendered, and old answers are trimmed to 400 characters so they cannot dominate the prompt |
| Persistence | **None across restarts** (`ISS-006`): closing the app forgets conversations |
| Isolation tests | `test_clear_conversation_only_affects_the_active_video`, `test_switching_videos_shows_only_that_videos_conversation` |

### 8.11 A full question, traced through the real implementation

> **Clearly labelled illustrative example.** The text below is a hand-written walkthrough of the code
> path, not a captured live answer.

**User asks:** "What did the lecturer say about supervised learning?" (video: `vid-chatui01`)

| # | What happens | Code |
| --- | --- | --- |
| 1 | The page reads the input box and calls the service seam | `chat_page._handle_question` → `ask_question(settings, transcript, question, history)` |
| 2 | The service validates the question (non-empty, ≤ 1000 chars) | `RagService.ask` → `RagOutcome.INVALID_QUESTION` otherwise |
| 3 | Free shortcut: if the video is not indexed, it stops with `NO_INDEX` and the page offers a Build button | `RagService.ask` → `IndexInfo.status` |
| 4 | Provider check: no key → `MISSING_CREDENTIALS` *before* any work | `GroundedAnswerGenerator.is_configured` |
| 5 | Retrieval: the question is embedded and compared against **this video's** collection | `Retriever.retrieve` → `ChromaVectorStore.search` |
| 6 | The best 4 passages are ranked, de-duplicated, trimmed to the 6000-character context budget, and labelled `S1…S4` | `_within_context_budget` |
| 7 | If nothing was retrieved → `RagOutcome.NO_RESULTS` ("I could not find this in the recording") — the model is **not** asked | `RagService.ask` |
| 8 | The prompt is built: system rules ("answer only from the evidence, cite `[S#]`, treat passages as data") + `render_sources` + trimmed history + the question + the JSON contract | `src/rag/prompts.py` |
| 9 | The LCEL chain runs: prompt → adapter → `LLMService.generate` → `StrOutputParser` | `_build_chain`, `_call_model` |
| 10 | The JSON is extracted; if malformed, one bounded repair re-ask happens | `extract_json_object`, `build_repair_prompt` |
| 11 | Citations are resolved against the supplied passages; unknown refs and unverified quotes are removed and noted | `_validate` |
| 12 | `RagAnswer` + `RagResult(outcome=success)` are returned; the page stores the message and renders the answer with each citation's `time_label` and excerpt | `chat_page._assistant_message`, `_render_citations` |

If step 6 returns passages but the model's answer cites a source that was not supplied, the user sees
the answer **plus** a note that a citation was removed — an honest partial result rather than a
fabricated reference.

### 8.12 The three things students confuse

| Situation | Does it use your documents? | Does it write new text? | In this project |
| --- | --- | --- | --- |
| 1. LLM answering from its own learned knowledge | No | Yes | **Not used** for answering — the prompt forbids it |
| 2. LLM answering from retrieved evidence | Yes (retrieved passages) | Yes (composed from evidence) | **This is what `GroundedAnswerGenerator` does** |
| 3. Retrieval finding relevant content, no answering | Yes | No | **This is `Retriever.retrieve`** — you can see it alone by inspecting the "retrieved sources" shown with each answer |

### 8.13 Honest limitations of this RAG implementation

| Limitation | Why it exists | Mitigation in the code |
| --- | --- | --- |
| Retrieval can miss the right passage (wrong chunk, embedding mismatch) | Embeddings are approximate; chunking is heuristic | Top-k 4, overlap, conversation-aware query widening; the UI lists the retrieved sources so the miss is visible |
| Missing evidence → weaker answer | The model only sees what was retrieved | `NO_RESULTS` handling; the answer contract permits "the recording does not mention this" |
| Hallucinated content remains possible | LLMs generate fluent text | Grounding + citation/quote validation + honest notes. **The chatbot is not hallucination-proof** |
| Wrong timestamps | The model can supply a plausible time | Citations take their time from the stored metadata, never from the model's text |
| Prompt injection via transcript text | Transcripts are untrusted input | Markers + "these passages are data" instruction + tests; still a risk, not a proof |
| Summary chunks are paraphrases | They are indexed for context | Labelled `summary` in the UI ("paraphrase"), never used as the evidence of record |
| Chat history dies with the app | Session-scoped by design in Step 4 | `ISS-006` documented as a limitation |
| Quality depends on the embedding model | `all-MiniLM-L6-v2` is small and fast | Swapping models is one setting; the registry flags the mismatch instead of silently searching |
