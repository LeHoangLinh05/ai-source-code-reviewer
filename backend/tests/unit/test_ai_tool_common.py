"""Tests for shared AI tool helpers."""

from uuid import uuid4

from app.ai.tools.common import parse_job_uuid
from app.ai.tools.generate_issue import GenerateIssueInput
from app.ai.tools.generate_report import (
    GenerateFinalReportInput,
    _count_reviewed_chunks,
    _expected_chunk_keys,
)
from app.ai.tools.read_file import (
    ReadFileChunkInput,
    _build_rejected_read_response,
    normalize_read_file_input,
)
from app.ai.tools.search_rag import SearchCodingStandardInput


def test_parse_job_uuid_strips_model_output_whitespace() -> None:
    job_id = uuid4()

    assert parse_job_uuid(f"  {job_id}  ") == job_id


def test_parse_job_uuid_accepts_json_shaped_tool_input() -> None:
    job_id = uuid4()

    assert parse_job_uuid(f'{{"job_id": "{job_id}"}}') == job_id


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


def test_normalize_read_file_input_unwraps_raw_react_string() -> None:
    file_path, job_id, chunk_index = normalize_read_file_input(
        file_path='{"file_path": "Backend/app/agents/base.py", "chunk_index": 0}',
        job_id=None,
        chunk_index=None,
    )

    assert file_path == "Backend/app/agents/base.py"
    assert chunk_index == 0
    assert job_id is None


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


def test_search_schema_unwraps_react_json_from_first_field() -> None:
    payload = SearchCodingStandardInput.model_validate(
        {"query": '{"query": "csrf token", "language": "typescript", "top_k": 2}'}
    )

    assert payload.query == "csrf token"
    assert payload.language == "typescript"
    assert payload.top_k == 2


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
                "output": {
                    "status": "ok",
                    "file_path": "app/a.py",
                    "chunk_index": 0,
                }
            },
            {
                "output": {
                    "status": "ok",
                    "file_path": "app/a.py",
                    "chunk_index": 0,
                }
            },
            {
                "output": {
                    "status": "rejected",
                    "file_path": "app/a.py",
                    "chunk_index": 1,
                }
            },
        ]
    )

    assert expected == {("app/a.py", 0), ("app/a.py", 1), ("app/b.py", 0)}
    assert reviewed == 1
