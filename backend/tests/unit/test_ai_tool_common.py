"""Tests for shared AI tool helpers."""

import importlib
from pathlib import Path
from typing import cast
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.analyze_structure import (
    AnalyzeProjectStructureInput,
    _compact_roadmap_context,
)
from app.ai.tools.common import parse_job_uuid, parse_job_uuid_or_current
from app.ai.tools.generate_issue import GenerateIssueInput
from app.ai.tools.generate_report import (
    GenerateFinalReportInput,
    _count_reviewed_chunks,
    _expected_chunk_keys,
    _is_placeholder_summary,
)
from app.ai.tools.read_file import (
    ReadFileChunkInput,
    _build_rejected_read_response,
    _closest_file_path_suggestions,
    _first_unread_required_chunk_index,
    _read_file_chunk_impl,
    normalize_read_file_input,
)
from app.ai.tools.search_code import SearchCodeSemanticInput
from app.ai.tools.search_rag import (
    SearchCodingStandardInput,
    SearchKnowledgeBaseInput,
)
from app.db.mongodb import CHUNK_METADATA_COLLECTION
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import calculate_report_scores

read_file_module = importlib.import_module("app.ai.tools.read_file")


def test_parse_job_uuid_strips_model_output_whitespace() -> None:
    job_id = uuid4()

    assert parse_job_uuid(f"  {job_id}  ") == job_id


def test_parse_job_uuid_accepts_json_shaped_tool_input() -> None:
    job_id = uuid4()

    assert parse_job_uuid(f'{{"job_id": "{job_id}"}}') == job_id


def test_parse_job_uuid_or_current_handles_empty_agent_input() -> None:
    job_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=Path("."),
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        assert parse_job_uuid_or_current(None) == job_id
        assert parse_job_uuid_or_current("") == job_id
        assert parse_job_uuid_or_current("{}") == job_id
        assert parse_job_uuid_or_current("None") == job_id
        assert parse_job_uuid_or_current("null") == job_id
        assert parse_job_uuid_or_current('{"job_id": ""}') == job_id


def test_read_file_chunk_schema_unwraps_react_json_from_first_field() -> None:
    payload = ReadFileChunkInput.model_validate(
        {"job_id": '{"file_path": "frontend/auth.ts", "chunk_index": 0}'}
    )

    assert payload.file_path == "frontend/auth.ts"
    assert payload.chunk_index == 0
    assert payload.job_id is None


def test_read_file_chunk_schema_unwraps_react_json_from_file_path() -> None:
    payload = ReadFileChunkInput.model_validate(
        {"file_path": '{"file_path": "frontend/auth.ts", "chunk_index": 0}'}
    )

    assert payload.file_path == "frontend/auth.ts"
    assert payload.chunk_index == 0
    assert payload.job_id is None


def test_read_file_chunk_schema_accepts_required_chunk_indexes() -> None:
    payload = ReadFileChunkInput.model_validate(
        {"file_path": "backend/app/auth.py", "required_chunk_indexes": [0, 2, 3]}
    )

    assert payload.chunk_index is None
    assert payload.required_chunk_indexes == [0, 2, 3]


def test_normalize_read_file_input_unwraps_raw_react_string() -> None:
    file_path, job_id, chunk_index, required_chunk_indexes = normalize_read_file_input(
        file_path='{"file_path": "Backend/app/agents/base.py", "chunk_index": 0}',
        job_id=None,
        chunk_index=None,
    )

    assert file_path == "Backend/app/agents/base.py"
    assert chunk_index == 0
    assert job_id is None
    assert required_chunk_indexes is None


def test_normalize_read_file_input_unwraps_required_chunk_indexes() -> None:
    file_path, _job_id, chunk_index, required_chunk_indexes = normalize_read_file_input(
        file_path=(
            '{"file_path": "backend/app/auth.py", '
            '"required_chunk_indexes": [0, "2", "bad"]}'
        ),
        job_id=None,
        chunk_index=None,
    )

    assert file_path == "backend/app/auth.py"
    assert chunk_index is None
    assert required_chunk_indexes == [0, 2]


def test_first_unread_required_chunk_index_skips_reviewed_indexes() -> None:
    assert (
        _first_unread_required_chunk_index(
            required_chunk_indexes=[0, 2, 3],
            reviewed_indexes={0, 2},
        )
        == 3
    )
    assert (
        _first_unread_required_chunk_index(
            required_chunk_indexes=[0],
            reviewed_indexes={0},
        )
        is None
    )


def test_read_file_chunk_builds_rejected_response_for_invalid_chunk() -> None:
    response = _build_rejected_read_response(
        file_path="Backend/app/models/study_goal.py",
        reason=(
            "chunk_index 2 out of range for Backend/app/models/study_goal.py; "
            "total_chunks=2"
        ),
        requested_chunk_index=2,
    )

    assert response["status"] == "rejected"
    assert response["requested_chunk_index"] == 2
    assert response["file_path"] == "Backend/app/models/study_goal.py"


