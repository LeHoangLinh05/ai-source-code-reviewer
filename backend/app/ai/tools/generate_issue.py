"""AI tool for producing normalized review issues."""

from __future__ import annotations

from typing import Literal, cast

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
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


class GenerateIssueInput(BaseModel):
    """Input schema for normalized AI issues."""

    severity: str | None = None
    category: str | None = None
    title: str | None = None
    description: str | None = None
    confidence: float | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    references: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        return unwrap_react_json_input(data, "severity")


@tool(args_schema=GenerateIssueInput)
async def generate_issue(
    severity: str | None = None,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    confidence: float | None = None,
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    suggestion: str | None = None,
    references: list[str] | None = None,
) -> dict[str, object]:
    """Tạo 1 issue có cấu trúc. CHỈ gọi khi confidence >= 0.7. Đây là cách DUY NHẤT để báo issue — không bao giờ trả issue dưới dạng free text."""

    parsed_input = parse_json_object_text(severity)
    if parsed_input is not None:
        severity = _optional_str(parsed_input.get("severity")) or severity
        category = _optional_category(parsed_input.get("category")) or category
        title = _optional_str(parsed_input.get("title")) or title
        description = _optional_str(parsed_input.get("description")) or description
        confidence = _optional_float(parsed_input.get("confidence")) or confidence
        file_path = _optional_str(parsed_input.get("file_path")) or file_path
        line_start = _optional_int(parsed_input.get("line_start")) or line_start
        line_end = _optional_int(parsed_input.get("line_end")) or line_end
        suggestion = _optional_str(parsed_input.get("suggestion")) or suggestion
        references = _optional_str_list(parsed_input.get("references")) or references

    references = references or []
    category = _optional_category(category)
    severity = _optional_severity(severity)
    rejection_reason = _issue_rejection_reason(
        category=category,
        confidence=confidence,
        description=description,
        severity=severity,
        title=title,
    )
    if rejection_reason is not None:
        return {"status": "rejected", "reason": rejection_reason}

    assert category is not None
    assert confidence is not None
    assert description is not None
    assert severity is not None
    assert title is not None

    try:
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
    except IssueValidationError as error:
        return {"status": "rejected", "reason": str(error)}

    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()
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


def _optional_category(value: object) -> AllowedIssueCategory | None:
    allowed_categories = {
        "security",
        "bug",
        "performance",
        "maintainability",
        "style",
    }
    if isinstance(value, str) and value in allowed_categories:
        return cast(AllowedIssueCategory, value)

    return None


def _optional_severity(value: object) -> AllowedIssueSeverity | None:
    allowed_severities = {"critical", "high", "medium", "low", "info"}
    if isinstance(value, str) and value in allowed_severities:
        return cast(AllowedIssueSeverity, value)

    return None


def _issue_rejection_reason(
    *,
    category: AllowedIssueCategory | None,
    confidence: float | None,
    description: str | None,
    severity: AllowedIssueSeverity | None,
    title: str | None,
) -> str | None:
    missing_fields: list[str] = []
    if severity is None:
        missing_fields.append("severity")
    if category is None:
        missing_fields.append("category")
    if title is None:
        missing_fields.append("title")
    if description is None:
        missing_fields.append("description")
    if confidence is None:
        missing_fields.append("confidence")

    if not missing_fields:
        return None

    return f"AI issue rejected: missing or invalid fields: {', '.join(missing_fields)}"


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value

    return None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None

    return [str(item) for item in value]


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
