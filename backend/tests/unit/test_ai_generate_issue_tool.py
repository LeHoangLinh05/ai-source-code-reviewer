"""Tests for AI issue validation safety gates."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.generate_issue import (
    GenerateIssueInput,
    IssueValidationError,
    MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES,
    _adjust_security_confidence,
    _build_source_context,
    _latest_rag_references,
    _latest_kb_grounding,
    _optional_category,
    _optional_severity,
    _normalize_issue_file_path,
    _rag_result_reference,
    _sanitize_kb_visible_text,
    validate_issue_payload,
)


def test_rejects_confidence_below_threshold() -> None:
    with pytest.raises(IssueValidationError, match="confidence"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="medium",
            category="bug",
            title="Possible bug",
            description="The issue is not certain enough.",
            suggestion=None,
            confidence=0.69,
            references=[],
        )


def test_security_issue_requires_knowledge_base_reference(tmp_path: Path) -> None:
    source_path = tmp_path / "auth.py"
    source_path.write_text("validate(token)\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )
    with pytest.raises(IssueValidationError, match="knowledge-base reference"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="high",
            category="security",
            title="Missing validation",
            description="Security finding based on concrete code evidence.",
            suggestion=None,
            confidence=0.75,
            references=[],
        )

    with ai_tool_runtime(runtime):
        validate_issue_payload(
            file_path="auth.py",
            line_start=1,
            line_end=1,
            severity="high",
            category="security",
            title="Missing validation",
            description="Security finding grounded by knowledge.",
            suggestion=None,
            confidence=0.8,
            references=["OWASP ASVS"],
        )


def test_caps_security_confidence_without_references() -> None:
    confidence = _adjust_security_confidence(
        category="security",
        confidence=0.95,
        references=[],
    )

    assert confidence == MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES


def test_issue_enum_helpers_normalize_model_case_drift() -> None:
    assert _optional_severity("High") == "high"
    assert _optional_severity(" CRITICAL ") == "critical"
    assert _optional_category("Security") == "security"
    assert _optional_category(" Maintainability ") == "maintainability"
    assert _optional_severity("P0") == "critical"
    assert _optional_severity("P1") == "high"
    assert _optional_severity("P2") == "low"
    assert _normalize_issue_file_path("./backend\\app/auth.py") == (
        "backend/app/auth.py"
    )


def test_generate_issue_schema_normalizes_location_aliases() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "severity": "critical",
            "category": "security",
            "title": "Plaintext password",
            "description": "Password is stored without hashing.",
            "confidence": 0.8,
            "path": "backend/app/auth.py",
            "line_range": "18-22",
            "source": "KB",
        }
    )

    assert payload.file_path == "backend/app/auth.py"
    assert payload.line_start == 18
    assert payload.line_end == 22


def test_kb_visible_text_removes_internal_rule_identifiers() -> None:
    assert (
        _sanitize_kb_visible_text(
            "Password is plaintext (Rule ID: RC-W1-09). Roadmap violation."
        )
        == "Password is plaintext. requirements violation."
    )


def test_rag_result_reference_prefers_source() -> None:
    assert (
        _rag_result_reference({"source": "OWASP ASVS", "content": "Use hashing."})
        == "OWASP ASVS"
    )


def test_rag_result_reference_falls_back_to_content() -> None:
    assert _rag_result_reference({"content": "Use a password hashing algorithm."}) == (
        "Use a password hashing algorithm."
    )


def test_rag_result_reference_accepts_all_knowledge_documents() -> None:
    assert (
        _rag_result_reference(
            {
                "source": "roadmap_bootcamp_v1",
                "metadata": {"doc_type": "roadmap_rule"},
            }
        )
        == "roadmap_bootcamp_v1"
    )


def test_source_context_contains_nearby_numbered_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "one\ntwo\nproblem\nfour\nfive\n",
        encoding="utf-8",
    )
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        context = _build_source_context(
            file_path="app.py",
            line_start=3,
            line_end=3,
            context_radius=1,
        )

    assert context == {"start_line": 2, "lines": ["two", "problem", "four"]}


@pytest.mark.asyncio
async def test_latest_rag_references_reads_latest_search_trace(tmp_path: Path) -> None:
    job_id = uuid4()
    session_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=session_id,
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(
                {
                    "output": {
                        "status": "ok",
                        "results": [
                            {"source": "OWASP ASVS", "content": "Use hashing."},
                            {"content": "Store passwords with a slow hash."},
                        ],
                    }
                }
            ),
        ),
    )

    with ai_tool_runtime(runtime):
        references = await _latest_rag_references()

    assert references == ["OWASP ASVS", "Store passwords with a slow hash."]


@pytest.mark.asyncio
async def test_kb_grounding_accepts_structure_checklist(tmp_path: Path) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _StructureGroundingDatabase(),
        ),
    )

    with ai_tool_runtime(runtime):
        references, metadata = await _latest_kb_grounding()

    assert references == ["knowledge_base checklist"]
    assert metadata == {"knowledge_doc_type": "roadmap_rule"}


def test_accepts_requirement_category_for_structural_kb_issue(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        validate_issue_payload(
            file_path="pyproject.toml",
            line_start=1,
            line_end=1,
            severity="high",
            category="requirement",
            title="Required dependency is missing",
            description="The required runtime dependency is not declared.",
            suggestion=None,
            confidence=1.0,
            references=["Project requirements"],
        )


class _FakeMongoDatabase:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.collection = _FakeMongoCollection(document)

    def __getitem__(self, _name: str) -> "_FakeMongoCollection":
        return self.collection


class _FakeMongoCollection:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document

    async def find_one(
        self,
        _query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> dict[str, object] | None:
        _ = sort
        return self.document


class _StructureGroundingDatabase:
    def __getitem__(self, _name: str) -> "_StructureGroundingCollection":
        return _StructureGroundingCollection()


class _StructureGroundingCollection:
    async def find_one(
        self,
        query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> dict[str, object] | None:
        _ = sort
        if query.get("tool_name") == "search_knowledge_base":
            return None
        return {"output": {"roadmap": {"applicable_rule_ids": ["RC-W1-17"]}}}


def test_rejects_line_range_that_does_not_exist(tmp_path: Path) -> None:
    source_path = tmp_path / "app.py"
    source_path.write_text("print('hello')\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with (
        ai_tool_runtime(runtime),
        pytest.raises(
            IssueValidationError,
            match="does not exist",
        ),
    ):
        validate_issue_payload(
            file_path="app.py",
            line_start=2,
            line_end=2,
            severity="medium",
            category="bug",
            title="Invalid line",
            description="The line does not exist in the file.",
            suggestion=None,
            confidence=0.8,
            references=[],
        )


def test_rejects_file_path_that_does_not_exist(tmp_path: Path) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with (
        ai_tool_runtime(runtime),
        pytest.raises(
            IssueValidationError,
            match="File does not exist in sandbox",
        ),
    ):
        validate_issue_payload(
            file_path="backend/websocket.py",
            line_start=1,
            line_end=1,
            severity="high",
            category="bug",
            title="Invented path",
            description="The model must not invent file paths.",
            suggestion=None,
            confidence=0.8,
            references=[],
        )