def test_read_file_chunk_rejection_can_include_path_suggestions() -> None:
    response = _build_rejected_read_response(
        file_path="backend/app/verify_logic_errors.py",
        reason="File does not exist in sandbox: backend/app/verify_logic_errors.py",
        requested_chunk_index=0,
        file_path_suggestions=["backend/app/auth.py"],
    )

    assert response["file_path_suggestions"] == ["backend/app/auth.py"]
    assert "Retry read_file_chunk" in str(response["next_action"])


def test_closest_file_path_suggestions_match_similar_paths() -> None:
    suggestions = _closest_file_path_suggestions(
        requested_file_path="backend/app/verify_logic_errors.py",
        known_file_paths=[
            "backend/app/auth.py",
            "backend/app/services/logic_errors.py",
            "backend/app/main.py",
        ],
    )

    assert suggestions[0] == "backend/app/services/logic_errors.py"


def test_closest_file_path_suggestions_match_unique_basename() -> None:
    suggestions = _closest_file_path_suggestions(
        requested_file_path="backend/database.py",
        known_file_paths=[
            "backend/app/auth.py",
            "backend/app/database.py",
            "backend/app/products.py",
        ],
    )

    assert suggestions[0] == "backend/app/database.py"


@pytest.mark.asyncio
async def test_read_file_chunk_resolves_unique_basename_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = uuid4()
    file_path = "backend/app/database.py"
    source_path = tmp_path / file_path
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "def add_user(password):\n    return password\n", encoding="utf-8"
    )
    database = _ReadFileMongoDatabase(
        [
            {
                "job_id": str(job_id),
                "file_path": file_path,
                "chunk_index": 3,
                "total_chunks": 4,
                "line_start": 12,
                "line_end": 16,
                "language": "python",
                "chunk_type": "function",
                "function_name": "add_user",
                "class_name": None,
                "module": "backend.app.database",
                "risk_area": "database",
                "imports": [],
                "token_count": 8,
                "chunk_text": "def add_user(password):\n    return password\n",
            }
        ]
    )

    async def active_job() -> None:
        return None

    monkeypatch.setattr(read_file_module, "ensure_ai_job_active", active_job)
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, database),
    )

    with ai_tool_runtime(runtime):
        response = await _read_file_chunk_impl(
            file_path="backend/database.py",
            job_id=str(job_id),
            chunk_index=3,
            required_chunk_indexes=None,
        )

    assert response["status"] == "ok"
    assert response["file_path"] == "backend/app/database.py"
    assert response["requested_file_path"] == "backend/database.py"
    assert response["path_resolution"] == "unique_file_path_suggestion"


def test_search_schema_unwraps_react_json_from_first_field() -> None:
    payload = SearchCodingStandardInput.model_validate(
        {"query": '{"query": "csrf token", "language": "typescript", "top_k": 2}'}
    )

    assert payload.query == "csrf token"
    assert payload.language == "typescript"
    assert payload.top_k == 2


def test_search_knowledge_schema_clamps_oversized_top_k() -> None:
    payload = SearchKnowledgeBaseInput.model_validate(
        {
            "query": "roadmap rules for profile roadmap_bootcamp_v1",
            "doc_type": "roadmap_rule",
            "profile_id": "roadmap_bootcamp_v1",
            "top_k": 10,
        }
    )

    assert payload.top_k == 5


def test_search_code_schema_accepts_file_path_only_input() -> None:
    payload = SearchCodeSemanticInput.model_validate(
        {"file_path": "backend/app/auth.py"}
    )

    assert payload.job_id is None
    assert payload.file_path == "backend/app/auth.py"
    assert payload.query == "Review relevant behavior in backend/app/auth.py"


def test_analyze_project_structure_schema_accepts_empty_input() -> None:
    payload = AnalyzeProjectStructureInput.model_validate({})

    assert payload.job_id is None


def test_analyze_project_structure_schema_accepts_react_none_input() -> None:
    payload = AnalyzeProjectStructureInput.model_validate({"input": "None"})

    assert payload.job_id is None


