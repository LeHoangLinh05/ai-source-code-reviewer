"""Structured final-report synthesis for completed AI reviews."""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import delete, select

from app.ai.reporting.draft import (
    FinalReportDraft,
    TechStackInput,
    build_final_report_draft,
    parse_final_report_draft_text,
)
from app.ai.tools.runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.db.mongodb import FILE_ANALYSIS_RESULTS_COLLECTION
from app.models.review_issue import IssueCategory, IssueSeverity, ReviewIssue
from app.models.review_report import ReviewReport
from app.schemas.normalized_issue import NormalizedIssue
from app.services.reporting.generation import (
    AI_REPORT_MODEL,
    build_top_risky_files,
    calculate_report_scores,
)

FINAL_REPORT_SYNTHESIS_TRACE_NAME = "final_report_synthesis"
TOP_RISKY_FILE_LIMIT = 5

__all__ = [
    "FinalReportDraft",
    "ReportTraceWriter",
    "build_final_report_draft",
    "build_final_report_input",
    "parse_final_report_draft_text",
    "persist_final_report",
    "synthesize_final_report",
]


class ReportTraceWriter(Protocol):
    """Protocol for writing report synthesis trace entries."""

    async def write_synthetic_tool_log(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, object],
        output: dict[str, object],
    ) -> None:
        """Persist one backend-produced trace entry."""


async def synthesize_final_report(
    *,
    job_id: UUID,
    review_handoff: str,
    report_context: str,
    reviewed_chunks: int,
    total_chunks: int,
    callback: ReportTraceWriter,
) -> dict[str, object]:
    """Generate, validate, and persist the final report."""

    report_input = build_final_report_input(
        job_id=job_id,
        review_handoff=review_handoff,
        report_context=report_context,
        reviewed_chunks=reviewed_chunks,
        total_chunks=total_chunks,
    )
    draft, source = await build_final_report_draft(
        report_input=report_input,
        report_context=report_context,
    )
    result = await persist_final_report(job_id=job_id, draft=draft)
    await callback.write_synthetic_tool_log(
        tool_name=FINAL_REPORT_SYNTHESIS_TRACE_NAME,
        tool_input={
            "job_id": str(job_id),
            "source": source,
            "reviewed_chunks": reviewed_chunks,
            "total_chunks": total_chunks,
        },
        output={
            **result,
            "source": source,
            "structured_contract": FinalReportDraft.__name__,
        },
    )
    return {
        "draft": draft.model_dump(mode="json"),
        "result": result,
        "source": source,
    }


def build_final_report_input(
    *,
    job_id: UUID,
    review_handoff: str,
    report_context: str,
    reviewed_chunks: int,
    total_chunks: int,
) -> str:
    """Build the final-report LLM prompt input from persisted review context."""

    return (
        f"Review job_id={job_id}. Backend-directed probe review completed with "
        f"source evidence coverage {reviewed_chunks}/{total_chunks}.\n\n"
        f"Review handoff:\n{review_handoff}\n\n"
        f"Persisted issue context:\n{report_context}"
    )


async def persist_final_report(
    *,
    job_id: UUID,
    draft: FinalReportDraft,
) -> dict[str, object]:
    """Persist a validated final report payload for the current AI runtime."""

    await ensure_ai_job_active()
    runtime = get_ai_tool_runtime()
    existing_report = await _load_existing_report(job_id)
    issues = await _load_issues(job_id)
    normalized_issues = [_to_normalized_issue(issue) for issue in issues]
    total_files_analyzed = await _total_files_analyzed(job_id, existing_report)
    deterministic_scores = calculate_report_scores(normalized_issues)
    executive_summary = draft.executive_summary
    if _is_placeholder_summary(executive_summary):
        executive_summary = _fallback_executive_summary(
            issues=normalized_issues,
            total_files_analyzed=total_files_analyzed,
        )

    severity_counts = Counter(issue.severity for issue in normalized_issues)
    report = ReviewReport(
        job_id=job_id,
        total_files_analyzed=total_files_analyzed,
        total_issues=len(issues),
        critical_count=severity_counts[IssueSeverity.CRITICAL],
        high_count=severity_counts[IssueSeverity.HIGH],
        medium_count=severity_counts[IssueSeverity.MEDIUM],
        low_count=severity_counts[IssueSeverity.LOW],
        info_count=severity_counts[IssueSeverity.INFO],
        security_score=_clamp_score(deterministic_scores["security_score"]),
        maintainability_score=_clamp_score(
            deterministic_scores["maintainability_score"],
        ),
        performance_score=_clamp_score(deterministic_scores["performance_score"]),
        overall_score=_clamp_score(deterministic_scores["overall_score"]),
        tech_stack=_normalize_tech_stack(draft.tech_stack),
        top_risky_files=_prioritized_files(normalized_issues, draft.top_priorities),
        executive_summary=executive_summary,
        ai_model_used=AI_REPORT_MODEL,
    )
    await runtime.postgres_session.execute(
        delete(ReviewReport).where(ReviewReport.job_id == job_id)
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
    top_priorities: list[str],
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

    return top_risky_files[:TOP_RISKY_FILE_LIMIT]


def _to_normalized_issue(issue: ReviewIssue) -> NormalizedIssue:
    raw_output: dict[str, Any] = dict(issue.raw_output or {})
    if raw_output.get("priority") == "P0":
        raw_output["priority_override"] = "kb_p0_first"

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


def _is_placeholder_summary(executive_summary: str | None) -> bool:
    if executive_summary is None or not executive_summary.strip():
        return True

    normalized_summary = executive_summary.lower()
    placeholder_markers = (
        "(as above)",
        "(as prepared above)",
        "(the json input above)",
        "as above",
        "as prepared above",
        "json input above",
        "successfully generated",
        "final report has been",
    )
    return any(marker in normalized_summary for marker in placeholder_markers)


def _fallback_executive_summary(
    *,
    issues: list[NormalizedIssue],
    total_files_analyzed: int,
) -> str:
    if not issues:
        return (
            f"AI semantic review completed across {total_files_analyzed} "
            "files and did not persist any confirmed issues."
        )

    severity_counts = Counter(issue.severity for issue in issues)
    category_counts = Counter(issue.category for issue in issues)
    return (
        f"AI semantic review completed across {total_files_analyzed} files "
        f"and persisted {len(issues)} confirmed issues: "
        f"{severity_counts[IssueSeverity.CRITICAL]} critical, "
        f"{severity_counts[IssueSeverity.HIGH]} high, "
        f"{severity_counts[IssueSeverity.MEDIUM]} medium, "
        f"{severity_counts[IssueSeverity.LOW]} low, and "
        f"{severity_counts[IssueSeverity.INFO]} informational. "
        f"Main categories: security={category_counts[IssueCategory.SECURITY]}, "
        f"bug={category_counts[IssueCategory.BUG]}, "
        f"performance={category_counts[IssueCategory.PERFORMANCE]}, "
        f"maintainability={category_counts[IssueCategory.MAINTAINABILITY]}, "
        f"style={category_counts[IssueCategory.STYLE]}."
    )


def _normalize_tech_stack(value: TechStackInput | None) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value

    return {"technologies": value}
