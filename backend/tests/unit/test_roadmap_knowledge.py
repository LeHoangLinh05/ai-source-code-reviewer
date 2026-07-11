"""Tests for roadmap ingestion into the unified knowledge base."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.rag.bm25_index import BM25Index
from app.ai.rag.ingestion import RAGDocument, RAGIngestionPipeline
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
import app.ai.rag.vectorstore as vectorstore_module
from app.ai.rag.vectorstore import (
    COLLECTION_NAME,
    KNOWLEDGE_EMBEDDING_MODEL,
    KNOWLEDGE_EMBEDDING_MODEL_VERSION,
    ChromaVectorStore,
    VectorSearchResult,
)
from app.ai.roadmap.knowledge import (
    ROADMAP_PROFILE_ID,
    ROADMAP_SOURCE_PATH,
    load_roadmap_documents,
    load_roadmap_requirements,
)
import app.ai.tools.search_knowledge as search_knowledge_module
from app.ai.tools.search_knowledge import search_knowledge_base

EXPECTED_ROADMAP_RULES = 79
REQUIRED_ROADMAP_METADATA = {
    "doc_type",
    "rule_id",
    "profile_id",
    "week",
    "priority",
    "skill_group",
    "needs_ai_verification",
}


class FakeVectorStore:
    """Small metadata-aware vector store for ingestion/retrieval tests."""

    def __init__(self) -> None:
        self.documents: list[VectorSearchResult] = []

    def add_documents(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, object]],
    ) -> None:
        self.documents.extend(
            VectorSearchResult(
                id=document_id,
                content=content,
                metadata=metadata,
                score=0.8,
            )
            for document_id, content, metadata in zip(
                ids,
                documents,
                metadatas,
                strict=True,
            )
        )

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object] | None = None,
    ) -> list[VectorSearchResult]:
        del query
        return [
            document
            for document in self.documents
            if _matches_chroma_where(document.metadata, where)
        ][:n_results]

    def all_documents(self) -> list[VectorSearchResult]:
        return self.documents

    def reset_collection(self) -> None:
        self.documents = []


class FakeCollection:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata


class FakeChromaClient:
    def __init__(self) -> None:
        self.collection_names: list[str] = []
        self.collection: FakeCollection | None = None

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, object],
    ) -> FakeCollection:
        self.collection_names.append(name)
        self.collection = FakeCollection(metadata)
        return self.collection


class FakeEmbedder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


def test_roadmap_yaml_loads_all_requirements() -> None:
    assert ROADMAP_SOURCE_PATH.exists()

    requirements = load_roadmap_requirements()

    assert len(requirements) == EXPECTED_ROADMAP_RULES
    assert len({requirement.rule_id for requirement in requirements}) == len(
        requirements
    )
    ai_rule_ids = {
        requirement.rule_id
        for requirement in requirements
        if requirement.needs_ai_verification
    }
    assert {"RC-W1-10", "RC-W5-01", "RC-W7-06"} <= ai_rule_ids


def test_roadmap_ingestion_creates_one_document_per_rule() -> None:
    vectorstore = FakeVectorStore()
    pipeline = RAGIngestionPipeline(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        bm25_index=BM25Index(),
    )

    chunks = pipeline.ingest_roadmap()

    assert len(load_roadmap_documents()) == EXPECTED_ROADMAP_RULES
    assert len(chunks) == EXPECTED_ROADMAP_RULES
    assert len(vectorstore.documents) == EXPECTED_ROADMAP_RULES
    assert all(REQUIRED_ROADMAP_METADATA <= chunk.metadata.keys() for chunk in chunks)
    assert all(chunk.metadata["source"] == ROADMAP_PROFILE_ID for chunk in chunks)


def test_search_roadmap_by_rule_id() -> None:
    retriever = _roadmap_retriever()

    results = retriever.search(
        "RC-W5-01",
        doc_type="roadmap_rule",
        top_k=1,
    )

    assert results[0].metadata["rule_id"] == "RC-W5-01"


def test_search_roadmap_by_profile_and_weeks() -> None:
    retriever = _roadmap_retriever()

    results = retriever.search(
        "requirement",
        doc_type="roadmap_rule",
        category="requirement",
        language="general",
        profile_id=ROADMAP_PROFILE_ID,
        weeks=[5],
        priority="P0",
        top_k=5,
    )

    assert results
    assert all(
        result.metadata["profile_id"] == ROADMAP_PROFILE_ID for result in results
    )
    assert all(result.metadata["week"] == 5 for result in results)
    assert all(result.metadata["priority"] == "P0" for result in results)


def test_search_knowledge_tool_supports_roadmap_filters_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRetriever:
        def search(self, query: str, **filters: object) -> list[RetrievedChunk]:
            assert query == "WebSocket requirement"
            assert filters["doc_type"] == "roadmap_rule"
            assert filters["profile_id"] == ROADMAP_PROFILE_ID
            assert filters["weeks"] == [5]
            return [
                RetrievedChunk(
                    id="roadmap-rule",
                    source=ROADMAP_PROFILE_ID,
                    content="Rule ID: RC-W5-01",
                    metadata={
                        "doc_type": "roadmap_rule",
                        "rule_id": "RC-W5-01",
                        "profile_id": ROADMAP_PROFILE_ID,
                        "week": 5,
                        "priority": "P0",
                        "skill_group": "Realtime Communication",
                    },
                    vector_score=0.81,
                    bm25_score=0.74,
                    final_score=0.79,
                )
            ]

    monkeypatch.setattr(search_knowledge_module, "_retriever", FakeRetriever())

    output = search_knowledge_base.invoke(
        {
            "query": "WebSocket requirement",
            "doc_type": "roadmap_rule",
            "category": "requirement",
            "language": "general",
            "profile_id": ROADMAP_PROFILE_ID,
            "weeks": [5],
            "priority": "P0",
            "top_k": 3,
        }
    )

    results = output["results"]
    assert isinstance(results, list)
    assert results[0]["metadata"]["rule_id"] == "RC-W5-01"
    assert results[0]["vector_score"] == 0.81


@pytest.mark.parametrize("doc_type", ["standard", "guideline", "checklist"])
def test_search_coding_knowledge_types_still_works(doc_type: str) -> None:
    vectorstore = FakeVectorStore()
    pipeline = RAGIngestionPipeline(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        bm25_index=BM25Index(),
    )
    pipeline.ingest_document(
        RAGDocument(
            source=f"{doc_type}-source",
            content=f"Unique {doc_type} security knowledge",
            language="python",
            doc_type=doc_type,
            category="security",
        )
    )
    retriever = HybridRetriever(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        bm25_index=pipeline.bm25_index,
    )

    results = retriever.search(
        f"Unique {doc_type}",
        doc_type=doc_type,
        language="python",
        top_k=1,
    )

    assert results[0].metadata["doc_type"] == doc_type


def test_knowledge_collection_uses_minilm_and_no_roadmap_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeChromaClient()
    monkeypatch.setattr(ChromaVectorStore, "_instance", None)
    monkeypatch.setattr(
        ChromaVectorStore,
        "_build_client",
        lambda _self, _path: client,
    )
    monkeypatch.setattr(
        vectorstore_module, "_SentenceTransformerEmbedder", FakeEmbedder
    )

    store = ChromaVectorStore(
        persist_path=tmp_path,
        embedding_model_name=KNOWLEDGE_EMBEDDING_MODEL,
    )

    assert store.collection_name == "knowledge_base"
    assert COLLECTION_NAME == "knowledge_base"
    assert client.collection_names == ["knowledge_base"]
    assert client.collection is not None
    assert client.collection.metadata["embedding_model"] == KNOWLEDGE_EMBEDDING_MODEL
    assert (
        client.collection.metadata["embedding_model_version"]
        == KNOWLEDGE_EMBEDDING_MODEL_VERSION
    )
    assert "roadmap" not in client.collection_names


def _roadmap_retriever() -> HybridRetriever:
    vectorstore = FakeVectorStore()
    pipeline = RAGIngestionPipeline(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        bm25_index=BM25Index(),
    )
    pipeline.ingest_roadmap()
    return HybridRetriever(
        vectorstore=vectorstore,  # type: ignore[arg-type]
        bm25_index=pipeline.bm25_index,
    )


def _matches_chroma_where(
    metadata: dict[str, object],
    where: dict[str, object] | None,
) -> bool:
    if where is None:
        return True
    if "$and" in where:
        conditions = where["$and"]
        assert isinstance(conditions, list)
        return all(
            _matches_chroma_where(metadata, condition) for condition in conditions
        )

    for key, expected in where.items():
        if isinstance(expected, dict) and "$in" in expected:
            values = expected["$in"]
            assert isinstance(values, list)
            if metadata.get(key) not in values:
                return False
        elif metadata.get(key) != expected:
            return False

    return True
