"""Tests for preview-first semantic code search tool output."""

from pathlib import Path
import importlib
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

import app.ai.tools.read_file as read_file_module
from app.ai.rag.code_retriever import RetrievedCodeChunk
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.read_file import _read_file_chunk_impl
from app.ai.tools.search_code import (
    DEFAULT_SEMANTIC_CODE_QUERY,
    _is_review_source_path,
    _normalize_investigation_id,
    _search_code_impl,
    _semantic_query,
    _search_code_semantic_impl,
)


@pytest.mark.asyncio
async def test_missing_investigation_id_gets_stable_query_default() -> None:
    first = _normalize_investigation_id(
        investigation_id=None,
        rule_id=None,
        query="Verify whether authentication behavior is implemented",
    )
    second = _normalize_investigation_id(
        investigation_id=" ",
        rule_id=None,
        query="Verify whether authentication behavior is implemented",
    )

    assert first == second
    assert first.startswith("auto-authentication-behavior")


@pytest.mark.asyncio
async def test_missing_investigation_id_uses_roadmap_rule_id() -> None:
    first = _normalize_investigation_id(
        investigation_id=None,
        rule_id=None,
        query=(
            "Verify whether the repository implements these related roadmap "
            "requirements end-to-end; skill group: Backend Core & JWT Auth; "
            "rule RC-W1-10; requirement: Endpoint /login"
        ),
    )
    second = _normalize_investigation_id(
        investigation_id=None,
        rule_id=None,
        query=(
            "Verify whether the repository implements these related roadmap "
            "requirements end-to-end; skill group: Backend Core & JWT Auth; "
            "rule RC-W1-11; requirement: Endpoint /token/refresh"
        ),
    )

    assert first == "auto-rc-w1-10"
    assert second == "auto-rc-w1-11"


@pytest.mark.asyncio
async def test_missing_investigation_id_prefers_explicit_roadmap_rule_id() -> None:
    assert (
        _normalize_investigation_id(
            investigation_id=None,
            rule_id="RC-W1-04",
            query="Có thư viện JWT",
        )
        == "rc-w1-04"
    )


@pytest.mark.asyncio
async def test_missing_investigation_id_hash_avoids_prefix_collision() -> None:
    first = _normalize_investigation_id(
        investigation_id=None,
        rule_id=None,
        query="Review authentication behavior in app/auth.py",
    )
    second = _normalize_investigation_id(
        investigation_id=None,
        rule_id=None,
        query="Review authentication behavior in app/tokens.py",
    )

    assert first != second
    assert first.startswith("auto-review-authentication-be")
    assert second.startswith("auto-review-authentication-be")


@pytest.mark.asyncio
async def test_job_id_cannot_be_used_as_investigation_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()

    async def active_job() -> None:
        return None

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        response = await _search_code_impl(
            investigation_id=str(job_id),
            job_id=str(job_id),
            query="verify login behavior",
            mode="auto",
            file_path=None,
            audit_plan_item_id=None,
            top_k=3,
            language=None,
            risk_area=None,
        )

    assert response["status"] == "invalid_investigation_scope"
    assert "distinct ID" in str(response["next_action"])


search_code_module = importlib.import_module("app.ai.tools.search_code")


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
    assert _semantic_query(query=None, file_path=None) == DEFAULT_SEMANTIC_CODE_QUERY
    assert _semantic_query(query="  ", file_path="") == DEFAULT_SEMANTIC_CODE_QUERY


def test_review_source_path_filter_excludes_docs_and_markdown_specs() -> None:
    assert _is_review_source_path("backend/app/auth.py")
    assert _is_review_source_path("frontend/middleware.ts")
    assert _is_review_source_path("docker-compose.yml")
    assert _is_review_source_path("requirements.txt")
    assert not _is_review_source_path("roadmap_ai_rules_test_spec.md")
    assert not _is_review_source_path("docs/examples/auth.py")


