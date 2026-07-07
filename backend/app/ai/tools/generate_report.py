"""AI tool for producing final review reports."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import delete, select

from app.ai.tool_runtime import get_ai_tool_runtime
from app.db.mongodb import (
    FILE_ANALYSIS_RESULTS_COLLECTION,
    ROADMAP_COMPLIANCE_RESULTS_COLLECTION,
)
from app.models.review_issue import IssueSeverity, IssueSource, ReviewIssue
from app.models.review_report import ReviewReport
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import build_top_risky_files


@tool
async def generate_final_report(
    job_id: str,
    executive_summary: str,
    security_score: float,
    maintainability_score: float,
    performance_score: float,
    overall_score: float,
    top_priorities: list[str] | None = None,
    tech_stack: dict[str, object] | None = None,
) -> dict[str, object]:
    """Tổng hợp toàn bộ issue đã tạo trong session thành report cuối: scores, executive summary, thứ tự ưu tiên sửa. Gọi 1 lần duy nhất, sau khi đã review xong các file ưu tiên."""

    runtime = get_ai_tool_runtime()
    job_uuid = UUID(job_id)
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
        ai_model_used="langchain-react-agent-v1",
    )
    await runtime.postgres_session.execute(
        delete(ReviewReport).where(ReviewReport.job_id == job_uuid)
    )
    runtime.postgres_session.add(report)
    await runtime.postgres_session.commit()
    await runtime.postgres_session.refresh(report)
    return {"status": "created", "report_id": str(report.id)}


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


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float | str):
        return float(value)

    return None
