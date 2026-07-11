"""Tests for code-specific embeddings and job-isolated retrieval."""

from pathlib import Path
import sys
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.ai.rag.code_embedding as code_embedding_module
from app.ai.rag.code_embedding import (
    ALLOWED_CODE_EMBEDDING_MODELS,
    CODE_CHUNKS_COLLECTION,
    CODE_EMBEDDING_DIMENSION,
    CODE_EMBEDDING_MODEL_VERSION,
    CodeEmbeddingStore,
    CodeVectorSearchResult,
    _build_sentence_transformer,
)
from app.ai.rag.code_retriever import CodeSemanticRetriever
from app.core.config import Settings
from app.schemas.mongodb import ChunkMetadataDocument

JINA_CODE_MODEL = "jinaai/jina-embeddings-v2-base-code"
MINILM_KNOWLEDGE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def test_code_embedder_uses_allowlisted_jina_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_calls: list[tuple[str, str, bool]] = []
    collection = _FakeCollection()

    def build_model(model_name: str) -> _FakeModel:
        model_calls.append((model_name, "cpu", True))
        return _FakeModel()

    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        build_model,
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )

    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
    )

    assert store.model_name == JINA_CODE_MODEL
    assert model_calls == [(JINA_CODE_MODEL, "cpu", True)]
    assert JINA_CODE_MODEL in ALLOWED_CODE_EMBEDDING_MODELS
    assert collection.name == CODE_CHUNKS_COLLECTION
    assert collection.metadata["embedding_model"] == JINA_CODE_MODEL
    assert (
        collection.metadata["embedding_model_version"] == CODE_EMBEDDING_MODEL_VERSION
    )
    assert collection.metadata["embedding_dimension"] == CODE_EMBEDDING_DIMENSION


def test_code_embedder_rejects_non_allowlisted_model(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        CodeEmbeddingStore(
            persist_path=tmp_path,
            model_name=MINILM_KNOWLEDGE_MODEL,
        )


def test_jina_sentence_transformer_enables_remote_code_only_for_allowlisted_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, bool]] = []

    def sentence_transformer(
        model_name: str,
        *,
        device: str,
        trust_remote_code: bool,
    ) -> object:
        calls.append((model_name, device, trust_remote_code))
        return object()

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=sentence_transformer),
    )

    _build_sentence_transformer(JINA_CODE_MODEL)

    assert calls == [(JINA_CODE_MODEL, "cpu", True)]


def test_knowledge_and_code_embedding_defaults_are_separate() -> None:
    assert Settings.model_fields["rag_embedding_model"].default == (
        MINILM_KNOWLEDGE_MODEL
    )
    assert Settings.model_fields["code_embedding_model"].default == JINA_CODE_MODEL
    assert Settings.model_fields["code_embedding_batch_size"].default == 4
    assert Settings.model_fields["code_embedding_max_sequence_length"].default == 1024


def test_code_embedder_indexes_full_chunk_content_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _FakeCollection()
    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        lambda _model_name: _FakeModel(),
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )
    job_id = uuid4()
    full_content = (
        "def find_user(user_id: int) -> User | None:\n    return query(user_id)\n"
    )
    chunk = ChunkMetadataDocument(
        job_id=job_id,
        file_path="app/repositories/user.py",
        language="python",
        chunk_type="function",
        chunk_index=2,
        total_chunks=3,
        function_name="find_user",
        line_start=40,
        line_end=41,
        imports=["app.models.User"],
        module="app.repositories.user",
        risk_area="database",
        token_count=14,
        chunk_text=full_content,
    )
    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
    )

    store.index_chunks([chunk])

    assert collection.upsert_payload["documents"] == [full_content]
    assert collection.upsert_payload["metadatas"] == [
        {
            "job_id": str(job_id),
            "file_path": "app/repositories/user.py",
            "chunk_index": 2,
            "line_start": 40,
            "line_end": 41,
            "function_name": "find_user",
            "class_name": "",
            "language": "python",
            "risk_area": "database",
            "module": "app.repositories.user",
            "imports": '["app.models.User"]',
        }
    ]


def test_code_embedder_upserts_each_embedding_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _FakeCollection()
    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        lambda _model_name: _FakeModel(),
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )
    job_id = uuid4()
    chunks = [
        ChunkMetadataDocument(
            job_id=job_id,
            file_path=f"app/module_{index}.py",
            language="python",
            chunk_type="function",
            chunk_index=0,
            total_chunks=1,
            function_name=f"function_{index}",
            line_start=1,
            line_end=1,
            imports=[],
            module="app",
            risk_area="general",
            token_count=3,
            chunk_text=f"def function_{index}(): pass",
        )
        for index in range(5)
    ]
    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
        batch_size=2,
    )

    store.index_chunks(chunks)

    assert [len(payload["documents"]) for payload in collection.upsert_payloads] == [
        2,
        2,
        1,
    ]


def test_code_retriever_requires_and_filters_by_job_id() -> None:
    job_id = uuid4()
    vectorstore = _FakeVectorStore([])
    retriever = CodeSemanticRetriever(vectorstore)

    retriever.search(query="database query", job_id=job_id)

    assert vectorstore.where == {"job_id": str(job_id)}
    with pytest.raises(ValueError, match="job_id is required"):
        retriever.search(query="database query", job_id=None)  # type: ignore[arg-type]


