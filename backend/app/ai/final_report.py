"""Structured final-report synthesis for completed AI reviews."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select

from app.ai.json_utils import parse_json_object_text
from app.ai.prompts import FINAL_REPORT_STRUCTURED_SYSTEM_PROMPT
from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.db.mongodb import FILE_ANALYSIS_RESULTS_COLLECTION
from app.models.review_issue import IssueCategory, IssueSeverity, ReviewIssue
from app.models.review_report import ReviewReport
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import (
    AI_REPORT_MODEL,
    build_top_risky_files,
    calculate_report_scores,
)

logger = logging.getLogger(__name__)

FALLBACK_TOP_PRIORITY_LIMIT = 10
FINAL_REPORT_SYNTHESIS_TRACE_NAME = "final_report_synthesis"
TOP_RISKY_FILE_LIMIT = 5

TechStackInput = dict[str, object] | list[str]


class FinalReportDraft(BaseModel):
    """Structured LLM contract for the final report synthesis step."""

    executive_summary: str = Field(min_length=1)
    security_score: float = Field(ge=0.0, le=10.0)
    maintainability_score: float = Field(ge=0.0, le=10.0)
    performance_score: float = Field(ge=0.0, le=10.0)
    overall_score: float = Field(ge=0.0, le=10.0)
    top_priorities: list[str] = Field(default_factory=list)
    tech_stack: TechStackInput | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_top_priorities(cls, data: object) -> object:
        if isinstance(data, dict) and "top_priorities" in data:
            top_priorities = _optional_str_list(data.get("top_priorities"))
            if top_priorities is not None:
                return {**data, "top_priorities": top_priorities}

        return data


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


async def build_final_report_draft(
    *,
    report_input: str,
    report_context: str,
) -> tuple[FinalReportDraft, str]:
    """Return a valid final-report draft, falling back deterministically if needed."""

    try:
        return (
            await _invoke_structured_final_report(report_input),
            "langchain_structured_output",
        )
    except Exception as error:
        logger.warning("Structured final report generation failed: %s", error)

    try:
        return await _invoke_raw_json_final_report(report_input), "raw_json_output"
    except Exception as error:
        logger.warning("Raw JSON final report generation failed: %s", error)

    return build_deterministic_final_report_draft(report_context), (
        "deterministic_fallback"
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


def parse_final_report_draft_text(text: str) -> FinalReportDraft:
    """Parse a final-report draft from raw model text."""

    payload = parse_json_object_text(text)
    if payload is None:
        raise ValueError("Final report JSON object was not found")

    return FinalReportDraft.model_validate(payload)


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


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value

    return None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None

    normalized_items: list[str] = []
    for item in value:
        normalized_item = _stringify_top_priority(item)
        if normalized_item is not None:
            normalized_items.append(normalized_item)

    return normalized_items


def _stringify_top_priority(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, dict):
        path = _optional_str(value.get("file_path")) or _optional_str(value.get("path"))
        if path is not None:
            return path

        parts = [
            _optional_str(value.get("severity")),
            _optional_str(value.get("category")),
            _optional_str(value.get("title")),
            _optional_str(value.get("source")),
        ]
        return " | ".join(part for part in parts if part) or None

    return str(value).strip() or None


def _normalize_tech_stack(value: TechStackInput | None) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value

    return {"technologies": value}


async def _invoke_structured_final_report(report_input: str) -> FinalReportDraft:
    from app.ai.llm_config import run_with_configured_llm

    async def call(llm: Any) -> FinalReportDraft:
        structured_llm = llm.with_structured_output(FinalReportDraft)
        result = await structured_llm.ainvoke(
            [
                ("system", FINAL_REPORT_STRUCTURED_SYSTEM_PROMPT),
                ("human", report_input),
            ]
        )
        if isinstance(result, FinalReportDraft):
            return result

        return FinalReportDraft.model_validate(result)

    return await run_with_configured_llm(call)


async def _invoke_raw_json_final_report(report_input: str) -> FinalReportDraft:
    from app.ai.llm_config import run_with_configured_llm

    async def call(llm: Any) -> FinalReportDraft:
        result = await llm.ainvoke(
            [
                ("system", FINAL_REPORT_STRUCTURED_SYSTEM_PROMPT),
                (
                    "human",
                    report_input + "\n\nReturn only a JSON object with these keys: "
                    "executive_summary, security_score, maintainability_score, "
                    "performance_score, overall_score, top_priorities, tech_stack.",
                ),
            ]
        )
        return parse_final_report_draft_text(_message_content(result))

    return await run_with_configured_llm(call)


def _message_content(result: object) -> str:
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_message_content_item_text(item) for item in content)

    return str(result)


def _message_content_item_text(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        if isinstance(text, str):
            return text
        content = item.get("content")
        if isinstance(content, str):
            return content

    return str(item)


def build_deterministic_final_report_draft(report_context: str) -> FinalReportDraft:
    """Build a valid final-report draft from persisted issue context."""

    context = _parse_report_context(report_context)
    total_issues = _context_int(context.get("total_issues"))
    severity_counts = _context_mapping(context.get("severity_counts"))
    category_counts = _context_mapping(context.get("category_counts"))
    top_priorities = _context_top_priorities(context.get("top_issues"))
    executive_summary = (
        "AI semantic review completed and persisted "
        f"{total_issues} confirmed issues. Severity mix: "
        f"{severity_counts.get('critical', 0)} critical, "
        f"{severity_counts.get('high', 0)} high, "
        f"{severity_counts.get('medium', 0)} medium, "
        f"{severity_counts.get('low', 0)} low, and "
        f"{severity_counts.get('info', 0)} informational. Main categories: "
        f"security={category_counts.get('security', 0)}, "
        f"bug={category_counts.get('bug', 0)}, "
        f"performance={category_counts.get('performance', 0)}, "
        f"maintainability={category_counts.get('maintainability', 0)}, "
        f"style={category_counts.get('style', 0)}."
    )
    return FinalReportDraft(
        executive_summary=executive_summary,
        security_score=0.0,
        maintainability_score=0.0,
        performance_score=0.0,
        overall_score=0.0,
        top_priorities=top_priorities,
        tech_stack={},
    )


def _parse_report_context(report_context: str) -> dict[str, object]:
    try:
        parsed = json.loads(report_context)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _context_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _context_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}

    return {str(key): item for key, item in value.items() if isinstance(item, int)}


def _context_top_priorities(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    priorities: list[str] = []
    for item in value[:FALLBACK_TOP_PRIORITY_LIMIT]:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        file_path = item.get("file_path")
        line_start = item.get("line_start")
        if not isinstance(title, str) or not isinstance(file_path, str):
            continue

        if isinstance(line_start, int):
            priorities.append(f"{title} ({file_path}:{line_start})")
        else:
            priorities.append(f"{title} ({file_path})")

    return priorities
