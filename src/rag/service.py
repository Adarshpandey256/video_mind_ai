"""Step 4 orchestration: index a transcript, then answer questions about it.

The service is the only entry point the interface (and later Steps) needs::

    service = RagService(settings)
    info = service.index_status(transcript)
    result = service.index_transcript(transcript)
    answer = service.ask("What did the speaker say about pricing?", transcript)

It owns the order of operations and nothing else - chunking lives in
:mod:`src.rag.chunking`, storage in :mod:`src.rag.vector_store`, retrieval in
:mod:`src.rag.retriever`, prompts in :mod:`src.rag.prompts` and the model call in
:mod:`src.rag.answer_generator`. There is no dependency on Streamlit, so the same
calls work from tests and from a future report exporter.

Honest reporting rules:

* a question is never answered without an index, and never from another video's
  index;
* an answer without validated citations is rejected, not displayed;
* a failed model call is never replaced by a fabricated answer;
* a stale index (transcript changed) is reported as stale instead of silently
  mixing old and new content.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from config.settings import Settings, get_settings
from src.models.rag_schemas import (
    ChatOptions,
    ChatTurn,
    IndexInfo,
    IndexOutcome,
    IndexResult,
    IndexStatus,
    RagAnswer,
    RagChunk,
    RagOutcome,
    RagResult,
    RetrievedSource,
)
from src.models.schemas import TranscriptRecord, TranscriptMetadata
from src.rag.answer_generator import (
    AnswerGenerationError,
    GeneratedAnswer,
    GroundedAnswerGenerator,
)
from src.rag.chunking import ChunkingError, TranscriptChunker
from src.rag.embeddings import EmbeddingError
from src.rag.retriever import RetrievalError, Retriever, contextual_query
from src.rag.transcript_adapter import (
    NormalizedTranscript,
    TranscriptNormalizationError,
    normalize_transcript,
)
from src.rag.vector_store import (
    ChromaVectorStore,
    IndexNotReadyError,
    VectorStoreError,
)
from src.services.summary_storage import SummaryStorage, SummaryStorageError
from src.services.transcript_service import TranscriptService

logger = logging.getLogger(__name__)

#: Error types that must not be retried by the user without changing something.
FATAL_ANSWER_ERROR_TYPES = {"configuration", "authentication"}


class RagService:
    """Index transcripts and answer grounded questions about one video."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: ChromaVectorStore | None = None,
        retriever: Retriever | None = None,
        generator: GroundedAnswerGenerator | None = None,
        transcripts: TranscriptService | None = None,
        summaries: SummaryStorage | None = None,
        chunker: TranscriptChunker | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._store = store or ChromaVectorStore(self._settings)
        self._retriever = retriever or Retriever(self._settings, store=self._store)
        self._generator = generator or GroundedAnswerGenerator(self._settings)
        self._transcripts = transcripts or TranscriptService(self._settings)
        self._summaries = summaries or SummaryStorage(self._settings.summary_dir)
        self._chunker = chunker or TranscriptChunker(self._settings)

    # ------------------------------------------------------------------ properties
    @property
    def settings(self) -> Settings:
        """Return the settings used by this service."""
        return self._settings

    @property
    def store(self) -> ChromaVectorStore:
        """Return the vector store."""
        return self._store

    @property
    def retriever(self) -> Retriever:
        """Return the retrieval service."""
        return self._retriever

    @property
    def generator(self) -> GroundedAnswerGenerator:
        """Return the grounded answer generator."""
        return self._generator

    @property
    def transcripts(self) -> TranscriptService:
        """Return the transcript service."""
        return self._transcripts

    def is_provider_configured(self) -> bool:
        """True when answers can be requested from the model."""
        return self._generator.is_configured()

    def list_videos(self) -> list[TranscriptMetadata]:
        """Every stored transcript, newest first (the chatbot's video list)."""
        return self._transcripts.list_transcripts()

    def load_transcript(self, transcript_id: str) -> TranscriptRecord:
        """Load a stored transcript by id."""
        return self._transcripts.load(transcript_id)

    # ------------------------------------------------------------------ status
    def index_status(self, transcript: TranscriptRecord | None) -> IndexInfo:
        """Return the index state for a transcript, including staleness."""
        if transcript is None:
            return IndexInfo(video_id="", status=IndexStatus.NOT_INDEXED)
        try:
            normalized = normalize_transcript(transcript)
        except TranscriptNormalizationError as error:
            return IndexInfo(
                video_id=str(transcript.video_id or ""),
                status=IndexStatus.UNAVAILABLE,
                title=transcript.title,
                message=str(error),
            )
        return self._store.status(normalized.video_id, transcript_hash=normalized.fingerprint)

    # ------------------------------------------------------------------ indexing
    def index_transcript(
        self,
        transcript: TranscriptRecord | None,
        *,
        rebuild: bool = False,
        include_summary: bool | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> IndexResult:
        """Build (or reuse) the vector index for one transcript.

        ``rebuild=True`` forces a new collection even when the transcript and the
        embedding model are unchanged. An unchanged index is reused, so clicking
        "build" twice does not duplicate work or vectors.
        """
        try:
            normalized = normalize_transcript(transcript)
        except TranscriptNormalizationError as error:
            return IndexResult(outcome=IndexOutcome.NO_TRANSCRIPT, message=str(error))
        assert transcript is not None  # guarded by normalize_transcript

        if normalized.is_empty:
            return IndexResult(
                outcome=IndexOutcome.EMPTY_TRANSCRIPT,
                message=(
                    "This transcript has no text, so there is nothing to index. Re-run "
                    "transcription on the recording."
                ),
            )

        with_summary = (
            self._settings.retrieval_include_summary
            if include_summary is None
            else bool(include_summary)
        )

        current = self._store.status(
            normalized.video_id, transcript_hash=normalized.fingerprint
        )
        if current.status is IndexStatus.READY and not rebuild:
            return IndexResult(
                outcome=IndexOutcome.REUSED,
                message="This video is already indexed; the existing index was reused.",
                info=current,
                chunks_indexed=current.chunk_count,
            )

        self._notify(on_stage, "Splitting the transcript into chunks")
        try:
            chunks = self._chunker.chunk_transcript(normalized)
        except ChunkingError as error:
            outcome = (
                IndexOutcome.CHUNK_LIMIT_EXCEEDED
                if "exceeds the configured limit" in str(error)
                else IndexOutcome.EMPTY_TRANSCRIPT
            )
            return IndexResult(outcome=outcome, message=str(error))

        notes: list[str] = []
        if with_summary:
            summary_chunks = self._summary_chunks(transcript, normalized)
            if summary_chunks:
                chunks.extend(summary_chunks)
                notes.append(f"{len(summary_chunks)} summary chunk(s) were indexed as context.")
            else:
                notes.append("No summary was available, so only the transcript was indexed.")

        self._notify(
            on_stage,
            f"Embedding {len(chunks)} chunk(s) with {self._store.model_id}",
        )
        try:
            info = self._store.build_index(
                video_id=normalized.video_id,
                chunks=chunks,
                transcript_hash=normalized.fingerprint,
                transcript_id=normalized.transcript_id,
                title=normalized.title,
                source_name=normalized.source_name,
            )
        except EmbeddingError as error:
            logger.warning("Indexing failed at the embedding step: %s", type(error).__name__)
            return IndexResult(
                outcome=IndexOutcome.EMBEDDING_UNAVAILABLE,
                message=str(error),
                info=current if current.status is IndexStatus.READY else None,
            )
        except VectorStoreError as error:
            logger.warning("Indexing failed in the vector store: %s", type(error).__name__)
            return IndexResult(
                outcome=IndexOutcome.STORE_FAILED,
                message=str(error),
                info=current if current.status is IndexStatus.READY else None,
            )

        message = f"Indexed {len(chunks)} chunk(s) for this video."
        if notes:
            message = f"{message} {' '.join(notes)}"
        logger.info(
            "Indexed %s (%s transcript chunk(s), summary=%s).",
            normalized.video_id,
            len(chunks),
            with_summary,
        )
        return IndexResult(
            outcome=IndexOutcome.SUCCESS,
            message=message,
            info=info,
            chunks_indexed=len(chunks),
        )

    def delete_index(self, video_id: str) -> bool:
        """Delete one video's index (its collection and registry entry)."""
        return self._store.delete(video_id)

    # ------------------------------------------------------------------ questions
    def ask(
        self,
        question: str,
        transcript: TranscriptRecord | None,
        *,
        history: Sequence[ChatTurn] = (),
        options: ChatOptions | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> RagResult:
        """Answer a question from this video's indexed transcript."""
        cleaned = (question or "").strip()
        if not cleaned:
            return RagResult(
                outcome=RagOutcome.INVALID_QUESTION,
                message="Enter a question about the video first.",
            )
        limit = self._settings.rag_max_question_chars
        if len(cleaned) > limit:
            return RagResult(
                outcome=RagOutcome.INVALID_QUESTION,
                message=(
                    f"That question is {len(cleaned)} characters long; the limit is {limit}. "
                    "Shorten it and try again."
                ),
            )

        try:
            normalized = normalize_transcript(transcript)
        except TranscriptNormalizationError as error:
            return RagResult(outcome=RagOutcome.NO_TRANSCRIPT, message=str(error))
        if normalized.is_empty:
            return RagResult(
                outcome=RagOutcome.NO_TRANSCRIPT,
                message=(
                    "This transcript has no text, so there is nothing to retrieve. Re-run "
                    "transcription on the recording."
                ),
            )

        info = self._store.status(normalized.video_id, transcript_hash=normalized.fingerprint)
        if info.status is not IndexStatus.READY:
            return RagResult(
                outcome=(
                    RagOutcome.INDEX_UNAVAILABLE
                    if info.status is IndexStatus.UNAVAILABLE
                    else RagOutcome.NO_INDEX
                ),
                message=info.describe(),
            )

        if not self.is_provider_configured():
            return RagResult(
                outcome=RagOutcome.MISSING_CREDENTIALS,
                message=(
                    "No NVIDIA API key is configured, so no answer can be generated. The "
                    "transcript and index are unaffected - add NVIDIA_API_KEY to your local "
                    ".env file and restart the application."
                ),
            )

        resolved = options or ChatOptions()
        history_list = list(history)
        contextual = contextual_query(history_list, cleaned)

        self._notify(on_stage, "Searching the transcript for relevant passages")
        try:
            sources = self._retriever.retrieve(
                normalized.video_id,
                contextual or cleaned,
                top_k=resolved.top_k,
                include_summary=resolved.include_summary,
                transcript_hash=normalized.fingerprint,
                extra_queries=[cleaned] if contextual and contextual != cleaned else None,
            )
        except IndexNotReadyError as error:
            return RagResult(outcome=RagOutcome.NO_INDEX, message=str(error))
        except RetrievalError as error:
            return RagResult(outcome=RagOutcome.INDEX_UNAVAILABLE, message=str(error))

        if not sources:
            return RagResult(
                outcome=RagOutcome.NO_RESULTS,
                message=(
                    "No passage of this video's transcript was close enough to the question to "
                    "use as evidence. Try rephrasing it with words from the recording."
                ),
            )

        self._notify(
            on_stage,
            f"Generating an answer from {len(sources)} retrieved passage(s)",
        )
        started = time.perf_counter()
        try:
            generated = self._generator.generate(
                cleaned,
                sources,
                history=history_list,
                video_title=normalized.title,
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
            )
        except AnswerGenerationError as error:
            return RagResult(
                outcome=_outcome_for_answer_error(error.error_type),
                message=str(error),
                sources=sources,
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        answer = self._build_answer(
            question=cleaned,
            generated=generated,
            sources=sources,
            contextual_query=contextual or cleaned,
            history_turns=len(history_list),
            elapsed_ms=elapsed_ms,
        )
        logger.info(
            "Answered question about %s (sources=%s, cited=%s, insufficient=%s, latency_ms=%s).",
            normalized.video_id,
            len(sources),
            len(answer.citations),
            answer.insufficient_evidence,
            elapsed_ms,
        )
        return RagResult(
            outcome=RagOutcome.SUCCESS,
            message="Answer generated from the video transcript.",
            answer=answer,
            sources=sources,
        )

    # ------------------------------------------------------------------ internals
    def _build_answer(
        self,
        *,
        question: str,
        generated: GeneratedAnswer,
        sources: list[RetrievedSource],
        contextual_query: str,
        history_turns: int,
        elapsed_ms: float,
    ) -> RagAnswer:
        """Assemble the answer object returned to the interface."""
        return RagAnswer(
            question=question,
            answer=generated.answer,
            citations=generated.citations,
            sources=sources,
            model=generated.model,
            retrieval_query=contextual_query,
            history_turns=history_turns,
            insufficient_evidence=generated.insufficient_evidence,
            notes=list(generated.notes),
            latency_ms=elapsed_ms,
            total_tokens=generated.total_tokens,
        )

    def _summary_chunks(
        self, transcript: TranscriptRecord, normalized: NormalizedTranscript
    ) -> list[RagChunk]:
        """Load the newest stored summary and turn it into labelled chunks."""
        if not normalized.transcript_id:
            return []
        try:
            summary = self._summaries.latest_for_transcript(normalized.transcript_id)
        except SummaryStorageError:
            logger.warning("The stored summary could not be read; indexing the transcript only.")
            return []
        if summary is None:
            return []
        try:
            return self._chunker.chunk_summary(
                summary,
                video_id=normalized.video_id,
                source_name=normalized.source_name,
            )
        except ChunkingError:  # pragma: no cover - a summary always has some text
            logger.info("The stored summary produced no usable chunks.")
            return []

    @staticmethod
    def _notify(on_stage: Callable[[str], None] | None, label: str) -> None:
        """Report progress without letting a callback break the work."""
        if on_stage is None:
            return
        try:
            on_stage(label)
        except Exception:  # noqa: BLE001 - a UI callback must not fail a request
            logger.debug("Stage callback raised; continuing without progress updates.")


def _outcome_for_answer_error(error_type: str) -> RagOutcome:
    """Map an answer error type onto the published outcome enum."""
    if error_type in FATAL_ANSWER_ERROR_TYPES:
        return RagOutcome.MISSING_CREDENTIALS
    if error_type in {"invalid_output", "empty_response", "truncated"}:
        return RagOutcome.INVALID_OUTPUT
    return RagOutcome.PROVIDER_FAILED
