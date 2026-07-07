"""AI tool for producing normalized review issues."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from sqlalchemy import select

from app.ai.tool_runtime import get_ai_tool_runtime
from app.ai.tools.common import resolve_sandbox_file
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)

SEVERITY_RANK = {
    IssueSeverity.CRITICAL: 5,
    IssueSeverity.HIGH: 4,
    IssueSeverity.MEDIUM: 3,
    IssueSeverity.LOW: 2,
    IssueSeverity.INFO: 1,
}

AllowedIssueCategory = Literal[
    "security",
    "bug",
    "performance",
    "maintainability",
    "style",
]
AllowedIssueSeverity = Literal["critical", "high", "medium", "low", "info"]


class IssueValidationError(ValueError):
    """Raised when an AI issue violates backend safety gates."""


@tool
async def generate_issue(
    severity: AllowedIssueSeverity,
    category: AllowedIssueCategory,
    title: str,
    description: str,
    confidence: float,
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    suggestion: str | None = None,
    references: list[str] | None = None,
) -> dict[str, object]:
    """Tạo 1 issue có cấu trúc. CHỈ gọi khi confidence >= 0.7. Đây là cách DUY NHẤT để báo issue — không bao giờ trả issue dưới dạng free text."""

    references = references or []
    validate_issue_payload(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        severity=severity,
        category=category,
        title=title,
        description=description,
        suggestion=suggestion,
        confidence=confidence,
        references=references,
    )
    runtime = get_ai_tool_runtime()
    existing_issue = await _find_existing_issue(
        file_path=file_path,
        line_start=line_start,
        category=IssueCategory(category),
    )
    if existing_issue is not None:
        if existing_issue.source == IssueSource.ROADMAP_RULE:
            return {
                "status": "skipped",
                "reason": "duplicate_roadmap_rule_issue",
                "source": IssueSource.ROADMAP_RULE.value,
            }
        if existing_issue.source == IssueSource.AI_REVIEW:
            return {
                "status": "skipped",
                "reason": "duplicate_ai_review_issue",
                "source": IssueSource.AI_REVIEW.value,
            }
        if (
            SEVERITY_RANK[existing_issue.severity]
            > SEVERITY_RANK[IssueSeverity(severity)]
        ):
            severity = existing_issue.severity.value

    review_issue = ReviewIssue(
        job_id=runtime.job_id,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        severity=IssueSeverity(severity),
        category=IssueCategory(category),
        title=title[:255],
        description=description,
        suggestion=suggestion,
        source=IssueSource.AI_REVIEW,
        confidence=confidence,
        raw_output={"references": references},
    )
    runtime.postgres_session.add(review_issue)
    await runtime.postgres_session.commit()
    await runtime.postgres_session.refresh(review_issue)
    return {
        "status": "created",
        "issue_id": str(review_issue.id),
        "source": IssueSource.AI_REVIEW.value,
    }


async def _find_existing_issue(
    *,
    file_path: str | None,
    line_start: int | None,
    category: IssueCategory,
) -> ReviewIssue | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewIssue)
        .where(
            ReviewIssue.job_id == runtime.job_id,
            ReviewIssue.file_path == file_path,
            ReviewIssue.line_start == line_start,
            ReviewIssue.category == category,
        )
        .order_by(ReviewIssue.created_at.asc())
    )
    return result.scalars().first()


def validate_issue_payload(
    *,
    file_path: str | None,
    line_start: int | None,
    line_end: int | None,
    severity: str,
    category: str,
    title: str,
    description: str,
    suggestion: str | None,
    confidence: float,
    references: list[str],
) -> None:
    """Validate an AI issue before any database write happens."""

    _ = (severity, title, description, suggestion)
    if confidence < 0.7:
        raise IssueValidationError("AI issue rejected: confidence must be >= 0.7")

    if category == IssueCategory.REQUIREMENT.value:
        raise IssueValidationError(
            'AI issue rejected: category="requirement" is reserved for roadmap rules'
        )

    if category == IssueCategory.SECURITY.value and not references:
        raise IssueValidationError(
            "AI issue rejected: security issues require RAG references"
        )

    if file_path is None:
        return

    if line_start is None or line_end is None:
        raise IssueValidationError(
            "AI issue rejected: file_path requires line_start and line_end"
        )

    if line_start < 1 or line_end < line_start:
        raise IssueValidationError("AI issue rejected: invalid source line range")

    source_path = resolve_sandbox_file(file_path)
    line_count = len(
        source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    )
    if line_start > line_count or line_end > line_count:
        raise IssueValidationError(
            f"AI issue rejected: line range {line_start}-{line_end} "
            f"does not exist in {file_path} ({line_count} lines)"
        )