@pytest.mark.asyncio
async def test_search_code_semantic_returns_preview_and_read_priority(
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
            "total_chunks": 3,
            "line_start": 40,
            "line_end": 41,
            "language": "python",
            "chunk_type": "function",
            "function_name": "find_user",
            "class_name": None,
            "module": "app.repositories.user",
            "risk_area": "database",
            "imports": [],
            "token_count": 12,
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

    assert response["status"] == "ok"
    assert response["investigation_id"] == "legacy-semantic-search"
    assert response["requested_mode"] == "semantic"
    assert response["strategy_used"] == ["semantic"]
    results = response["results"]
    assert isinstance(results, list)
    assert results == [
        {
            "result_index": 1,
            "file_path": file_path,
            "chunk_index": 2,
            "line_start": 40,
            "line_end": 41,
            "function_name": "find_user",
            "class_name": None,
            "language": "python",
            "risk_area": "database",
            "semantic_score": 0.81,
            "lexical_score": None,
            "final_score": 0.81,
            "preview": full_content.rstrip(),
            "preview_truncated": False,
            "evidence_status": "preview_only",
        }
    ]
    assert retriever.job_id == str(job_id)
    assert duplicate_read["status"] == "ok"
    assert duplicate_read["content"] == full_content


@pytest.mark.asyncio
async def test_repeated_semantic_query_returns_duplicate_without_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()
    retriever = _FakeRetriever([])

    async def active_job() -> None:
        return None

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    monkeypatch.setattr(search_code_module, "get_code_retriever", lambda: retriever)
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
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        first = await _search_code_semantic_impl(
            job_id=str(job_id),
            query="find token revocation",
            top_k=3,
            language=None,
            risk_area=None,
        )
        second = await _search_code_semantic_impl(
            job_id=str(job_id),
            query="find token revocation",
            top_k=3,
            language=None,
            risk_area=None,
        )

    assert first["status"] == "ok"
    assert second["status"] == "duplicate_query"
    assert second["previous_query_id"] == first["query_id"]


@pytest.mark.asyncio
async def test_exact_search_finds_literal_without_semantic_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()
    file_path = "app/api/auth.py"
    source_path = tmp_path / file_path
    source_path.parent.mkdir(parents=True)
    content = '@router.post("/logout")\nasync def logout(): pass\n'
    source_path.write_text(content, encoding="utf-8")
    database = _FakeMongoDatabase(
        {
            "job_id": str(job_id),
            "file_path": file_path,
            "chunk_index": 0,
            "line_start": 1,
            "line_end": 2,
            "language": "python",
            "risk_area": "api",
            "module": "app.api.auth",
            "imports": [],
            "chunk_text": content,
        }
    )

    async def active_job() -> None:
        return None

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    monkeypatch.setattr(
        search_code_module,
        "get_settings",
        lambda: SimpleNamespace(enable_code_semantic_search=False),
    )
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, database),
    )

    with ai_tool_runtime(runtime):
        response = await _search_code_impl(
            investigation_id="logout-flow",
            job_id=str(job_id),
            query="/logout",
            mode="exact",
            file_path=None,
            audit_plan_item_id="category_review:security:1",
            top_k=3,
            language="python",
            risk_area=None,
        )

    assert response["status"] == "ok"
    assert response["strategy_used"] == ["exact"]
    assert response["audit_plan_item_id"] == "category_review:security:1"
    results = response["results"]
    assert isinstance(results, list)
    assert results[0]["file_path"] == file_path
    assert results[0]["evidence_status"] == "preview_only"


@pytest.mark.asyncio
async def test_search_code_semantic_validates_repo_branch_cached_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()
    file_path = "app/services/user.py"
    full_content = "def create_user():\n    return True\n"
    source_path = tmp_path / file_path
    source_path.parent.mkdir(parents=True)
    source_path.write_text(full_content, encoding="utf-8")
    embedding_cache_id = "cache-id-1"
    repo_branch_key = "repo-branch-key"
    result = RetrievedCodeChunk(
        content=full_content,
        metadata={
            "repo_branch_key": repo_branch_key,
            "embedding_cache_id": embedding_cache_id,
            "file_path": file_path,
            "chunk_index": 0,
            "line_start": 1,
            "line_end": 2,
            "function_name": "create_user",
            "class_name": "",
            "language": "python",
            "risk_area": "general",
        },
        semantic_score=0.9,
    )
    retriever = _FakeRetriever([result])
    database = _FakeMongoDatabase(
        {
            "job_id": str(job_id),
            "repo_branch_key": repo_branch_key,
            "embedding_cache_id": embedding_cache_id,
            "file_path": file_path,
            "chunk_index": 0,
            "total_chunks": 1,
            "line_start": 1,
            "line_end": 2,
            "language": "python",
            "chunk_type": "function",
            "function_name": "create_user",
            "class_name": None,
            "module": "app.services.user",
            "risk_area": "general",
            "imports": [],
            "token_count": 8,
            "chunk_text": full_content,
        }
    )

    async def active_job() -> None:
        return None

    monkeypatch.setattr(search_code_module, "ensure_ai_job_active", active_job)
    monkeypatch.setattr(search_code_module, "get_code_retriever", lambda: retriever)
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
            query="create user",
            top_k=3,
            language=None,
            risk_area=None,
        )

    assert response["status"] == "ok"
    assert retriever.repo_branch_key == repo_branch_key
    assert database.collection.find_one_queries[-1] == {
        "job_id": str(job_id),
        "embedding_cache_id": embedding_cache_id,
    }


class _FakeRetriever:
    def __init__(self, results: list[RetrievedCodeChunk]) -> None:
        self.results = results
        self.job_id: object = None
        self.repo_branch_key: object = None

    def search(self, **kwargs: object) -> list[RetrievedCodeChunk]:
        self.job_id = kwargs["job_id"]
        self.repo_branch_key = kwargs.get("repo_branch_key")
        return self.results


class _FakeMongoCollection:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.find_one_queries: list[dict[str, object]] = []

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        self.find_one_queries.append(query)
        if all(self.document.get(key) == value for key, value in query.items()):
            return self.document
        return None

    def find(self, query: dict[str, object]) -> "_FakeMongoCursor":
        if all(self.document.get(key) == value for key, value in query.items()):
            return _FakeMongoCursor([self.document])
        return _FakeMongoCursor([])


class _FakeMongoCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, object]]:
        _ = length
        return self.documents


class _FakeMongoDatabase:
    def __init__(self, document: dict[str, object]) -> None:
        self.collection = _FakeMongoCollection(document)

    def __getitem__(self, _name: str) -> _FakeMongoCollection:
        return self.collection
