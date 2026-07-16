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
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
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
            provider="local",
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


def test_mistral_code_embedder_sends_output_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_payloads: list[dict[str, Any]] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "index": 0,
                        "embedding": [0.1] * 1536,
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "total_tokens": 3,
                },
            }

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> FakeResponse:
            posted_payloads.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                }
            )
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            Client=FakeClient,
            HTTPStatusError=RuntimeError,
        ),
    )
    embedder = code_embedding_module._OpenAICompatibleCodeEmbedder(
        provider="mistral",
        model_name="codestral-embed-2505",
        api_key="mistral-key",
        base_url="https://api.mistral.ai/v1",
        output_dimension=1536,
        max_retries=0,
        retry_base_delay_seconds=2.0,
    )

    embeddings = embedder.embed_documents(["def read_user(): pass"])

    assert len(embeddings[0]) == 1536
    assert posted_payloads == [
        {
            "url": "https://api.mistral.ai/v1/embeddings",
            "headers": {
                "Authorization": "Bearer mistral-key",
                "Content-Type": "application/json",
            },
            "json": {
                "model": "codestral-embed-2505",
                "input": ["def read_user(): pass"],
                "encoding_format": "float",
                "output_dimension": 1536,
                "output_dtype": "float",
            },
        }
    ]
    assert embedder.last_token_usage == {
        "input_tokens": 3,
        "total_tokens": 3,
    }


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
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
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
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
        batch_size=2,
    )

    store.index_chunks(chunks)

    assert [len(payload["documents"]) for payload in collection.upsert_payloads] == [
        2,
        2,
        1,
    ]


def test_code_embedder_reuses_cached_repo_branch_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _FakeCollection()
    model = _FakeModel()
    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        lambda _model_name: model,
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )
    repository_id = uuid4()
    chunks = [
        _chunk(file_path="app/a.py", chunk_text="def a(): pass"),
        _chunk(file_path="app/b.py", chunk_text="def b(): pass"),
    ]
    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
    )

    first = store.index_chunks(chunks, repository_id=repository_id, branch="main")
    second = store.index_chunks(chunks, repository_id=repository_id, branch="main")

    assert first.embedded_count == 2
    assert first.cache_hit_count == 0
    assert second.embedded_count == 0
    assert second.cache_hit_count == 2
    assert model.encode_call_count == 1
    assert len(collection.upsert_payloads) == 1
    assert len(collection.update_payloads) == 1


def test_code_embedder_embeds_changed_chunk_and_prunes_stale_cache_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collection = _FakeCollection()
    model = _FakeModel()
    monkeypatch.setattr(
        code_embedding_module,
        "_build_sentence_transformer",
        lambda _model_name: model,
    )
    monkeypatch.setattr(
        code_embedding_module,
        "_build_chroma_client",
        lambda _path: _FakeChromaClient(collection),
    )
    repository_id = uuid4()
    store = CodeEmbeddingStore(
        persist_path=tmp_path,
        model_name=JINA_CODE_MODEL,
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
    )

    store.index_chunks(
        [
            _chunk(file_path="app/a.py", chunk_text="def a(): return 1"),
            _chunk(file_path="app/b.py", chunk_text="def b(): pass"),
        ],
        repository_id=repository_id,
        branch="main",
    )
    summary = store.index_chunks(
        [
            _chunk(file_path="app/a.py", chunk_text="def a(): return 2"),
        ],
        repository_id=repository_id,
        branch="main",
    )

    assert summary.embedded_count == 1
    assert summary.cache_hit_count == 0
    assert summary.pruned_count == 2
    assert model.encode_call_count == 2
    assert len(collection.deleted_ids) == 2


def test_code_retriever_requires_and_filters_by_job_id() -> None:
    job_id = uuid4()
    vectorstore = _FakeVectorStore([])
    retriever = CodeSemanticRetriever(vectorstore)

    retriever.search(query="database query", job_id=job_id)

    assert vectorstore.where == {"job_id": str(job_id)}
    with pytest.raises(ValueError, match="job_id is required"):
        retriever.search(query="database query", job_id=None)  # type: ignore[arg-type]


