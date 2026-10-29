"""Retrieval augmented generation over Step 2 transcripts (Step 4).

The pipeline is split into small pieces so each one can be tested alone:

``transcript_adapter`` -> ``chunking`` -> ``embeddings`` -> ``vector_store``
-> ``retriever`` -> ``answer_generator``

:class:`~src.rag.service.RagService` is the public entry point used by the
Streamlit page (and by later steps):

    service.index_transcript(transcript)        # build or reuse the index
    service.ask(question, transcript, history)  # grounded, cited answer
"""

from src.rag.answer_generator import AnswerGenerationError, GroundedAnswerGenerator
from src.rag.chunking import ChunkingError, TranscriptChunker, summary_text
from src.rag.embeddings import (
    EmbeddingError,
    LocalSentenceTransformerEmbeddings,
    build_embeddings,
    embedding_model_id,
    reset_embedding_cache,
)
from src.rag.prompts import SYSTEM_PROMPT, build_answer_user_prompt, render_sources
from src.rag.retriever import RetrievalError, Retriever, contextual_query
from src.rag.service import RagService
from src.rag.transcript_adapter import (
    NormalizedSegment,
    NormalizedTranscript,
    TranscriptNormalizationError,
    normalize_transcript,
)
from src.rag.vector_store import (
    ChromaVectorStore,
    IndexNotReadyError,
    IndexRecord,
    IndexRegistry,
    VectorStoreError,
    collection_name_for,
    reset_vector_store_cache,
)

__all__ = [
    "SYSTEM_PROMPT",
    "AnswerGenerationError",
    "ChromaVectorStore",
    "ChunkingError",
    "EmbeddingError",
    "GroundedAnswerGenerator",
    "IndexNotReadyError",
    "IndexRecord",
    "IndexRegistry",
    "LocalSentenceTransformerEmbeddings",
    "NormalizedSegment",
    "NormalizedTranscript",
    "RagService",
    "RetrievalError",
    "Retriever",
    "TranscriptChunker",
    "TranscriptNormalizationError",
    "VectorStoreError",
    "build_answer_user_prompt",
    "build_embeddings",
    "collection_name_for",
    "contextual_query",
    "embedding_model_id",
    "normalize_transcript",
    "render_sources",
    "reset_embedding_cache",
    "reset_vector_store_cache",
    "summary_text",
]
