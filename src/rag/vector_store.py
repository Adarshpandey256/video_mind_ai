"""Persistent ChromaDB storage for retrieval (Step 4).

Isolation strategy
------------------
**One Chroma collection per video *and* per transcript version.** The collection
name is derived from the stable Step 2 ``video_id`` plus a short hash of the
transcript content, so:

* a question can only ever search the active video's collection (structural
  isolation), and every query *also* filters on ``video_id`` as a second guard;
* re-indexing an unchanged transcript reuses the existing collection instead of
  duplicating it;
* a changed transcript builds a **new** collection first, then switches the
  registry entry, and only then deletes the old collection - so a failed rebuild
  never destroys a working index.

A tiny JSON registry (``chroma_data/index_registry.json``) records which
collection is active for each video, together with the embedding model, the
transcript hash and the chunk counts. Chroma keeps the vectors; the registry
keeps the bookkeeping that Chroma's API does not expose conveniently.

Only scalar metadata reaches Chroma (no ``None``, no nested objects), because
Chroma rejects those values.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.settings import Settings, get_settings
from src.models.rag_schemas import (
    DocumentType,
    IndexInfo,
    IndexStatus,
    RagChunk,
    RetrievedSource,
)
from src.rag.embeddings import (
    EmbeddingError,
    LocalSentenceTransformerEmbeddings,
    build_embeddings,
    embedding_model_id,
)

logger = logging.getLogger(__name__)

REGISTRY_SCHEMA_VERSION = 1
COLLECTION_PREFIX = "videomind"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
#: Add in batches so a long recording does not build one huge request.
INDEX_BATCH_SIZE = 64


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be read or written."""


class IndexNotReadyError(VectorStoreError):
    """Raised when a video has no usable index (not built, stale or mismatched)."""