def test_code_retriever_filters_by_repo_branch_key() -> None:
    job_id = uuid4()
    repo_branch_key = "repo-branch-a"
    vectorstore = _FakeVectorStore(
        [
            _vector_result(
                job_id=job_id,
                repo_branch_key=repo_branch_key,
                file_path="app/a.py",
            ),
            _vector_result(
                job_id=job_id,
                repo_branch_key="repo-branch-b",
                file_path="app/b.py",
            ),
        ]
    )

    results = CodeSemanticRetriever(vectorstore).search(
        query="database query",
        job_id=job_id,
        repo_branch_key=repo_branch_key,
    )

    assert vectorstore.where == {"repo_branch_key": repo_branch_key}
    assert [result.metadata["file_path"] for result in results] == ["app/a.py"]


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
            provider="local",
            dimension=CODE_EMBEDDING_DIMENSION,
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
        provider="local",
        dimension=CODE_EMBEDDING_DIMENSION,
    )

    store.delete_job("job-a")

    assert collection.delete_where == {"job_id": "job-a"}


class _FakeEmbedding:
    def __init__(self, values: list[list[float]]) -> None:
        self.values = values

    def tolist(self) -> list[list[float]]:
        return self.values


class _FakeModel:
    def __init__(self) -> None:
        self.encode_call_count = 0

    def encode(self, texts: list[str], **_kwargs: object) -> _FakeEmbedding:
        self.encode_call_count += 1
        return _FakeEmbedding([[0.1] * CODE_EMBEDDING_DIMENSION for _text in texts])


class _FakeCollection:
    def __init__(self) -> None:
        self.name = ""
        self.metadata: dict[str, object] = {
            "embedding_model": JINA_CODE_MODEL,
        }
        self.upsert_payload: dict[str, Any] = {}
        self.upsert_payloads: list[dict[str, Any]] = []
        self.update_payloads: list[dict[str, Any]] = []
        self.delete_where: dict[str, object] | None = None
        self.deleted_ids: list[str] = []
        self.records: dict[str, dict[str, Any]] = {}

    def upsert(self, **payload: Any) -> None:
        self.upsert_payload = payload
        self.upsert_payloads.append(payload)
        ids = payload.get("ids", [])
        documents = payload.get("documents", [])
        metadatas = payload.get("metadatas", [])
        for item_id, document, metadata in zip(ids, documents, metadatas, strict=True):
            self.records[str(item_id)] = {
                "document": document,
                "metadata": metadata,
            }

    def get(
        self,
        *,
        where: dict[str, object],
        include: list[str],
    ) -> dict[str, list[object]]:
        _ = include
        ids: list[object] = [
            item_id
            for item_id, record in self.records.items()
            if all(record["metadata"].get(key) == value for key, value in where.items())
        ]
        return {"ids": ids}

    def update(self, **payload: Any) -> None:
        self.update_payloads.append(payload)
        ids = payload.get("ids", [])
        documents = payload.get("documents")
        metadatas = payload.get("metadatas", [])
        for index, (item_id, metadata) in enumerate(
            zip(ids, metadatas, strict=True)
        ):
            record = self.records.setdefault(str(item_id), {})
            if isinstance(documents, list):
                record["document"] = documents[index]
            record["metadata"] = metadata

    def delete(
        self,
        *,
        where: dict[str, object] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        if ids is not None:
            self.deleted_ids.extend(ids)
            for item_id in ids:
                self.records.pop(item_id, None)
            return

        self.delete_where = where


class _FakeChromaClient:
    def __init__(self, collection: _FakeCollection) -> None:
        self.collection = collection

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, object],
        embedding_function: object | None = None,
    ) -> _FakeCollection:
        _ = embedding_function
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
        embedding_function: object | None = None,
    ) -> _FakeCollection:
        _ = name, metadata, embedding_function
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
    repo_branch_key: str | None = None,
    content: str | None = None,
    score: float = 0.9,
) -> CodeVectorSearchResult:
    metadata: dict[str, object] = {
        "job_id": str(job_id),
        "file_path": file_path,
        "chunk_index": 0,
    }
    if repo_branch_key is not None:
        metadata["repo_branch_key"] = repo_branch_key
    return CodeVectorSearchResult(
        content=content or f"# {file_path}",
        metadata=metadata,
        score=score,
    )


def _chunk(*, file_path: str, chunk_text: str) -> ChunkMetadataDocument:
    return ChunkMetadataDocument(
        job_id=uuid4(),
        file_path=file_path,
        language="python",
        chunk_type="function",
        chunk_index=0,
        total_chunks=1,
        function_name=None,
        line_start=1,
        line_end=1,
        imports=[],
        module="app",
        risk_area="general",
        token_count=3,
        chunk_text=chunk_text,
    )