def test_compact_roadmap_context_omits_full_rule_lists() -> None:
    compact = _compact_roadmap_context(
        {
            "profile_id": "roadmap_bootcamp_v1",
            "weeks_included": None,
            "applicable_rule_ids": ["RC-W1-01", "RC-W1-10"],
            "review_rules": [
                {
                    "rule_id": "RC-W1-01",
                    "review_category": "structure",
                    "requirement": "Dùng FastAPI",
                },
                {
                    "rule_id": "RC-W1-10",
                    "review_category": "security",
                    "requirement": "Endpoint /login",
                },
            ],
            "ai_verification_rules": [
                {
                    "rule_id": "RC-W1-10",
                    "review_category": "security",
                    "requirement": "Endpoint /login",
                }
            ],
        }
    )

    assert "review_rules" not in compact
    assert "ai_verification_rules" not in compact
    assert compact["review_rule_count"] == 2
    assert compact["ai_verification_rule_count"] == 1
    assert compact["review_category_counts"] == {"security": 1, "structure": 1}


def test_generate_issue_schema_unwraps_react_json_from_first_field() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "severity": (
                '{"severity": "high", "category": "security", "title": "Weak auth", '
                '"description": "Missing authorization check.", "confidence": 0.8, '
                '"references": ["OWASP ASVS"]}'
            )
        }
    )

    assert payload.severity == "high"
    assert payload.category == "security"
    assert payload.references == ["OWASP ASVS"]


def test_generate_issue_schema_unwraps_react_json_from_input_field() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "input": (
                "{\n"
                '  "severity": "High",\n'
                '  "category": "Security",\n'
                '  "title": "Plaintext password",\n'
                '  "description": "Password is stored in plaintext.",\n'
                '  "confidence": 0.9,\n'
                '  "file_path": "backend/app/auth.py",\n'
                '  "line_start": 10,\n'
                '  "line_end": 12\n'
                "}"
            )
        }
    )

    assert payload.severity == "High"
    assert payload.category == "Security"
    assert payload.title == "Plaintext password"
    assert payload.confidence == 0.9
    assert payload.file_path == "backend/app/auth.py"


def test_generate_issue_schema_extracts_json_from_multi_action_markdown() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "input": (
                "**Action Input:**\n```json\n"
                '{"severity":"high","category":"bug","title":"Bad state",'
                '"description":"State is stale.","confidence":0.8,'
                '"path":"app.py","line_range":"10-12"}\n```\n'
                "**Observation:** generated\n**Action: generate_issue**\n"
                "**Action Input:**\n```json\n{}\n```"
            )
        }
    )

    assert payload.title == "Bad state"
    assert payload.file_path == "app.py"
    assert payload.line_start == 10
    assert payload.line_end == 12


def test_generate_issue_schema_unwraps_json_with_trailing_react_text() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "severity": (
                '{"severity": "medium", "category": "bug", "title": "Bad state", '
                '"description": "State can become stale.", "confidence": 0.75}'
                "\nAction: generate_final_report\nAction Input: {}"
            )
        }
    )

    assert payload.severity == "medium"
    assert payload.category == "bug"
    assert payload.title == "Bad state"


def test_generate_issue_schema_accepts_malformed_issue_for_tool_rejection() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "severity": (
                '{"category": "security", "title": "Missing refs"}'
                "\nAction: generate_final_report\nAction Input: {}"
            )
        }
    )

    assert payload.severity is None
    assert payload.category == "security"


def test_generate_issue_schema_accepts_direct_dict_missing_optional_fields() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "category": "security",
            "description": "The endpoint may expose private data.",
            "file_path": "app/api/users.py",
            "line_start": 117,
            "line_end": 169,
        }
    )

    assert payload.severity is None
    assert payload.title is None
    assert payload.confidence is None
    assert payload.category == "security"


def test_generate_report_schema_unwraps_react_json_from_first_field() -> None:
    payload = GenerateFinalReportInput.model_validate(
        {
            "job_id": (
                '{"executive_summary": "Done", "security_score": 8, '
                '"maintainability_score": 7, "performance_score": 9, '
                '"overall_score": 8}'
            )
        }
    )

    assert payload.job_id is None
    assert payload.executive_summary == "Done"
    assert payload.overall_score == 8


def test_generate_report_schema_unwraps_markdown_json_from_input_field() -> None:
    payload = GenerateFinalReportInput.model_validate(
        {
            "input": (
                "```json\n"
                "{\n"
                '  "executive_summary": "Done",\n'
                '  "security_score": 7,\n'
                '  "maintainability_score": 6,\n'
                '  "performance_score": 8,\n'
                '  "overall_score": 7\n'
                "}\n"
                "```"
            )
        }
    )

    assert payload.executive_summary == "Done"
    assert payload.security_score == 7
    assert payload.maintainability_score == 6
    assert payload.performance_score == 8
    assert payload.overall_score == 7


def test_generate_report_schema_accepts_tech_stack_list() -> None:
    payload = GenerateFinalReportInput.model_validate(
        {
            "executive_summary": "Done",
            "security_score": 7,
            "maintainability_score": 6,
            "performance_score": 8,
            "overall_score": 7,
            "tech_stack": ["Python", "Flask", "Redis", "MongoDB"],
        }
    )

    assert payload.tech_stack == ["Python", "Flask", "Redis", "MongoDB"]