@dataclass(frozen=True)
class IndexRecord:
    """What the registry remembers about one video's active collection."""

    video_id: str
    collection_name: str
    transcript_hash: str
    embedding_model: str
    chunk_count: int = 0
    transcript_chunks: int = 0
    summary_chunks: int = 0
    transcript_id: str | None = None
    title: str | None = None
    source_name: str | None = None
    indexed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON representation."""
        return {
            "video_id": self.video_id,
            "collection_name": self.collection_name,
            "transcript_hash": self.transcript_hash,
            "embedding_model": self.embedding_model,
            "chunk_count": self.chunk_count,
            "transcript_chunks": self.transcript_chunks,
            "summary_chunks": self.summary_chunks,
            "transcript_id": self.transcript_id,
            "title": self.title,
            "source_name": self.source_name,
            "indexed_at": self.indexed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexRecord":
        """Rebuild a record, tolerating missing optional fields."""
        return cls(
            video_id=str(data["video_id"]),
            collection_name=str(data["collection_name"]),
            transcript_hash=str(data.get("transcript_hash", "")),
            embedding_model=str(data.get("embedding_model", "")),
            chunk_count=int(data.get("chunk_count", 0) or 0),
            transcript_chunks=int(data.get("transcript_chunks", 0) or 0),
            summary_chunks=int(data.get("summary_chunks", 0) or 0),
            transcript_id=data.get("transcript_id"),
            title=data.get("title"),
            source_name=data.get("source_name"),
            indexed_at=str(data.get("indexed_at") or ""),
        )

    def to_info(self, *, status: IndexStatus, message: str | None = None) -> IndexInfo:
        """Describe this record for the interface."""
        return IndexInfo(
            video_id=self.video_id,
            status=status,
            transcript_id=self.transcript_id,
            title=self.title,
            source_name=self.source_name,
            chunk_count=self.chunk_count,
            transcript_chunks=self.transcript_chunks,
            summary_chunks=self.summary_chunks,
            embedding_model=self.embedding_model,
            transcript_hash=self.transcript_hash,
            collection_name=self.collection_name,
            indexed_at=_parse_datetime(self.indexed_at),
            message=message,
        )


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, returning ``None`` when it is unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class IndexRegistry:
    """Small, atomically written JSON index of active collections."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Location of the registry document."""
        return self._path

    def load(self) -> dict[str, IndexRecord]:
        """Read every valid entry; a damaged file is reported, not fatal."""
        if not self._path.is_file():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("The retrieval index registry could not be read; treating it as empty.")
            return {}
        if not isinstance(payload, dict):
            return {}
        entries: dict[str, IndexRecord] = {}
        for video_id, raw in (payload.get("videos") or {}).items():
            if not isinstance(raw, dict):
                continue
            try:
                entries[str(video_id)] = IndexRecord.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                logger.info("Skipping unreadable index registry entry for %s.", video_id)
        return entries

    def get(self, video_id: str) -> IndexRecord | None:
        """Return the active record for a video, if any."""
        return self.load().get(str(video_id))

    def put(self, record: IndexRecord) -> None:
        """Store (or replace) one video's active record."""
        entries = self.load()
        entries[record.video_id] = record
        self._write(entries)

    def remove(self, video_id: str) -> bool:
        """Forget one video; returns True when an entry existed."""
        entries = self.load()
        if entries.pop(str(video_id), None) is None:
            return False
        self._write(entries)
        return True

    def all(self) -> list[IndexRecord]:
        """Every active record, newest first."""
        records = list(self.load().values())
        records.sort(key=lambda item: item.indexed_at, reverse=True)
        return records

    def _write(self, entries: dict[str, IndexRecord]) -> None:
        """Write the registry atomically (temporary file + ``os.replace``)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "videos": {video_id: record.to_dict() for video_id, record in entries.items()},
        }
        temporary: str | None = None
        try:
            handle_fd, temporary = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".index-registry-", suffix=".tmp"
            )
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            temporary = None
        except OSError as error:
            logger.warning("Index registry write failed: %s", type(error).__name__)
            raise VectorStoreError(
                "The retrieval index registry could not be saved. Check that the data "
                "directory is writable and has free space."
            ) from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


@lru_cache(maxsize=4)
def _persistent_client(path: str) -> Any:
    """Open (and reuse) one persistent Chroma client per directory."""
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except Exception as error:  # noqa: BLE001 - native import may fail
        raise VectorStoreError(
            "The vector database is unavailable because 'chromadb' is not installed. "
            "Install the Step 4 requirements (pip install -r requirements.txt)."
        ) from error

    try:
        return chromadb.PersistentClient(
            path=path,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
    except Exception as error:  # noqa: BLE001 - corrupt or locked directory
        logger.warning("Chroma could not be opened: %s", type(error).__name__)
        raise VectorStoreError(
            "The local vector database could not be opened. Check that the ChromaDB "
            "directory is writable and is not locked by another program."
        ) from error


def reset_vector_store_cache() -> None:
    """Close cached Chroma clients (tests and embedding-model changes)."""
    _persistent_client.cache_clear()


def collection_name_for(video_id: str, transcript_hash: str) -> str:
    """Deterministic, Chroma-safe collection name for one video version."""
    if not VIDEO_ID_PATTERN.match(str(video_id or "")):
        raise VectorStoreError("The video identifier is not valid.")
    slug = re.sub(r"[^a-z0-9]+", "-", video_id.lower()).strip("-")[:24] or "video"
    suffix = re.sub(r"[^a-zA-Z0-9]", "", transcript_hash)[:10] or "0"
    name = f"{COLLECTION_PREFIX}-{slug}-{suffix}"
    # Chroma requires 3-63 characters, starting/ending with an alphanumeric.
    return name[:63].rstrip("-")


class ChromaVectorStore:
    """Build, query and delete per-video vector indexes."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embeddings: LocalSentenceTransformerEmbeddings | None = None,
        registry: IndexRegistry | None = None,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._embeddings = embeddings
        self._registry = registry or IndexRegistry(self._settings.rag_registry_file)
        self._client = client

    # ------------------------------------------------------------------ properties
    @property
    def settings(self) -> Settings:
        """Return the settings that control the store."""
        return self._settings

    @property
    def registry(self) -> IndexRegistry:
        """Return the index registry."""
        return self._registry

    @property
    def embeddings(self) -> LocalSentenceTransformerEmbeddings:
        """Return the configured embedding provider (lazily created)."""
        if self._embeddings is None:
            self._embeddings = build_embeddings(self._settings)
        return self._embeddings

    @property
    def model_id(self) -> str:
        """Identifier of the embedding model in use."""
        return getattr(self.embeddings, "model_id", embedding_model_id(self._settings))

    @property
    def client(self) -> Any:
        """Return the persistent Chroma client (opened on first use)."""
        if self._client is None:
            self._client = _persistent_client(str(self._settings.chroma_dir))
        return self._client

    # ------------------------------------------------------------------ building
    def build_index(
        self,
        *,
        video_id: str,
        chunks: list[RagChunk],
        transcript_hash: str,
        transcript_id: str | None = None,
        title: str | None = None,
        source_name: str | None = None,
    ) -> IndexInfo:
        """Embed and store the chunks, then make the new collection active.

        Raises :class:`IndexNotReadyError`/`VectorStoreError` on failure. The
        previously active collection is only deleted after the new one is complete.
        """
        if not chunks:
            raise VectorStoreError("There are no chunks to index.")

        collection_name = collection_name_for(video_id, transcript_hash)
        previous = self._registry.get(video_id)
        model_id = self.model_id

        # A leftover collection with the same name can only come from an interrupted
        # build of the same transcript version, so replacing it is safe.
        self._delete_collection(collection_name, missing_ok=True)

        try:
            store = self._open_store(collection_name)
            for start in range(0, len(chunks), INDEX_BATCH_SIZE):
                batch = chunks[start : start + INDEX_BATCH_SIZE]
                store.add_texts(
                    texts=[chunk.text for chunk in batch],
                    metadatas=[chunk.metadata() for chunk in batch],
                    ids=[chunk.chunk_id for chunk in batch],
                )
            stored = int(self._collection_count(collection_name))
        except Exception as error:  # noqa: BLE001 - translated below
            logger.warning("Index build failed: %s", type(error).__name__)
            self._delete_collection(collection_name, missing_ok=True)
            if isinstance(error, VectorStoreError):
                raise
            raise VectorStoreError(
                "The transcript could not be embedded and stored. The previous index, if "
                "any, was left untouched. Check the logs for the technical details."
            ) from error

        if stored != len(chunks):
            self._delete_collection(collection_name, missing_ok=True)
            raise VectorStoreError(
                f"The vector store reported {stored} of {len(chunks)} chunks, so the index "
                "is incomplete. It was discarded instead of being used."
            )

        transcript_chunks = sum(
            1 for chunk in chunks if chunk.document_type is DocumentType.TRANSCRIPT
        )
        record = IndexRecord(
            video_id=video_id,
            collection_name=collection_name,
            transcript_hash=transcript_hash,
            embedding_model=model_id,
            chunk_count=len(chunks),
            transcript_chunks=transcript_chunks,
            summary_chunks=len(chunks) - transcript_chunks,
            transcript_id=transcript_id,
            title=title,
            source_name=source_name,
        )
        self._registry.put(record)

        if previous is not None and previous.collection_name != collection_name:
            self._delete_collection(previous.collection_name, missing_ok=True)

        logger.info(
            "Indexed %s chunk(s) for %s into %s.",
            len(chunks),
            video_id,
            collection_name,
        )
        return record.to_info(status=IndexStatus.READY)

    # ------------------------------------------------------------------ reading
    def status(self, video_id: str, *, transcript_hash: str | None = None) -> IndexInfo:
        """Return the honest state of a video's index."""
        try:
            record = self._registry.get(video_id)
        except VectorStoreError as error:
            return IndexInfo(
                video_id=video_id, status=IndexStatus.UNAVAILABLE, message=str(error)
            )
        if record is None:
            return IndexInfo(video_id=video_id, status=IndexStatus.NOT_INDEXED)

        if record.embedding_model and record.embedding_model != self.model_id:
            return record.to_info(
                status=IndexStatus.EMBEDDING_MISMATCH,
                message=(
                    f"This index was built with {record.embedding_model}, but the configured "
                    f"model is {self.model_id}. Rebuild the index to compare vectors from the "
                    "same model."
                ),
            )

        if transcript_hash and transcript_hash != record.transcript_hash:
            return record.to_info(status=IndexStatus.STALE)

        try:
            count = self._collection_count(record.collection_name)
        except Exception:  # noqa: BLE001 - a missing collection is a state, not a crash
            logger.info("Collection %s is no longer present.", record.collection_name)
            return IndexInfo(
                video_id=video_id,
                status=IndexStatus.NOT_INDEXED,
                message=(
                    "The stored index is missing from the vector database. Rebuild it to ask "
                    "questions again."
                ),
            )
        if count <= 0:
            return IndexInfo(
                video_id=video_id,
                status=IndexStatus.NOT_INDEXED,
                message="The stored index is empty. Rebuild it to ask questions again.",
            )
        return record.to_info(status=IndexStatus.READY)

    def search(
        self,
        video_id: str,
        query: str,
        *,
        k: int = 4,
        document_types: list[DocumentType] | None = None,
        transcript_hash: str | None = None,
    ) -> list[RetrievedSource]:
        """Return the closest passages for ``query`` from this video only."""
        question = (query or "").strip()
        if not question:
            raise VectorStoreError("Enter a question first.")

        record = self._registry.get(video_id)
        if record is None:
            raise IndexNotReadyError("This video has not been indexed yet.")

        if record.embedding_model and record.embedding_model != self.model_id:
            raise IndexNotReadyError(
                "The index was built with a different embedding model. Rebuild it first."
            )
        if transcript_hash and transcript_hash != record.transcript_hash:
            raise IndexNotReadyError(
                "The transcript changed since this index was built. Rebuild the index first."
            )

        where: dict[str, Any] = {"video_id": video_id}
        if document_types:
            where = {
                "$and": [
                    {"video_id": video_id},
                    {"document_type": {"$in": [item.value for item in document_types]}},
                ]
            }

        try:
            store = self._open_store(record.collection_name)
            results = store.similarity_search_with_score(question, k=max(1, int(k)), filter=where)
        except Exception as error:  # noqa: BLE001 - translated below
            logger.warning("Vector search failed: %s", type(error).__name__)
            if isinstance(error, VectorStoreError):
                raise
            raise VectorStoreError(
                "The vector search failed. Rebuild the index from this page and try again."
            ) from error

        sources: list[RetrievedSource] = []
        for document, score in results:
            metadata = dict(document.metadata or {})
            distance = float(score) if score is not None else None
            sources.append(
                RetrievedSource(
                    source_ref="",
                    chunk_id=str(metadata.get("chunk_id") or metadata.get("text_hash") or ""),
                    video_id=str(metadata.get("video_id") or video_id),
                    chunk_index=int(metadata.get("chunk_index") or 0),
                    document_type=DocumentType(
                        metadata.get("document_type") or DocumentType.TRANSCRIPT.value
                    ),
                    text=document.page_content,
                    source_name=metadata.get("source_name"),
                    start_seconds=metadata.get("start_seconds"),
                    end_seconds=metadata.get("end_seconds"),
                    distance=distance,
                    similarity=_similarity_from_distance(distance),
                )
            )
        return sources

    def count(self, video_id: str) -> int:
        """Number of stored chunks for a video (0 when not indexed)."""
        record = self._registry.get(video_id)
        if record is None:
            return 0
        try:
            return self._collection_count(record.collection_name)
        except Exception:  # noqa: BLE001 - reported as zero by the caller
            return 0

    def delete(self, video_id: str) -> bool:
        """Delete a video's index (collection + registry entry)."""
        record = self._registry.get(video_id)
        if record is None:
            return False
        self._delete_collection(record.collection_name, missing_ok=True)
        removed = self._registry.remove(video_id)
        logger.info("Deleted the index for %s.", video_id)
        return removed

    # ------------------------------------------------------------------ internals
    def _open_store(self, collection_name: str) -> Any:
        """Create a LangChain Chroma handle for one collection."""
        try:
            from langchain_chroma import Chroma
        except Exception as error:  # noqa: BLE001 - optional dependency
            raise VectorStoreError(
                "The LangChain Chroma integration is unavailable. Install the Step 4 "
                "requirements (pip install -r requirements.txt)."
            ) from error

        try:
            return Chroma(
                collection_name=collection_name,
                embedding_function=self.embeddings,
                client=self.client,
                # Cosine distance is required by _similarity_from_distance; the raw
                # distance is stored with every result so the conversion is checkable.
                collection_metadata={"hnsw:space": "cosine"},
            )
        except Exception as error:  # noqa: BLE001 - translated below
            if isinstance(error, (EmbeddingError, VectorStoreError)):
                raise
            raise VectorStoreError(
                "The vector collection could not be opened. It may have been built by an "
                "incompatible version of ChromaDB - rebuild the index."
            ) from error

    def _collection_count(self, collection_name: str) -> int:
        """Count the vectors in a collection (0 when it does not exist)."""
        try:
            collection = self.client.get_collection(collection_name)
        except Exception:  # noqa: BLE001 - chroma raises several unrelated types
            return 0
        return int(collection.count())

    def _delete_collection(self, collection_name: str, *, missing_ok: bool) -> None:
        """Delete one collection, ignoring an absent one when allowed."""
        try:
            self.client.delete_collection(collection_name)
        except Exception as error:  # noqa: BLE001 - absence is expected
            if not missing_ok:
                raise VectorStoreError(
                    "The stored vector collection could not be deleted. Close the application "
                    "using it and try again."
                ) from error
            logger.debug("Collection %s was already absent.", collection_name)


def _similarity_from_distance(distance: float | None) -> float | None:
    """Convert a cosine distance (0 = identical) into a 0-1 similarity.

    Chroma's default ``l2`` space would need a different conversion, which is why
    every collection is created with ``hnsw:space = cosine``. The raw distance is
    kept next to the similarity so the interface never hides the real score.
    """
    if distance is None:
        return None
    return max(0.0, min(1.0, 1.0 - distance))
