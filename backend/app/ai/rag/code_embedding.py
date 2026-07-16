"""ChromaDB storage and embeddings for repository source-code chunks."""

from __future__ import annotations

from dataclasses import dataclass
import gc
import json
import logging
from pathlib import Path
import time
from typing import Any, Protocol

from app.core.config import get_settings
from app.schemas.mongodb import ChunkMetadataDocument

logger = logging.getLogger(__name__)

CODE_CHUNKS_COLLECTION = "code_chunks"
ALLOWED_CODE_EMBEDDING_MODELS = frozenset({"jinaai/jina-embeddings-v2-base-code"})
CODE_EMBEDDING_MODEL_VERSION = "v2-base-code"
CODE_EMBEDDING_DIMENSION = 768
DEFAULT_CODE_EMBEDDING_MAX_SEQUENCE_LENGTH = 1024
REMOTE_CODE_EMBEDDING_MODEL_VERSION = "openai-compatible"
UNKNOWN_CODE_EMBEDDING_DIMENSION = 0


@dataclass(slots=True, frozen=True)
class CodeVectorSearchResult:
    """One source-code chunk returned by the vector index."""

    content: str
    metadata: dict[str, object]
    score: float


@dataclass(slots=True, frozen=True)
class CodeEmbeddingBatchTrace:
    """Timing and token metadata for one embedding batch."""

    batch_number: int
    total_batches: int
    file_paths: list[str]
    chunk_count: int
    duration_ms: int
    estimated_input_tokens: int
    token_usage: dict[str, int] | None = None


@dataclass(slots=True, frozen=True)
class CodeEmbeddingIndexSummary:
    """Summary returned after indexing source-code chunks."""

    indexed_count: int
    duration_ms: int
    batches: list[CodeEmbeddingBatchTrace]


