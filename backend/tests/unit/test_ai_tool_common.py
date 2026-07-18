"""Tests for shared AI report helpers."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.review_coverage import load_chunk_review_coverage
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.common import parse_job_uuid, parse_job_uuid_or_current
from app.ai.tools.generate_report import (
    GenerateFinalReportInput,
    _is_placeholder_summary,
)
from app.db.mongodb import CHUNK_METADATA_COLLECTION, TOOL_CALL_LOGS_COLLECTION
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import calculate_report_scores


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


@pytest.mark.asyncio
async def test_review_coverage_counts_probe_retrieval_chunks() -> None:
    job_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=Path("."),
        postgres_session=cast(AsyncSession, _FakePostgresSession()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(
                chunks=[
                    _chunk(job_id=job_id, file_path="app/a.py", chunk_index=0),
                    _chunk(job_id=job_id, file_path="app/a.py", chunk_index=1),
                    _chunk(job_id=job_id, file_path="app/b.py", chunk_index=0),
                ],
                tool_logs=[
                    {
                        "job_id": str(job_id),
                        "tool_name": "probe_retrieval",
                        "output": {
                            "status": "ok",
                            "results": [
                                {
                                    "file_path": "app/a.py",
                                    "chunk_index": 0,
                                },
                                {
                                    "file_path": "app/a.py",
                                    "chunk_index": 0,
                                },
                            ],
                        },
                    },
                    {
                        "job_id": str(job_id),
                        "tool_name": "unrelated_source_tool",
                        "output": {
                            "status": "ok",
                            "file_path": "app/a.py",
                            "chunk_index": 1,
                        },
                    },
                ],
            ),
        ),
    )

    with ai_tool_runtime(runtime):
        reviewed, total, missing = await load_chunk_review_coverage(job_id)

    assert reviewed == 1
    assert total == 3
    assert missing == [
        {"file_path": "app/a.py", "chunk_index": 1},
        {"file_path": "app/b.py", "chunk_index": 0},
    ]


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


def _chunk(*, job_id: UUID, file_path: str, chunk_index: int) -> dict[str, object]:
    return {
        "job_id": str(job_id),
        "file_path": file_path,
        "chunk_index": chunk_index,
        "line_start": chunk_index + 1,
        "line_end": chunk_index + 1,
    }


class _FakePostgresSession:
    async def execute(self, _statement: object) -> "_FakeScalarResult":
        return _FakeScalarResult()


class _FakeScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeMongoDatabase:
    def __init__(
        self,
        *,
        chunks: list[dict[str, object]],
        tool_logs: list[dict[str, object]],
    ) -> None:
        self.collections = {
            CHUNK_METADATA_COLLECTION: chunks,
            TOOL_CALL_LOGS_COLLECTION: tool_logs,
        }

    def __getitem__(self, name: str) -> "_FakeMongoCollection":
        return _FakeMongoCollection(self.collections.get(name, []))


class _FakeMongoCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self, query: dict[str, object]) -> "_FakeMongoCursor":
        return _FakeMongoCursor(
            [document for document in self.documents if _matches_query(document, query)]
        )


class _FakeMongoCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, object]]:
        _ = length
        return self.documents


def _matches_query(
    document: dict[str, object],
    query: dict[str, object],
) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            values = expected["$in"]
            if not isinstance(values, list) or document.get(key) not in values:
                return False
            continue
        if document.get(key) != expected:
            return False
    return True
