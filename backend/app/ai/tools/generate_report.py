"""AI tool for producing final review reports."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator
from sqlalchemy import delete, select

from app.ai.review_plan import (
    all_chunk_keys,
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)
from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    parse_job_uuid,
    unwrap_react_json_input,
)
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    ROADMAP_COMPLIANCE_RESULTS_COLLECTION,
    TOOL_CALL_LOGS_COLLECTION,
)
from app.models.review_issue import IssueSeverity, IssueSource, ReviewIssue
from app.models.review_job import ReviewJob
from app.models.review_report import ReviewReport
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import (
    AI_REPORT_MODEL,
    build_top_risky_files,
)


class GenerateFinalReportInput(BaseModel):
    """Input schema for final AI report generation."""

    job_id: str | None = None
    executive_summary: str | None = None
    security_score: float | None = None
    maintainability_score: float | None = None
    performance_score: float | None = None
    overall_score: float | None = None
    top_priorities: list[str] | None = None
    tech_stack: dict[str, object] | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        return unwrap_react_json_input(data, "job_id")


@tool(args_schema=GenerateFinalReportInput)
async def generate_final_report(
    executive_summary: str | None = None,
    security_score: float | None = None,
    maintainability_score: float | None = None,
    performance_score: float | None = None,
    overall_score: float | None = None,
    job_id: str | None = None,
    top_priorities: list[str] | None = None,
    tech_stack: dict[str, object] | None = None,
) -> dict[str, object]:
    """Tổng hợp toàn bộ issue đã tạo trong session thành report cuối. BẮT BUỘC truyền executive_summary, security_score, maintainability_score, performance_score, overall_score; không gọi với Action Input rỗng {}."""

    parsed_input = parse_json_object_text(executive_summary)
    if parsed_input is not None:
        executive_summary = (
            _optional_str(parsed_input.get("executive_summary")) or executive_summary
        )
        job_id = _optional_str(parsed_input.get("job_id")) or job_id
        parsed_security_score = _optional_float(parsed_input.get("security_score"))
        if parsed_security_score is not None:
            security_score = parsed_security_score
        parsed_maintainability_score = _optional_float(
            parsed_input.get("maintainability_score"),
        )
        if parsed_maintainability_score is not None:
            maintainability_score = parsed_maintainability_score
        parsed_performance_score = _optional_float(
            parsed_input.get("performance_score"),
        )
        if parsed_performance_score is not None:
            performance_score = parsed_performance_score
        parsed_overall_score = _optional_float(parsed_input.get("overall_score"))
        if parsed_overall_score is not None:
            overall_score = parsed_overall_score
        top_priorities = _optional_str_list(parsed_input.get("top_priorities"))
        tech_stack = _optional_dict(parsed_input.get("tech_stack")) or tech_stack

    rejection_reason = _report_rejection_reason(
        executive_summary=executive_summary,
        maintainability_score=maintainability_score,
        overall_score=overall_score,
        performance_score=performance_score,
        security_score=security_score,
    )
    if rejection_reason is not None:
        return {"status": "rejected", "reason": rejection_reason}

    assert executive_summary is not None
    assert maintainability_score is not None
    assert overall_score is not None
    assert performance_score is not None
    assert security_score is not None

    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()
    job_uuid = parse_job_uuid(job_id or runtime.job_id)
    reviewed_chunks, total_chunks, missing_chunks = await _load_chunk_review_coverage(
        job_uuid
    )
    if reviewed_chunks < total_chunks:
        return {
            "status": "rejected",
            "reason": (
                "AI final report rejected: chunk review coverage is incomplete "
                f"({reviewed_chunks}/{total_chunks} chunks read). Continue calling "
                "read_file_chunk for the chunk_review_plan target chunks before "
                "trying again."
            ),
            "reviewed_chunks": reviewed_chunks,
            "total_chunks": total_chunks,
            "missing_chunks_preview": missing_chunks,
        }

    issues = await _load_issues(job_uuid)
    normalized_issues = [_to_normalized_issue(issue) for issue in issues]
    severity_counts = Counter(issue.severity for issue in normalized_issues)
    existing_report = await _load_existing_report(job_uuid)
    compliance_score, bonus_score = await _preserved_roadmap_scores(
        job_uuid=job_uuid,
        existing_report=existing_report,
    )
    report = ReviewReport(
        job_id=job_uuid,
        total_files_analyzed=await _total_files_analyzed(job_uuid, existing_report),
        total_issues=len(issues),
        critical_count=severity_counts[IssueSeverity.CRITICAL],
        high_count=severity_counts[IssueSeverity.HIGH],
        medium_count=severity_counts[IssueSeverity.MEDIUM],
        low_count=severity_counts[IssueSeverity.LOW],
        info_count=severity_counts[IssueSeverity.INFO],
        security_score=_clamp_score(security_score),
        maintainability_score=_clamp_score(maintainability_score),
        performance_score=_clamp_score(performance_score),
        overall_score=_clamp_score(overall_score),
        compliance_score=compliance_score,
        bonus_score=bonus_score,
        tech_stack=tech_stack or {},
        top_risky_files=_prioritized_files(normalized_issues, top_priorities),
        executive_summary=executive_summary,
        ai_model_used=AI_REPORT_MODEL,
    )
    await runtime.postgres_session.execute(
        delete(ReviewReport).where(ReviewReport.job_id == job_uuid)
    )
    runtime.postgres_session.add(report)
    await runtime.postgres_session.commit()
    await runtime.postgres_session.refresh(report)
    return {"status": "created", "report_id": str(report.id)}


async def _load_chunk_review_coverage(
    job_id: UUID,
) -> tuple[int, int, list[dict[str, object]]]:
    runtime = get_ai_tool_runtime()
    job_filter = {"job_id": str(job_id)}
    chunk_documents = (
        await runtime.mongodb_database[CHUNK_METADATA_COLLECTION]
        .find(job_filter)
        .to_list(length=None)
    )
    job_options = await _load_job_options(job_id)
    review_mode = get_review_mode(job_options)
    roadmap_document = await runtime.mongodb_database[
        ROADMAP_COMPLIANCE_RESULTS_COLLECTION
    ].find_one(job_filter, sort=[("checked_at", -1)])
    review_plan = build_chunk_review_plan(
        chunk_documents=chunk_documents,
        review_mode=review_mode,
        verification_queue=_verification_queue(roadmap_document),
        max_smart_chunks=get_smart_review_max_chunks(job_options),
    )
    expected_chunks = expected_chunk_keys_from_plan(review_plan)
    if not expected_chunks:
        return 0, 0, []

    tool_documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find({**job_filter, "tool_name": "read_file_chunk"})
        .to_list(length=None)
    )
    reviewed_chunks = _reviewed_chunk_keys(tool_documents)
    missing_chunks = sorted(expected_chunks - reviewed_chunks)[:25]
    return (
        len(reviewed_chunks & expected_chunks),
        len(expected_chunks),
        [
            {"file_path": file_path, "chunk_index": chunk_index}
            for file_path, chunk_index in missing_chunks
        ],
    )


async def _load_job_options(job_id: UUID) -> dict[str, object] | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    return options if isinstance(options, dict) else None


def _verification_queue(
    roadmap_document: dict[str, Any] | None,
) -> list[dict[str, object]]:
    if roadmap_document is None:
        return []

    queue: list[dict[str, object]] = []
    raw_queue = roadmap_document.get("verification_queue", [])
    if not isinstance(raw_queue, list):
        return queue

    for item in raw_queue:
        if not isinstance(item, dict):
            continue
        queue.append(
            {
                "rule_id": str(item.get("rule_id", "")),
                "file_path": str(item.get("file_path", "")),
                "ai_hint": str(item.get("ai_hint", "")),
            }
        )

    return queue


def _expected_chunk_keys(documents: list[object]) -> set[tuple[str, int]]:
    return all_chunk_keys(documents)


def _count_reviewed_chunks(documents: list[object]) -> int:
    return len(_reviewed_chunk_keys(documents))


def _reviewed_chunk_keys(documents: list[object]) -> set[tuple[str, int]]:
    reviewed_chunks: set[tuple[str, int]] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        output = document.get("output", {})
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue

        file_path = output.get("file_path")
        chunk_index = output.get("chunk_index")
        if isinstance(file_path, str) and isinstance(chunk_index, int):
            reviewed_chunks.add((file_path, chunk_index))

    return reviewed_chunks


async def _load_issues(job_id: UUID) -> list[ReviewIssue]:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewIssue).where(ReviewIssue.job_id == job_id)
    )
    return list(result.scalars().all())


async def _load_existing_report(job_id: UUID) -> ReviewReport | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewReport).where(ReviewReport.job_id == job_id)
    )
    return result.scalar_one_or_none()


async def _preserved_roadmap_scores(
    *,
    job_uuid: UUID,
    existing_report: ReviewReport | None,
) -> tuple[float | None, float | None]:
    if existing_report is not None and (
        existing_report.compliance_score is not None
        or existing_report.bonus_score is not None
    ):
        return existing_report.compliance_score, existing_report.bonus_score

    runtime = get_ai_tool_runtime()
    roadmap_document = await runtime.mongodb_database[
        ROADMAP_COMPLIANCE_RESULTS_COLLECTION
    ].find_one({"job_id": str(job_uuid)}, sort=[("checked_at", -1)])
    if roadmap_document is None:
        return None, None

    return _optional_float(roadmap_document.get("compliance_score")), _optional_float(
        roadmap_document.get("bonus_score")
    )


async def _total_files_analyzed(
    job_id: UUID,
    existing_report: ReviewReport | None,
) -> int:
    if existing_report is not None:
        return existing_report.total_files_analyzed

    runtime = get_ai_tool_runtime()
    structure_document = await runtime.mongodb_database[
        FILE_ANALYSIS_RESULTS_COLLECTION
    ].find_one({"job_id": str(job_id)}, sort=[("analyzed_at", -1)])
    if structure_document is None:
        return 0

    file_tree = structure_document.get("file_tree", [])
    return len(file_tree) if isinstance(file_tree, list) else 0


def _prioritized_files(
    issues: list[NormalizedIssue],
    top_priorities: list[str] | None,
) -> list[dict[str, object]]:
    top_risky_files = build_top_risky_files(issues)
    if not top_priorities:
        return top_risky_files

    existing_paths = {str(item.get("path")) for item in top_risky_files}
    for priority in top_priorities:
        if priority in existing_paths:
            continue
        top_risky_files.append(
            {"path": priority, "issue_count": 0, "max_severity": "info"}
        )

    return top_risky_files[:5]


def _to_normalized_issue(issue: ReviewIssue) -> NormalizedIssue:
    raw_output: dict[str, Any] = dict(issue.raw_output or {})
    if issue.source == IssueSource.ROADMAP_RULE:
        raw_output["priority_override"] = "roadmap_rule_first"

    return NormalizedIssue(
        file_path=issue.file_path,
        line_start=issue.line_start,
        line_end=issue.line_end,
        severity=issue.severity,
        category=issue.category,
        title=issue.title,
        description=issue.description,
        suggestion=issue.suggestion,
        source=issue.source,
        confidence=issue.confidence,
        raw_output=raw_output,
    )


def _clamp_score(score: float) -> float:
    return max(0.0, min(10.0, round(float(score), 1)))


def _report_rejection_reason(
    *,
    executive_summary: str | None,
    maintainability_score: float | None,
    overall_score: float | None,
    performance_score: float | None,
    security_score: float | None,
) -> str | None:
    missing_fields: list[str] = []
    if executive_summary is None:
        missing_fields.append("executive_summary")
    if security_score is None:
        missing_fields.append("security_score")
    if maintainability_score is None:
        missing_fields.append("maintainability_score")
    if performance_score is None:
        missing_fields.append("performance_score")
    if overall_score is None:
        missing_fields.append("overall_score")

    if not missing_fields:
        return None

    return (
        "AI final report rejected: missing required fields: "
        f"{', '.join(missing_fields)}"
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value

    return None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None

    return [str(item) for item in value]


def _optional_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value

    return None