class CodeEmbedder(Protocol):
    """Embedding adapter used by the source-code vector store."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class CodeEmbeddingStore:
    """Embed full source chunks and persist them in a job-scoped collection."""

    def __init__(
        self,
        *,
        persist_path: str | Path | None = None,
        model_name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        dimension: int | None = None,
        batch_size: int | None = None,
        max_sequence_length: int | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = provider or settings.code_embedding_provider
        self.model_name = model_name or settings.code_embedding_model
        self.dimension = (
            settings.code_embedding_dimension if dimension is None else dimension
        )
        self.batch_size = batch_size or settings.code_embedding_batch_size
        self.max_sequence_length = (
            settings.code_embedding_max_sequence_length
            if max_sequence_length is None
            else max_sequence_length
        )
        self._validate_provider_config()

        self.persist_path = Path(persist_path or settings.rag_chroma_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self._client = _build_chroma_client(self.persist_path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name(),
            metadata=self._collection_metadata(),
        )
        self._validate_collection_model()
        self._embedder = self._build_embedder(
            settings=settings,
            base_url=base_url,
            api_key=api_key,
        )

    def index_chunks(
        self,
        chunks: list[ChunkMetadataDocument],
    ) -> CodeEmbeddingIndexSummary:
        """Embed and upsert exact, full chunk documents."""

        indexable_chunks = [
            chunk for chunk in chunks if isinstance(chunk.chunk_text, str)
        ]
        if not indexable_chunks:
            logger.info("No source chunks available for semantic code indexing")
            return CodeEmbeddingIndexSummary(
                indexed_count=0,
                duration_ms=0,
                batches=[],
            )

        started_at = time.perf_counter()
        logger.info(
            "Semantic code indexing preparing %d chunks with provider=%s model=%s "
            "batch_size=%d max_sequence_length=%d",
            len(indexable_chunks),
            self.provider,
            self.model_name,
            self.batch_size,
            self.max_sequence_length,
        )

        total_batches = _count_batches(len(indexable_chunks), self.batch_size)
        indexed_count = 0
        batch_traces: list[CodeEmbeddingBatchTrace] = []
        for batch_number, batch_chunks in enumerate(
            _batched(indexable_chunks, self.batch_size),
            start=1,
        ):
            batch_trace = self._index_chunk_batch(
                batch_chunks,
                batch_number=batch_number,
                total_batches=total_batches,
            )
            indexed_count += batch_trace.chunk_count
            batch_traces.append(batch_trace)

        duration_ms = _duration_ms(started_at)
        logger.info(
            "Semantic code indexing finished for %d chunks in Chroma collection %s "
            "in %.2fs",
            indexed_count,
            self._collection_name(),
            duration_ms / 1000,
        )
        return CodeEmbeddingIndexSummary(
            indexed_count=indexed_count,
            duration_ms=duration_ms,
            batches=batch_traces,
        )

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]:
        """Search source chunks using an explicit metadata filter."""

        if n_results <= 0:
            return []

        query_embedding = _validated_embeddings(
            [self._embedder.embed_query(query)],
            expected_dimension=self._expected_dimension(),
        )[0]
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            CodeVectorSearchResult(
                content=str(content),
                metadata=dict(metadata or {}),
                score=max(0.0, 1.0 - float(distance)),
            )
            for content, metadata, distance in zip(
                documents,
                metadatas,
                distances,
                strict=True,
            )
        ]

    def delete_job(self, job_id: str) -> None:
        """Delete only source vectors belonging to one completed review job."""

        normalized_job_id = job_id.strip()
        if not normalized_job_id:
            raise ValueError("job_id is required to clean code_chunks")

        self._collection.delete(where={"job_id": normalized_job_id})

    def _validate_collection_model(self) -> None:
        metadata = self._collection.metadata or {}
        expected_metadata: dict[str, str | int] = {
            "embedding_model": self.model_name,
            "embedding_model_version": self._model_version(),
        }
        if self.provider != "local":
            expected_metadata["embedding_provider"] = self.provider

        expected_dimension = self._expected_dimension()
        if expected_dimension is not None:
            expected_metadata["embedding_dimension"] = expected_dimension

        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise RuntimeError(
                "code_chunks has incompatible embedding model metadata; "
                "rebuild the collection before indexing or searching"
            )

    def _validate_provider_config(self) -> None:
        if self.provider not in {"local", "openai", "nvidia"}:
            raise ValueError(f"Unsupported code embedding provider: {self.provider}")

        if (
            self.provider == "local"
            and self.model_name not in ALLOWED_CODE_EMBEDDING_MODELS
        ):
            raise ValueError(
                f"Code embedding model is not allowlisted: {self.model_name}"
            )

        if self.batch_size <= 0:
            raise ValueError("code_embedding_batch_size must be greater than zero")

        if self.max_sequence_length < 0:
            raise ValueError(
                "code_embedding_max_sequence_length must be zero or greater"
            )

        if self.dimension < UNKNOWN_CODE_EMBEDDING_DIMENSION:
            raise ValueError("code_embedding_dimension must be zero or greater")

    def _build_embedder(
        self,
        *,
        settings: Any,
        base_url: str | None,
        api_key: str | None,
    ) -> CodeEmbedder:
        if self.provider == "local":
            return _SentenceTransformerCodeEmbedder(
                self.model_name,
                max_sequence_length=self.max_sequence_length,
            )

        resolved_api_key = api_key or _code_embedding_api_key(
            provider=self.provider,
            settings=settings,
        )
        resolved_base_url = base_url or _code_embedding_base_url(
            provider=self.provider,
            settings=settings,
        )
        return _OpenAICompatibleCodeEmbedder(
            provider=self.provider,
            model_name=self.model_name,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
        )

    def _index_chunk_batch(
        self,
        chunks: list[ChunkMetadataDocument],
        *,
        batch_number: int,
        total_batches: int,
    ) -> CodeEmbeddingBatchTrace:
        documents = [str(chunk.chunk_text) for chunk in chunks]
        batch_started_at = time.perf_counter()
        logger.info(
            "Generating semantic code embedding batch %d/%d with %d documents",
            batch_number,
            total_batches,
            len(documents),
        )
        embeddings = _validated_embeddings(
            self._embedder.embed_documents(documents),
            expected_dimension=self._expected_dimension(),
        )
        token_usage = getattr(self._embedder, "last_token_usage", None)
        if len(embeddings) != len(chunks):
            raise RuntimeError("Code embedding model returned an unexpected batch size")

        logger.info(
            "Generated semantic code embedding batch %d/%d in %.2fs",
            batch_number,
            total_batches,
            time.perf_counter() - batch_started_at,
        )
        upsert_started_at = time.perf_counter()
        self._collection.upsert(
            ids=[_chunk_id(chunk) for chunk in chunks],
            documents=documents,
            metadatas=[_chunk_metadata(chunk) for chunk in chunks],
            embeddings=embeddings,
        )
        logger.info(
            "Upserted semantic code embedding batch %d/%d to Chroma in %.2fs",
            batch_number,
            total_batches,
            time.perf_counter() - upsert_started_at,
        )
        batch_trace = CodeEmbeddingBatchTrace(
            batch_number=batch_number,
            total_batches=total_batches,
            file_paths=sorted({chunk.file_path for chunk in chunks}),
            chunk_count=len(chunks),
            duration_ms=_duration_ms(batch_started_at),
            estimated_input_tokens=sum(chunk.token_count for chunk in chunks),
            token_usage=token_usage if isinstance(token_usage, dict) else None,
        )
        del embeddings
        del documents
        gc.collect()
        return batch_trace

    def _collection_name(self) -> str:
        if self.provider == "local":
            return CODE_CHUNKS_COLLECTION

        safe_model_name = "".join(
            character if character.isalnum() else "_"
            for character in self.model_name.lower()
        ).strip("_")
        return f"{CODE_CHUNKS_COLLECTION}_{self.provider}_{safe_model_name}"[:63]

    def _collection_metadata(self) -> dict[str, str | int]:
        metadata: dict[str, str | int] = {
            "hnsw:space": "cosine",
            "embedding_model": self.model_name,
            "embedding_model_version": self._model_version(),
        }
        if self.provider != "local":
            metadata["embedding_provider"] = self.provider

        expected_dimension = self._expected_dimension()
        if expected_dimension is not None:
            metadata["embedding_dimension"] = expected_dimension

        return metadata

    def _model_version(self) -> str:
        if self.provider == "local":
            return CODE_EMBEDDING_MODEL_VERSION

        return REMOTE_CODE_EMBEDDING_MODEL_VERSION

    def _expected_dimension(self) -> int | None:
        if self.dimension == UNKNOWN_CODE_EMBEDDING_DIMENSION:
            return None

        return self.dimension


class DisabledCodeEmbeddingStore(CodeEmbeddingStore):
    """No-op code vector store used when semantic code search is disabled."""

    def __init__(self) -> None:
        """Avoid loading Chroma or embedding models when semantic search is off."""

    def index_chunks(
        self,
        chunks: list[ChunkMetadataDocument],
    ) -> CodeEmbeddingIndexSummary:
        """Skip source-code embedding while preserving normal chunk persistence."""

        logger.info(
            "Semantic code search disabled; skipping vector indexing for %d chunks",
            len(chunks),
        )
        return CodeEmbeddingIndexSummary(indexed_count=0, duration_ms=0, batches=[])

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]:
        """Return no semantic matches when the optional index is disabled."""

        _ = query, n_results, where
        return []

    def delete_job(self, job_id: str) -> None:
        """No-op cleanup for disabled code vector storage."""

        _ = job_id


class _SentenceTransformerCodeEmbedder:
    """Local sentence-transformers adapter for code embeddings."""

    def __init__(self, model_name: str, *, max_sequence_length: int) -> None:
        self._model = _build_sentence_transformer(model_name)
        if max_sequence_length > 0:
            self._model.max_seq_length = max_sequence_length
            logger.info(
                "Configured local code embedding max_seq_length=%d",
                max_sequence_length,
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _validated_embeddings(
            self._model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist(),
            expected_dimension=CODE_EMBEDDING_DIMENSION,
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class _OpenAICompatibleCodeEmbedder:
    """Remote embedding adapter for OpenAI-compatible APIs such as NVIDIA NIM."""

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        api_key: str,
        base_url: str,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.last_token_usage: dict[str, int] | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="passage")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], input_type="query")[0]

    def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []

        try:
            import httpx
        except ImportError as error:
            raise RuntimeError(
                "httpx is required for remote code semantic search embeddings"
            ) from error

        payload: dict[str, object] = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float",
        }
        if self.provider == "nvidia":
            payload["input_type"] = input_type

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self.headers,
                json=payload,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"{self.provider} code embedding request failed: "
                f"HTTP {response.status_code}"
            ) from error

        response_payload = response.json()
        self.last_token_usage = _token_usage_from_response(response_payload)
        return _embeddings_from_response(response_payload)


def _build_sentence_transformer(model_name: str) -> Any:
    if model_name not in ALLOWED_CODE_EMBEDDING_MODELS:
        raise ValueError(f"Code embedding model is not allowlisted: {model_name}")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError(
            "sentence-transformers is required for code semantic search"
        ) from error

    return SentenceTransformer(
        model_name,
        device="cpu",
        trust_remote_code=True,
    )


def _build_chroma_client(persist_path: Path) -> Any:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError("chromadb is required for code semantic search") from error

    return chromadb.PersistentClient(path=str(persist_path))


def _code_embedding_api_key(*, provider: str, settings: Any) -> str:
    secret = (
        settings.nvidia_api_key if provider == "nvidia" else settings.openai_api_key
    )
    api_key = secret.get_secret_value() if secret is not None else ""
    if not api_key:
        raise RuntimeError(f"{provider} API key is required for code embeddings")

    return api_key


def _code_embedding_base_url(*, provider: str, settings: Any) -> str:
    if settings.code_embedding_base_url:
        return str(settings.code_embedding_base_url)

    if provider == "nvidia":
        return str(settings.nvidia_base_url)

    return "https://api.openai.com/v1"


def _embeddings_from_response(response_payload: object) -> list[list[float]]:
    if not isinstance(response_payload, dict):
        raise RuntimeError("Code embedding API returned an invalid response")

    data = response_payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Code embedding API response has no data array")

    indexed_embeddings: list[tuple[int, object]] = []
    for default_index, item in enumerate(data):
        if not isinstance(item, dict):
            raise RuntimeError("Code embedding API returned an invalid item")

        index = item.get("index", default_index)
        if not isinstance(index, int):
            raise RuntimeError("Code embedding API returned an invalid index")

        indexed_embeddings.append((index, item.get("embedding")))

    return [
        embedding
        for _index, embedding in sorted(
            indexed_embeddings,
            key=lambda indexed_embedding: indexed_embedding[0],
        )
        if isinstance(embedding, list)
    ]


def _chunk_id(chunk: ChunkMetadataDocument) -> str:
    return f"{chunk.job_id}:{chunk.file_path}:{chunk.chunk_index}"


def _chunk_metadata(
    chunk: ChunkMetadataDocument,
) -> dict[str, str | int | float | bool]:
    return {
        "job_id": str(chunk.job_id),
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "function_name": chunk.function_name or "",
        "class_name": chunk.class_name or "",
        "language": chunk.language,
        "risk_area": chunk.risk_area or "general",
        "module": chunk.module or "",
        "imports": json.dumps(chunk.imports, ensure_ascii=False),
    }


def _batched(
    chunks: list[ChunkMetadataDocument],
    batch_size: int,
) -> list[list[ChunkMetadataDocument]]:
    return [
        chunks[start : start + batch_size]
        for start in range(0, len(chunks), batch_size)
    ]


def _count_batches(item_count: int, batch_size: int) -> int:
    return (item_count + batch_size - 1) // batch_size


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _token_usage_from_response(response_payload: object) -> dict[str, int] | None:
    if not isinstance(response_payload, dict):
        return None
    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _usage_int(usage, ("input_tokens", "prompt_tokens"))
    total_tokens = _usage_int(usage, ("total_tokens",))
    if total_tokens == 0:
        total_tokens = input_tokens
    result = {
        "input_tokens": input_tokens,
        "total_tokens": total_tokens,
    }
    return result if any(result.values()) else None


def _usage_int(value: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            return item
    return 0


def _validated_embeddings(
    values: object,
    *,
    expected_dimension: int | None = CODE_EMBEDDING_DIMENSION,
) -> list[list[float]]:
    if not isinstance(values, list):
        raise RuntimeError("Code embedding model returned an invalid tensor")

    embeddings: list[list[float]] = []
    for value in values:
        if not isinstance(value, list) or not value:
            raise RuntimeError("Code embedding model returned an invalid vector")

        if expected_dimension is not None and len(value) != expected_dimension:
            raise RuntimeError(
                "Code embedding dimension changed; rebuild code_chunks before use"
            )
        embeddings.append([float(item) for item in value])

    return embeddings
