"""Tests for full-content semantic code search tool output."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

import app.ai.tools.search_code as search_code_module
import app.ai.tools.read_file as read_file_module
from app.ai.rag.code_retriever import RetrievedCodeChunk
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.search_code import (
    DEFAULT_SEMANTIC_CODE_QUERY,
    _semantic_query,
    _search_code_semantic_impl,
)
from app.ai.tools.read_file import _read_file_chunk_impl


@pytest.mark.asyncio
async def test_search_code_semantic_returns_unavailable_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()

    async def active_job() -> None:
        return None

    def fail_if_retriever_is_loaded() -> _FakeRetriever:
        raise AssertionError("disabled semantic search must not load retriever")

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    monkeypatch.setattr(
        search_code_module,
        "get_settings",
        lambda: SimpleNamespace(enable_code_semantic_search=False),
    )
    monkeypatch.setattr(
        search_code_module,
        "get_code_retriever",
        fail_if_retriever_is_loaded,
    )
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        response = await _search_code_semantic_impl(
            job_id=str(job_id),
            query="find authentication flow",
            top_k=3,
            language=None,
            risk_area=None,
        )

    assert response == {
        "status": "unavailable",
        "reason": "code semantic search is disabled",
        "results": [],
    }


def test_search_code_semantic_uses_default_query_for_empty_input() -> None:
    assert (
        _semantic_query(query=None, file_path=None)
        == DEFAULT_SEMANTIC_CODE_QUERY
    )
    assert _semantic_query(query="  ", file_path="") == DEFAULT_SEMANTIC_CODE_QUERY


@pytest.mark.asyncio
async def test_search_code_semantic_returns_full_exact_content_and_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()
    file_path = "app/repositories/user.py"
    source_path = tmp_path / file_path
    source_path.parent.mkdir(parents=True)
    full_content = (
        "def find_user(user_id: int) -> User | None:\n"
        "    return session.get(User, user_id)\n"
    )
    source_path.write_text(full_content, encoding="utf-8")
    result = RetrievedCodeChunk(
        content=full_content,
        metadata={
            "job_id": str(job_id),
            "file_path": file_path,
            "chunk_index": 2,
            "line_start": 40,
            "line_end": 41,
            "function_name": "find_user",
            "class_name": "",
            "language": "python",
            "risk_area": "database",
        },
        semantic_score=0.81,
    )
    retriever = _FakeRetriever([result])
    database = _FakeMongoDatabase(
        {
            "job_id": str(job_id),
            "file_path": file_path,
            "chunk_index": 2,
            "line_start": 40,
            "line_end": 41,
            "chunk_text": full_content,
        }
    )

    async def active_job() -> None:
        return None

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    monkeypatch.setattr(read_file_module, "ensure_ai_job_active", active_job)

    async def no_static_issues(**_kwargs: object) -> list[dict[str, object]]:
        return []

    async def no_context_hints(**_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(
        read_file_module,
        "_static_issues_in_range",
        no_static_issues,
    )
    monkeypatch.setattr(
        read_file_module,
        "_context_hints",
        no_context_hints,
    )
    monkeypatch.setattr(
        search_code_module,
        "get_code_retriever",
        lambda: retriever,
    )
    monkeypatch.setattr(
        search_code_module,
        "get_settings",
        lambda: SimpleNamespace(enable_code_semantic_search=True),
    )
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, database),
    )

    with ai_tool_runtime(runtime):
        response = await _search_code_semantic_impl(
            job_id=str(job_id),
            query="find a user by id",
            top_k=3,
            language="python",
            risk_area="database",
        )
        duplicate_read = await _read_file_chunk_impl(
            file_path=file_path,
            job_id=str(job_id),
            chunk_index=2,
            required_chunk_indexes=None,
        )

    assert response == {
        "status": "ok",
        "results": [
            {
                "file_path": file_path,
                "chunk_index": 2,
                "line_start": 40,
                "line_end": 41,
                "function_name": "find_user",
                "class_name": None,
                "language": "python",
                "risk_area": "database",
                "semantic_score": 0.81,
                "content": full_content,
            }
        ],
    }
    assert retriever.job_id == job_id
    assert duplicate_read["status"] == "deduplicated"
    assert "content" not in duplicate_read


class _FakeRetriever:
    def __init__(self, results: list[RetrievedCodeChunk]) -> None:
        self.results = results
        self.job_id: object = None

    def search(self, **kwargs: object) -> list[RetrievedCodeChunk]:
        self.job_id = kwargs["job_id"]
        return self.results


class _FakeMongoCollection:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        if all(self.document.get(key) == value for key, value in query.items()):
            return self.document
        return None


class _FakeMongoDatabase:
    def __init__(self, document: dict[str, object]) -> None:
        self.collection = _FakeMongoCollection(document)

    def __getitem__(self, _name: str) -> _FakeMongoCollection:
        return self.collection
