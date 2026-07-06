"""ChromaDB vector store wrapper for persisted coding standard embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings

COLLECTION_NAME = "coding_standards"


@dataclass(slots=True)
class VectorSearchResult:
    """One vector search result returned from ChromaDB."""

    id: str
    content: str
    metadata: dict[str, object]
    score: float


class _SentenceTransformerEmbedder:
    """Small embedding adapter around sentence-transformers."""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "sentence-transformers is required for RAG embeddings. "
                "Install backend requirements before seeding or querying RAG."
            ) from error

        self._model = SentenceTransformer(model_name, device="cpu")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with CPU-friendly sentence-transformers."""

        if not texts:
            return []

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()


class ChromaVectorStore:
    """Singleton wrapper around a ChromaDB PersistentClient collection."""

    _instance: ChromaVectorStore | None = None

    def __new__(
        cls,
        persist_path: str | Path | None = None,
        embedding_model_name: str | None = None,
    ) -> ChromaVectorStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(
        self,
        persist_path: str | Path | None = None,
        embedding_model_name: str | None = None,
    ) -> None:
        if getattr(self, "_initialized", False):
            return

        settings = get_settings()
        self.persist_path = Path(persist_path or settings.rag_chroma_path)
        self.embedding_model_name = embedding_model_name or settings.rag_embedding_model
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self._client = self._build_client(self.persist_path)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder = _SentenceTransformerEmbedder(self.embedding_model_name)
        self._initialized = True

    @property
    def collection_name(self) -> str:
        """Return the Chroma collection name."""

        return COLLECTION_NAME

    def add_documents(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, object]],
    ) -> None:
        """Embed and upsert documents into ChromaDB."""

        if not ids:
            return

        embeddings = self._embedder.embed(documents)
        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=[_clean_metadata(metadata) for metadata in metadatas],
            embeddings=embeddings,
        )

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object] | None = None,
    ) -> list[VectorSearchResult]:
        """Query ChromaDB and normalize cosine distance to a similarity score."""

        if n_results <= 0:
            return []

        query_embedding = self._embedder.embed([query])[0]
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        search_results: list[VectorSearchResult] = []
        for document_id, content, metadata, distance in zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=True,
        ):
            search_results.append(
                VectorSearchResult(
                    id=str(document_id),
                    content=str(content),
                    metadata=dict(metadata or {}),
                    score=max(0.0, 1.0 - float(distance)),
                )
            )

        return search_results

    def all_documents(self) -> list[VectorSearchResult]:
        """Return all persisted documents for rebuilding in-memory indexes."""

        result = self._collection.get(include=["documents", "metadatas"])
        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        return [
            VectorSearchResult(
                id=str(document_id),
                content=str(content),
                metadata=dict(metadata or {}),
                score=1.0,
            )
            for document_id, content, metadata in zip(
                ids,
                documents,
                metadatas,
                strict=True,
            )
        ]

    def reset_collection(self) -> None:
        """Delete and recreate the coding standards collection."""

        self._client.delete_collection(COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _build_client(self, persist_path: Path) -> Any:
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError(
                "chromadb is required for the RAG vector store. "
                "Install backend requirements before seeding or querying RAG."
            ) from error

        return chromadb.PersistentClient(path=str(persist_path))


def get_vectorstore() -> ChromaVectorStore:
    """Return the singleton Chroma vector store."""

    return ChromaVectorStore()


def _clean_metadata(metadata: dict[str, object]) -> dict[str, str | int | float | bool]:
    cleaned_metadata: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue

        if isinstance(value, str | int | float | bool):
            cleaned_metadata[key] = value
            continue

        cleaned_metadata[key] = str(value)

    return cleaned_metadata
