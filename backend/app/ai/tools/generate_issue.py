"""AI tool for producing normalized review issues."""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app.ai.source_evidence import SOURCE_TOOL_NAMES, has_source_line_evidence
from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
from app.db.mongodb import TOOL_CALL_LOGS_COLLECTION
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
MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES = 0.75

AllowedIssueCategory = Literal[
    "security",
    "bug",
    "performance",
    "maintainability",
    "style",
    "requirement",
]
AllowedIssueSeverity = Literal["critical", "high", "medium", "low", "info"]
AllowedIssueSource = Literal["ai_review", "KB"]


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
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        data = unwrap_react_json_input(data, "input")
        data = unwrap_react_json_input(data, "severity")
        return _normalize_location_aliases(data)


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
    source: str | None = None,
) -> dict[str, object]:
    """Tạo 1 issue có cấu trúc. CHỈ gọi khi confidence >= 0.7. Đây là cách DUY NHẤT để báo issue — không bao giờ trả issue dưới dạng free text."""

    parsed_input = parse_json_object_text(severity)
    if parsed_input is not None:
        parsed_input = cast(
            dict[str, object],
            _normalize_location_aliases(parsed_input),
        )
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
        source = _optional_source(parsed_input.get("source")) or source

    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()

    references = references or []
    category = _optional_category(category)
    severity = _optional_severity(severity)
    file_path = _normalize_issue_file_path(file_path)
    issue_source = _optional_source(source) or IssueSource.AI_REVIEW.value
    knowledge_metadata: dict[str, object] = {}
    if issue_source == IssueSource.KB.value:
        knowledge_references, knowledge_metadata = await _latest_kb_grounding()
        if not knowledge_metadata:
            return _build_rejected_issue_response(
                "AI issue rejected: source=KB requires a successful "
                "search_knowledge_base result"
            )
        references = references or knowledge_references
        title = _sanitize_kb_visible_text(title)
        description = _sanitize_kb_visible_text(description)
        suggestion = _sanitize_kb_visible_text(suggestion)
    if category == IssueCategory.SECURITY.value and not references:
        references = await _latest_rag_references()
    confidence = _adjust_security_confidence(
        category=category,
        confidence=confidence,
        references=references,
    )

    rejection_reason = _issue_rejection_reason(
        category=category,
        confidence=confidence,
        description=description,
        severity=severity,
        title=title,
    )
    if rejection_reason is not None:
        return _build_rejected_issue_response(rejection_reason)

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
            source=issue_source,
            knowledge_metadata=knowledge_metadata,
        )
    except IssueValidationError as error:
        return _build_rejected_issue_response(str(error))

    if (
        file_path is not None
        and line_start is not None
        and line_end is not None
        and not await _has_source_evidence(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        )
    ):
        return _build_rejected_issue_response(
            "AI issue rejected: source range was not returned by "
            "read_file_chunk or search_code_semantic"
        )

    existing_issue = await _find_existing_issue(
        file_path=file_path,
        line_start=line_start,
        category=IssueCategory(category),
    )
    if existing_issue is not None:
        if existing_issue.source in {IssueSource.AI_REVIEW, IssueSource.KB}:
            return {
                "status": "skipped",
                "reason": "duplicate_ai_review_issue",
                "source": existing_issue.source.value,
            }
        if (
            SEVERITY_RANK[existing_issue.severity]
            > SEVERITY_RANK[IssueSeverity(severity)]
        ):
            severity = existing_issue.severity.value

    raw_output: dict[str, object] = {"references": references}
    raw_output.update(knowledge_metadata)
    assert file_path is not None
    assert line_start is not None
    assert line_end is not None
    raw_output["source_context"] = _build_source_context(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )
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
        source=IssueSource(issue_source),
        confidence=confidence,
        raw_output=raw_output,
    )
    runtime.postgres_session.add(review_issue)
    await runtime.postgres_session.commit()
    await runtime.postgres_session.refresh(review_issue)
    return {
        "status": "created",
        "issue_id": str(review_issue.id),
        "source": issue_source,
    }