def test_generate_report_schema_accepts_structured_top_priorities() -> None:
    payload = GenerateFinalReportInput.model_validate(
        {
            "executive_summary": "Done",
            "security_score": 7,
            "maintainability_score": 6,
            "performance_score": 8,
            "overall_score": 7,
            "top_priorities": [
                {
                    "severity": "critical",
                    "category": "requirement",
                    "source": "KB",
                    "title": "Missing FastAPI implementation",
                    "file_path": "requirements.txt",
                    "line_start": 1,
                },
                {
                    "severity": "high",
                    "category": "security",
                    "title": "Missing authorization",
                },
                "src/app.py",
            ],
        }
    )

    assert payload.top_priorities == [
        "requirements.txt",
        "high | security | Missing authorization",
        "src/app.py",
    ]


def test_generate_report_detects_placeholder_summary() -> None:
    assert _is_placeholder_summary("(as above)\n### Final Answer")
    assert _is_placeholder_summary("(the JSON input above)")
    assert _is_placeholder_summary("The final report has been successfully generated.")
    assert not _is_placeholder_summary("AI review found two high security issues.")


def test_generate_report_fallback_scores_from_persisted_issues() -> None:
    scores = calculate_report_scores(
        [
            _normalized_issue(
                severity=IssueSeverity.HIGH,
                category=IssueCategory.SECURITY,
            ),
            _normalized_issue(
                severity=IssueSeverity.MEDIUM,
                category=IssueCategory.PERFORMANCE,
            ),
            _normalized_issue(
                severity=IssueSeverity.LOW,
                category=IssueCategory.MAINTAINABILITY,
            ),
        ]
    )

    assert scores["security_score"] == 8.0
    assert scores["performance_score"] == 9.0
    assert scores["maintainability_score"] == 9.7
    assert scores["overall_score"] == 6.7


def test_generate_report_schema_accepts_empty_input_for_tool_rejection() -> None:
    payload = GenerateFinalReportInput.model_validate({})

    assert payload.executive_summary is None
    assert payload.security_score is None
    assert payload.overall_score is None


def test_generate_report_coverage_helpers_count_unique_valid_chunks() -> None:
    expected = _expected_chunk_keys(
        [
            {"file_path": "app/a.py", "chunk_index": 0},
            {"file_path": "app/a.py", "chunk_index": 1},
            {"file_path": "app/b.py", "chunk_index": 0},
            {"file_path": "app/b.py", "chunk_index": "bad"},
        ]
    )
    reviewed = _count_reviewed_chunks(
        [
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "ok",
                    "file_path": "app/a.py",
                    "chunk_index": 0,
                },
            },
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "ok",
                    "file_path": "app/a.py",
                    "chunk_index": 0,
                },
            },
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "rejected",
                    "file_path": "app/a.py",
                    "chunk_index": 1,
                },
            },
            {
                "tool_name": "search_code_semantic",
                "output": {
                    "status": "ok",
                    "results": [
                        {
                            "status": "ok",
                            "file_path": "app/a.py",
                            "chunk_index": 1,
                            "line_start": 10,
                            "line_end": 20,
                        }
                    ],
                },
            },
        ]
    )

    assert expected == {("app/a.py", 0), ("app/a.py", 1), ("app/b.py", 0)}
    assert reviewed == 1


class _ReadFileMongoDatabase:
    def __init__(self, chunk_documents: list[dict[str, object]]) -> None:
        self.chunk_documents = chunk_documents

    def __getitem__(self, name: str) -> "_ReadFileMongoCollection":
        documents = self.chunk_documents if name == CHUNK_METADATA_COLLECTION else []
        return _ReadFileMongoCollection(documents)


class _ReadFileMongoCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        for document in self.documents:
            if _matches_query(document, query):
                return document
        return None

    def find(
        self,
        query: dict[str, object],
        *_args: object,
    ) -> "_ReadFileMongoCursor":
        return _ReadFileMongoCursor(
            [document for document in self.documents if _matches_query(document, query)]
        )


class _ReadFileMongoCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, object]]:
        _ = length
        return self.documents


def _matches_query(
    document: dict[str, object],
    query: dict[str, object],
) -> bool:
    return all(document.get(key) == value for key, value in query.items())


def _normalized_issue(
    *,
    severity: IssueSeverity,
    category: IssueCategory,
) -> NormalizedIssue:
    return NormalizedIssue(
        file_path="app.py",
        line_start=1,
        line_end=1,
        severity=severity,
        category=category,
        title="Issue",
        description="Description",
        source=IssueSource.AI_REVIEW,
        confidence=0.8,
    )