def test_job_a_never_retrieves_job_b_chunk() -> None:
    job_a = uuid4()
    job_b = uuid4()
    vectorstore = _FakeVectorStore(
        [
            _vector_result(
                job_id=job_a,
                file_path="app/a.py",
                content="def authenticate(): pass",
            ),
            _vector_result(
                job_id=job_b,
                file_path="app/b.py",
                content="def authenticate(): pass",
            ),
        ]
    )

    results = CodeSemanticRetriever(vectorstore).search(
        query="authentication",
        job_id=job_a,
    )

    assert [result.metadata["file_path"] for result in results] == ["app/a.py"]


def test_code_retriever_reranks_runtime_source_above_spec_files() -> None:
    job_id = uuid4()
    vectorstore = _FakeVectorStore(
        [
            _vector_result(
                job_id=job_id,
                file_path="backend/verify_logic_errors.py",
                content="RC-W1-10 expected failure: login returns hardcoded tokens",
                score=0.96,
            ),
            _vector_result(
                job_id=job_id,
                file_path="roadmap_ai_rules_test_spec.md",
                content="The benchmark expects /login to verify password and issue JWT",
                score=0.95,
            ),
            _vector_result(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content=(
                    "async def login(payload):\n"
                    "    user = find_user(payload.email)\n"
                    "    return {'access_token': 'fake-token', 'refresh_token': 'fake'}\n"
                ),
                score=0.72,
            ),
        ]
    )

    results = CodeSemanticRetriever(vectorstore).search(
        query=(
            "rule RC-W1-10 /login verify password hash and generate access "
            "refresh JWT tokens, not hardcoded fake token"
        ),
        job_id=job_id,
        top_k=2,
    )

    assert [result.metadata["file_path"] for result in results] == [
        "backend/app/auth.py",
        "backend/verify_logic_errors.py",
    ]
    assert vectorstore.n_results == 40


@pytest.mark.parametrize(
    ("metadata_key", "incompatible_value"),
    [
        ("embedding_model", MINILM_KNOWLEDGE_MODEL),
        ("embedding_model_version", "old-version"),
        ("embedding_dimension", 384),
    ],
)
def test_code_chunks_reject_incompatible_model_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_key: str,
    incompatible_value: object,
) -> None:
    collection = _FakeCollection()
    collection.metadata = {
        "embedding_model": JINA_CODE_MODEL,
        "embedding_model_version": CODE_EMBEDDING_MODEL_VERSION,
        "embedding_dimension": CODE_EMBEDDING_DIMENSION,
    }
    collection.metadata[metadata_key] = incompatible_value
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _ExistingCollectionClient(collection),
    )

    with pytest.raises(RuntimeError, match="rebuild the collection"):
        CodeEmbeddingStore(
            persist_path=tmp_path,
            model_name=JINA_CODE_MODEL,
        )


def test_code_chunks_cleanup_is_scoped_to_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _FakeCollection()
    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        lambda _model_name: _FakeModel(),
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )
    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
    )

    store.delete_job("job-a")

    assert collection.delete_where == {"job_id": "job-a"}


class _FakeEmbedding:
    def __init__(self, values: list[list[float]]) -> None:
        self.values = values

    def tolist(self) -> list[list[float]]:
        return self.values


class _FakeModel:
    def encode(self, texts: list[str], **_kwargs: object) -> _FakeEmbedding:
        return _FakeEmbedding([[0.1] * CODE_EMBEDDING_DIMENSION for _text in texts])


class _FakeCollection:
    def __init__(self) -> None:
        self.name = ""
        self.metadata: dict[str, object] = {
            "embedding_model": JINA_CODE_MODEL,
        }
        self.upsert_payload: dict[str, Any] = {}
        self.upsert_payloads: list[dict[str, Any]] = []
        self.delete_where: dict[str, object] | None = None

    def upsert(self, **payload: Any) -> None:
        self.upsert_payload = payload
        self.upsert_payloads.append(payload)

    def delete(self, *, where: dict[str, object]) -> None:
        self.delete_where = where


class _FakeChromaClient:
    def __init__(self, collection: _FakeCollection) -> None:
        self.collection = collection

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, object],
    ) -> _FakeCollection:
        self.collection.name = name
        self.collection.metadata = metadata
        return self.collection


class _ExistingCollectionClient:
    def __init__(self, collection: _FakeCollection) -> None:
        self.collection = collection

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, object],
    ) -> _FakeCollection:
        _ = name, metadata
        return self.collection


class _FakeVectorStore:
    def __init__(self, results: list[CodeVectorSearchResult]) -> None:
        self.results = results
        self.where: dict[str, object] | None = None
        self.n_results: int | None = None

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]:
        _ = query
        self.n_results = n_results
        self.where = where
        return self.results


def _vector_result(
    *,
    job_id: object,
    file_path: str,
    content: str | None = None,
    score: float = 0.9,
) -> CodeVectorSearchResult:
    return CodeVectorSearchResult(
        content=content or f"# {file_path}",
        metadata={
            "job_id": str(job_id),
            "file_path": file_path,
            "chunk_index": 0,
        },
        score=score,
    )