def _optional_category(value: object) -> AllowedIssueCategory | None:
    allowed_categories = {
        "security",
        "bug",
        "performance",
        "maintainability",
        "style",
        "requirement",
    }
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in allowed_categories:
            return cast(AllowedIssueCategory, normalized_value)

    return None


def _normalize_location_aliases(data: object) -> object:
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    if "file_path" not in normalized and isinstance(normalized.get("path"), str):
        normalized["file_path"] = normalized["path"]

    line_range = normalized.get("line_range")
    if isinstance(line_range, dict):
        if "line_start" not in normalized:
            normalized["line_start"] = line_range.get("start")
        if "line_end" not in normalized:
            normalized["line_end"] = line_range.get("end")
    elif isinstance(line_range, str):
        line_match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", line_range)
        if line_match is not None:
            normalized.setdefault("line_start", int(line_match.group(1)))
            normalized.setdefault("line_end", int(line_match.group(2)))

    return normalized


def _sanitize_kb_visible_text(value: str | None) -> str | None:
    if value is None:
        return None

    sanitized = re.sub(
        r"\s*\([^)]*\brule\s*id\s*:[^)]*\)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(r"\bRC-[A-Z0-9-]+\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\broadmap\b", "requirements", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    return sanitized.strip()


def _optional_source(value: object) -> AllowedIssueSource | None:
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    if normalized_value.lower() == "ai_review":
        return "ai_review"
    if normalized_value.upper() == "KB":
        return "KB"
    return None


def _normalize_issue_file_path(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or None


def _optional_severity(value: object) -> AllowedIssueSeverity | None:
    allowed_severities = {"critical", "high", "medium", "low", "info"}
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        priority_severity = {
            "p0": "critical",
            "p1": "high",
            "p2": "low",
        }.get(normalized_value)
        if priority_severity is not None:
            return cast(AllowedIssueSeverity, priority_severity)
        if normalized_value in allowed_severities:
            return cast(AllowedIssueSeverity, normalized_value)

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


def _build_rejected_issue_response(reason: str) -> dict[str, object]:
    response: dict[str, object] = {"status": "rejected", "reason": reason}
    if "missing or invalid fields" in reason:
        response["next_action"] = (
            "Retry generate_issue with severity, category, title, description, "
            "confidence, and source line fields when file_path is present."
        )
    elif (
        "File does not exist in sandbox" in reason
        or "file_path, line_start, and line_end are required" in reason
    ):
        response["next_action"] = (
            "Retry generate_issue only with an exact file_path and line range from "
            "a successful source tool output. Do not invent or shorten paths."
        )

    return response


async def _has_source_evidence(
    *,
    file_path: str,
    line_start: int,
    line_end: int,
) -> bool:
    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(runtime.job_id),
                "session_id": str(runtime.session_id),
                "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)},
            }
        )
        .to_list(length=None)
    )
    return has_source_line_evidence(
        documents,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


def _adjust_security_confidence(
    *,
    category: AllowedIssueCategory | None,
    confidence: float | None,
    references: list[str],
) -> float | None:
    if category != IssueCategory.SECURITY.value or confidence is None or references:
        return confidence

    return min(confidence, MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES)


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


async def _latest_rag_references() -> list[str]:
    runtime = get_ai_tool_runtime()
    document = await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION].find_one(
        {
            "job_id": str(runtime.job_id),
            "session_id": str(runtime.session_id),
            "tool_name": {"$in": ["search_knowledge_base", "search_coding_standard"]},
            "output.status": "ok",
        },
        sort=[("sequence", -1)],
    )
    if document is None:
        return []

    output = document.get("output")
    if not isinstance(output, dict):
        return []

    references: list[str] = []
    for result in output.get("results", []):
        if not isinstance(result, dict):
            continue
        reference = _rag_result_reference(result)
        if reference is not None:
            references.append(reference)

    return references


