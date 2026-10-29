"""Local embedding provider for retrieval (Step 4).

An embedding turns a passage into a list of numbers such that passages about the
same idea end up close together. Retrieval embeds the user's question with the
*same* model and asks the vector store for the nearest passages.

Why a local model?
------------------
* No second paid API and no second key to configure.
* The transcript never leaves the machine for indexing.
* ``all-MiniLM-L6-v2`` is ~90 MB, runs on a CPU and is fast enough for a laptop.

The model is downloaded once by ``sentence-transformers`` into its local cache
(``~/.cache/huggingface`` on Linux/macOS, ``%USERPROFILE%\\.cache\\huggingface`` on
Windows) and is then reused. :func:`load_sentence_transformer` caches the loaded
model so a Streamlit rerun does not reload it.

This module implements LangChain's :class:`~langchain_core.embeddings.Embeddings`
interface, so ``langchain-chroma`` can embed documents and queries for us. Only
the chat model (NVIDIA Nemotron) is a language model - it is never used here.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from langchain_core.embeddings import Embeddings

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when the configured embedding model cannot be loaded or used."""


def embedding_model_id(settings: Settings | None = None) -> str:
    """Stable identifier of the configured embedding model.

    Stored next to every index, so a changed model is detected instead of silently
    comparing vectors from two different spaces.
    """
    resolved = settings or get_settings()
    return resolved.embedding_model_name


@lru_cache(maxsize=2)
def load_sentence_transformer(model_name: str, device: str) -> Any:
    """Load (and cache) a sentence-transformers model.

    Raises :class:`EmbeddingError` with an actionable message when the package is
    missing or the model cannot be downloaded - never falls back to fake vectors.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as error:  # noqa: BLE001 - import can fail on native deps
        logger.warning("sentence-transformers is unavailable: %s", type(error).__name__)
        raise EmbeddingError(
            "The local embedding model is unavailable because the 'sentence-transformers' "
            "package is not installed. Install the Step 4 requirements "
            "(pip install -r requirements.txt) and restart the application."
        ) from error

    try:
        model = SentenceTransformer(model_name, device=None if device == "auto" else device)
    except Exception as error:  # noqa: BLE001 - download/architecture failures
        logger.warning(
            "Embedding model %s could not be loaded: %s", model_name, type(error).__name__
        )
        raise EmbeddingError(
            f"The embedding model '{model_name}' could not be loaded. The first run downloads "
            "the model, so check your internet connection; if you are offline, pre-download it "
            "with: python -c \"from sentence_transformers import SentenceTransformer; "
            f"SentenceTransformer('{model_name}')\""
        ) from error

    logger.info("Loaded embedding model %s (device=%s).", model_name, device)
    return model


def reset_embedding_cache() -> None:
    """Drop loaded models (used by tests and by a settings change)."""
    load_sentence_transformer.cache_clear()


class LocalSentenceTransformerEmbeddings(Embeddings):
    """LangChain-compatible embeddings backed by a local transformer model."""

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        batch_size: int = 32,
    ) -> None:
        if not (model_name or "").strip():
            raise EmbeddingError("No embedding model is configured.")
        self._model_name = model_name.strip()
        self._device = device
        self._batch_size = max(1, int(batch_size))

    @property
    def model_id(self) -> str:
        """Identifier recorded in the index registry."""
        return self._model_name

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts into plain float vectors."""
        if not texts:
            return []
        if any(not (text or "").strip() for text in texts):
            raise EmbeddingError("An empty passage cannot be embedded.")
        model = load_sentence_transformer(self._model_name, self._device)
        try:
            vectors = model.encode(
                texts,
                batch_size=self._batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as error:  # noqa: BLE001 - reported, never faked
            logger.warning("Embedding failed: %s", type(error).__name__)
            raise EmbeddingError(
                "The embedding model failed while encoding the text. Try a shorter "
                "transcript, or check that the model finished downloading."
            ) from error
        return [[float(value) for value in vector] for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed transcript/summary chunks."""
        return self._encode(list(texts))

    def embed_query(self, text: str) -> list[float]:
        """Embed one question (the same model must be used for both sides)."""
        vectors = self._encode([text])
        if not vectors:  # pragma: no cover - guarded by _encode
            raise EmbeddingError("The question could not be embedded.")
        return vectors[0]


def build_embeddings(settings: Settings | None = None) -> LocalSentenceTransformerEmbeddings:
    """Create the configured embedding provider.

    Only the local provider exists today; naming it explicitly keeps a future
    remote provider a conscious choice, and prevents silently comparing vectors
    from two different models.
    """
    resolved = settings or get_settings()
    if resolved.embedding_provider != "sentence-transformers":
        raise EmbeddingError(
            f"Unsupported embedding provider '{resolved.embedding_provider}'. "
            "Only 'sentence-transformers' is implemented."
        )
    return LocalSentenceTransformerEmbeddings(
        resolved.embedding_model_name,
        device=resolved.embedding_device,
        batch_size=resolved.embedding_batch_size,
    )
