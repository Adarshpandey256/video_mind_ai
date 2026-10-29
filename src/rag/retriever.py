"""Retrieval for the video chatbot (Step 4).

The retriever turns a question into evidence:

1. the query is embedded with the same local model that embedded the chunks;
2. the video's own Chroma collection is searched (never another video's);
3. duplicate and over-long result sets are trimmed to fit the context budget;
4. every passage keeps its chunk id and, when it exists, its transcript timestamp.

Score semantics are explicit: collections use **cosine distance** (0 = identical,
larger = further apart). :attr:`RetrievedSource.similarity` is ``1 - distance``
clamped to 0-1, and the raw distance is kept too. No universal "relevance
threshold" is applied, because a fixed cut-off is meaningless across models and
questions; the answer prompt decides whether the evidence is sufficient.
"""

from __future__ import annotations

import hashlib
import logging

from config.settings import Settings, get_settings
from src.models.rag_schemas import ChatTurn, DocumentType, RetrievedSource
from src.rag.vector_store import ChromaVectorStore, IndexNotReadyError, VectorStoreError

logger = logging.getLogger(__name__)

#: Short or anaphoric questions only make sense with the previous turn in mind.
#: These words refer back to something already named, so they always need context.
_ANAPHORIC_STARTS = {
    "it",
    "its",
    "that",
    "this",
    "they",
    "them",
    "those",
    "these",
    "he",
    "she",
    "him",
    "her",
    "there",
    "then",
    "why",
    "and",
    "but",
    "also",
    "so",
    "more",
}
#: A bare request ("explain", "continue") only needs context while it stays short;
#: once the user names the subject themselves the question stands on its own.
_SHORT_REQUEST_STARTS = {
    "explain",
    "elaborate",
    "continue",
    "summarise",
    "summarize",
    "repeat",
}
_SHORT_REQUEST_WORDS = 6
_ANAPHORIC_PHRASES = ("what about", "how about", "tell me more", "go on", "and then")


class RetrievalError(RuntimeError):
    """Raised when a question cannot be turned into evidence."""


def needs_conversation_context(question: str) -> bool:
    """True when a question is too short or too referential to stand alone.

    This is a deliberately simple, deterministic heuristic: it avoids an extra
    model call per question, and a wrong guess only widens the retrieval query -
    it never changes the question that is answered.
    """
    text = (question or "").strip().lower().rstrip("?.!")
    if not text:
        return False
    words = text.split()
    if len(words) <= 4:
        return True
    if text.startswith(_ANAPHORIC_PHRASES):
        return True
    if words[0] in _ANAPHORIC_STARTS:
        return True
    return words[0] in _SHORT_REQUEST_STARTS and len(words) <= _SHORT_REQUEST_WORDS


def contextual_query(history: list[ChatTurn], question: str) -> str:
    """Widen a follow-up question with the previous question for retrieval only."""
    cleaned = (question or "").strip()
    if not history or not cleaned or not needs_conversation_context(cleaned):
        return cleaned
    previous = (history[-1].question or "").strip()
    if not previous:
        return cleaned
    return f"{previous} {cleaned}".strip()


class Retriever:
    """Search one video's index and return ranked, de-duplicated evidence."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: ChromaVectorStore | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._store = store or ChromaVectorStore(self._settings)

    @property
    def store(self) -> ChromaVectorStore:
        """Return the vector store used for searches."""
        return self._store

    def retrieve(
        self,
        video_id: str,
        query: str,
        *,
        top_k: int | None = None,
        include_summary: bool | None = None,
        transcript_hash: str | None = None,
        extra_queries: list[str] | None = None,
    ) -> list[RetrievedSource]:
        """Return the best passages for ``query``, scoped to ``video_id``.

        ``extra_queries`` lets a follow-up be searched with both its widened and
        its literal form; results are merged by chunk id, keeping the best score.
        """
        question = (query or "").strip()
        if not question:
            raise RetrievalError("Enter a question first.")

        resolved_top_k = max(1, int(top_k or self._settings.retrieval_top_k))
        with_summary = (
            self._settings.retrieval_include_summary
            if include_summary is None
            else bool(include_summary)
        )
        document_types = (
            [DocumentType.TRANSCRIPT, DocumentType.SUMMARY]
            if with_summary
            else [DocumentType.TRANSCRIPT]
        )

        queries = [question]
        for extra in extra_queries or []:
            clean = (extra or "").strip()
            if clean and clean not in queries:
                queries.append(clean)

        merged: dict[str, RetrievedSource] = {}
        for active_query in queries:
            try:
                results = self.store.search(
                    video_id,
                    active_query,
                    k=resolved_top_k,
                    document_types=document_types,
                    transcript_hash=transcript_hash,
                )
            except IndexNotReadyError:
                raise
            except VectorStoreError as error:
                raise RetrievalError(str(error)) from error
            for source in results:
                key = source.chunk_id or f"{source.chunk_index}:{source.text[:80]}"
                current = merged.get(key)
                if current is None or _rank(source) < _rank(current):
                    merged[key] = source

        ranked = sorted(merged.values(), key=_rank)
        selected = self._within_context_budget(ranked, top_k=resolved_top_k)
        for index, source in enumerate(selected):
            source.source_ref = f"S{index + 1}"

        logger.info(
            "Retrieved %s passage(s) for %s (top_k=%s, summary=%s).",
            len(selected),
            video_id,
            resolved_top_k,
            with_summary,
        )
        return selected

    def _within_context_budget(
        self, sources: list[RetrievedSource], *, top_k: int
    ) -> list[RetrievedSource]:
        """Keep the best passages that fit the context and are not duplicates."""
        budget = self._settings.retrieval_max_context_chars
        selected: list[RetrievedSource] = []
        seen_text: set[str] = set()
        used = 0
        for source in sources:
            if len(selected) >= top_k:
                break
            text = (source.text or "").strip()
            if not text:
                continue
            fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()
            if fingerprint in seen_text:
                continue
            cost = len(text)
            if selected and used + cost > budget:
                continue
            selected.append(source)
            seen_text.add(fingerprint)
            used += cost
        return selected


def _rank(source: RetrievedSource) -> float:
    """Sort key: cosine distance when known, otherwise "worst"."""
    return source.distance if source.distance is not None else float("inf")