async def _latest_kb_grounding() -> tuple[list[str], dict[str, object]]:
    runtime = get_ai_tool_runtime()
    document = await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION].find_one(
        {
            "job_id": str(runtime.job_id),
            "session_id": str(runtime.session_id),
            "tool_name": "search_knowledge_base",
            "output.status": "ok",
        },
        sort=[("sequence", -1)],
    )
    if document is None:
        structure_document = await runtime.mongodb_database[
            TOOL_CALL_LOGS_COLLECTION
        ].find_one(
            {
                "job_id": str(runtime.job_id),
                "session_id": str(runtime.session_id),
                "tool_name": "analyze_project_structure",
                "output.roadmap": {"$exists": True},
            },
            sort=[("sequence", -1)],
        )
        if structure_document is None:
            return [], {}
        return ["knowledge_base checklist"], {"knowledge_doc_type": "roadmap_rule"}

    output = document.get("output")
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return [], {}

    references: list[str] = []
    grounding: dict[str, object] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        reference = _rag_result_reference(result)
        if reference is not None:
            references.append(reference)
        metadata = result.get("metadata")
        if grounding or not isinstance(metadata, dict):
            continue
        grounding["knowledge_doc_type"] = str(metadata.get("doc_type", "unknown"))
        for field in ("rule_id", "week", "priority"):
            value = metadata.get(field)
            if value is not None:
                grounding[field] = value

    return references, grounding


def _rag_result_reference(result: dict[str, Any]) -> str | None:
    source = result.get("source")
    if isinstance(source, str) and source:
        return source

    content = result.get("content")
    if isinstance(content, str) and content:
        return content[:160]

    return None


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
    source: str = "ai_review",
    knowledge_metadata: dict[str, object] | None = None,
) -> None:
    """Validate an AI issue before any database write happens."""

    _ = severity
    if confidence < 0.7:
        raise IssueValidationError("AI issue rejected: confidence must be >= 0.7")

    if category == IssueCategory.SECURITY.value and not references:
        raise IssueValidationError(
            "AI issue rejected: security issues require a knowledge-base reference"
        )

    if file_path is None or line_start is None or line_end is None:
        raise IssueValidationError(
            "AI issue rejected: file_path, line_start, and line_end are required"
        )

    metadata = knowledge_metadata or {}
    if source == IssueSource.KB.value and metadata.get("knowledge_doc_type") == (
        "roadmap_rule"
    ):
        hidden_terms = ["roadmap"]
        rule_id = metadata.get("rule_id")
        if isinstance(rule_id, str):
            hidden_terms.append(rule_id.lower())
        visible_text = " ".join(
            value for value in (title, description, suggestion or "") if value
        ).lower()
        if any(term in visible_text for term in hidden_terms):
            raise IssueValidationError(
                "AI issue rejected: KB-derived issue text must describe the code "
                "problem without internal roadmap identifiers"
            )

    if line_start < 1 or line_end < line_start:
        raise IssueValidationError("AI issue rejected: invalid source line range")

    try:
        source_path = resolve_sandbox_file(file_path)
    except ValueError as error:
        raise IssueValidationError(f"AI issue rejected: {error}") from error

    line_count = len(
        source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    )
    if line_start > line_count or line_end > line_count:
        raise IssueValidationError(
            f"AI issue rejected: line range {line_start}-{line_end} "
            f"does not exist in {file_path} ({line_count} lines)"
        )


def _build_source_context(
    *,
    file_path: str,
    line_start: int,
    line_end: int,
    context_radius: int = 3,
) -> dict[str, object]:
    source_path = resolve_sandbox_file(file_path)
    lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    first_line = max(1, line_start - context_radius)
    last_line = min(len(lines), line_end + context_radius)
    return {
        "start_line": first_line,
        "lines": lines[first_line - 1 : last_line],
    }
