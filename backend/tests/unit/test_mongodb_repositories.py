"""Tests for MongoDB document repositories and index setup."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import mongodb
from app.repositories.mongodb_repository import (
    RepoSummaryResultRepository,
    ToolCallLogRepository,
)
from app.schemas.mongodb import RepoSummaryResultDocument, ToolCallLogDocument


class FakeInsertOneResult:
    """Minimal insert result returned by the fake Mongo collection."""

    inserted_id = "mongo-id-1"


class FakeCursor:
    """Minimal async cursor used by repository find tests."""

    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None) -> list[dict[str, object]]:
        return self.documents


class FakeCollection:
    """Minimal async Mongo collection used by repository and index tests."""

    def __init__(self) -> None:
        self.inserted_payload: dict[str, object] | None = None
        self.find_filter: dict[str, object] | None = None
        self.find_one_filter: dict[str, object] | None = None
        self.find_one_sort: list[tuple[str, int]] | None = None
        self.find_one_document: dict[str, object] | None = None
        self.indexes: list[tuple[list[tuple[str, int]], str]] = []

    async def insert_one(self, payload: dict[str, object]) -> FakeInsertOneResult:
        self.inserted_payload = payload
        return FakeInsertOneResult()

    def find(self, query_filter: dict[str, object]) -> FakeCursor:
        self.find_filter = query_filter
        return FakeCursor([{"_id": "abc", **query_filter}])

    async def find_one(
        self,
        query_filter: dict[str, object],
        sort: list[tuple[str, int]] | None = None,
    ) -> dict[str, object] | None:
        self.find_one_filter = query_filter
        self.find_one_sort = sort
        return self.find_one_document

    async def create_index(
        self,
        keys: list[tuple[str, int]],
        name: str,
    ) -> str:
        self.indexes.append((keys, name))
        return name


class FakeDatabase:
    """Mapping-style fake database that returns named fake collections."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, collection_name: str) -> FakeCollection:
        if collection_name not in self.collections:
            self.collections[collection_name] = FakeCollection()

        return self.collections[collection_name]


@pytest.mark.asyncio
async def test_tool_call_repository_inserts_and_finds_by_job_id() -> None:
    database = FakeDatabase()
    repository = ToolCallLogRepository(database)  # type: ignore[arg-type]
    job_id = uuid4()
    document = ToolCallLogDocument(
        job_id=job_id,
        session_id=uuid4(),
        sequence=1,
        tool_name="search_coding_standard",
        called_at=datetime.now(UTC),
        duration_ms=24,
        input={"query": "SQL injection"},
        output={"results": []},
    )

    inserted_id = await repository.insert_one(document)
    documents = await repository.find_by_job_id(job_id)

    assert inserted_id == "mongo-id-1"
    assert repository.collection.inserted_payload is not None
    assert repository.collection.inserted_payload["job_id"] == str(job_id)
    assert repository.collection.find_filter == {"job_id": str(job_id)}
    assert documents == [{"_id": "abc", "job_id": str(job_id)}]


@pytest.mark.asyncio
async def test_repo_summary_repository_inserts_and_finds_latest() -> None:
    database = FakeDatabase()
    repository = RepoSummaryResultRepository(database)  # type: ignore[arg-type]
    collection = database["repo_summary_results"]
    repository_id = uuid4()
    job_id = uuid4()
    generated_at = datetime.now(UTC)
    document = RepoSummaryResultDocument(
        repository_id=repository_id,
        job_id=job_id,
        commit_sha="abc123",
        generated_at=generated_at,
        model_used="gemini-2.0-flash",
        purpose="Review Python repositories and produce actionable reports.",
        project_type="REST API backend",
        tech_stack=["Python", "FastAPI", "MongoDB"],
        architecture_overview="Thin API routes call services and repositories.",
    )
    collection.find_one_document = {
        "_id": "summary-id-1",
        "repository_id": str(repository_id),
        "generated_at": generated_at.isoformat(),
    }

    inserted_id = await repository.insert_one(document)
    latest_document = await repository.find_latest_by_repository_id(repository_id)

    assert inserted_id == "mongo-id-1"
    assert collection.inserted_payload is not None
    assert collection.inserted_payload["repository_id"] == str(repository_id)
    assert collection.inserted_payload["job_id"] == str(job_id)
    assert collection.inserted_payload["tech_stack"] == [
        "Python",
        "FastAPI",
        "MongoDB",
    ]
    assert collection.find_one_filter == {"repository_id": str(repository_id)}
    assert collection.find_one_sort == [("generated_at", -1)]
    assert latest_document == {
        "_id": "summary-id-1",
        "repository_id": str(repository_id),
        "generated_at": generated_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_ensure_mongodb_indexes_creates_expected_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(mongodb, "get_mongodb_database", lambda: database)

    await mongodb.ensure_mongodb_indexes()

    assert any(
        name == "idx_tool_logs_job_session_sequence"
        for _keys, name in database["tool_call_logs"].indexes
    )
    assert any(
        name == "idx_chunk_metadata_job_module_risk"
        for _keys, name in database["chunk_metadata"].indexes
    )
    assert any(
        name == "idx_chunk_metadata_repo_branch_file"
        for _keys, name in database["chunk_metadata"].indexes
    )
    assert any(
        name == "idx_repo_summary_repository"
        for _keys, name in database["repo_summary_results"].indexes
    )
    assert any(
        keys == [("repository_id", 1), ("generated_at", -1)]
        and name == "idx_repo_summary_repository_generated_desc"
        for keys, name in database["repo_summary_results"].indexes
    )
    assert "roadmap_compliance_results" not in database.collections
