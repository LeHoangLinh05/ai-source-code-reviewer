"""AI tool for reading project structure and roadmap context."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app.ai.review_plan import (
    build_chunk_review_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)
from app.ai.roadmap.selection import build_roadmap_context
from app.ai.semantic_audit_plan import build_semantic_audit_plan
from app.ai.tool_runtime import get_ai_tool_runtime
from app.ai.tool_runtime import ensure_ai_job_active
from app.ai.tools.common import parse_job_uuid_or_current, serialize_mongo_document
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
)
from app.models.review_job import ReviewJob

HIGH_RISK_AREAS = {"security", "bug"}
HIGH_RISK_PATH_PARTS = {"auth", "security", "crypto", "middleware"}


class AnalyzeProjectStructureInput(BaseModel):
    """Input schema for project structure and roadmap context."""

    job_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_react_empty_input(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        raw_input = data.get("input")
        if isinstance(raw_input, str) and raw_input.strip().lower() in {
            "",
            "{}",
            "none",
            "null",
        }:
            return {}

        return data


@tool(args_schema=AnalyzeProjectStructureInput)
async def analyze_project_structure(job_id: str | None = None) -> dict[str, object]:
    """Lấy tổng quan project đang review: ngôn ngữ, framework, danh sách file cần review theo độ ưu tiên, tóm tắt static analysis, và tóm tắt roadmap compliance (nếu job bật rule_profile)."""

    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()
    job_uuid = parse_job_uuid_or_current(job_id)
    database = runtime.mongodb_database
    structure_document = await database[FILE_ANALYSIS_RESULTS_COLLECTION].find_one(
        {"job_id": str(job_uuid)},
        sort=[("analyzed_at", -1)],
    )
    if structure_document is None:
        raise ValueError(f"Structure analysis result not found for job: {job_uuid}")

    static_documents = (
        await database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION]
        .find({"job_id": str(job_uuid)})
        .to_list(length=None)
    )
    chunk_documents = (
        await database[CHUNK_METADATA_COLLECTION]
        .find({"job_id": str(job_uuid)})
        .to_list(length=None)
    )
    job_options = await _load_job_options(job_uuid)
    review_mode = get_review_mode(job_options)

    structure = serialize_mongo_document(structure_document)
    project_structure = _as_dict(structure.get("project_structure"))
    file_tree = _as_list(structure.get("file_tree"))
    static_issues = _static_issues(static_documents)
    chunk_counts = _chunk_counts_by_file(chunk_documents)
    files_to_review = _files_to_review(
        file_tree=file_tree,
        static_issues=static_issues,
        chunk_counts=chunk_counts,
    )
    roadmap_context = build_roadmap_context(job_options)
    semantic_audit_plan = build_semantic_audit_plan(
        roadmap_context=roadmap_context,
        files_to_review=files_to_review,
        static_issues=static_issues,
    )

    output: dict[str, object] = {
        "languages": _languages(project_structure),
        "frameworks": _frameworks(project_structure),
        "files_to_review": files_to_review,
        "chunk_review_plan": build_chunk_review_plan(
            chunk_documents=chunk_documents,
            review_mode=review_mode,
            max_smart_chunks=get_smart_review_max_chunks(job_options),
        ),
        "semantic_audit_plan": semantic_audit_plan,
        "static_analysis_summary": _static_analysis_summary(static_documents),
    }

    if roadmap_context is not None:
        output["roadmap"] = _compact_roadmap_context(roadmap_context)

    return output


async def _load_job_options(job_id: UUID) -> dict[str, object] | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    return options if isinstance(options, dict) else None


def _languages(project_structure: dict[str, object]) -> list[str]:
    languages = project_structure.get("languages")
    if isinstance(languages, dict):
        return [str(language) for language in languages]
    if isinstance(languages, list):
        return [str(language) for language in languages]

    primary_language = project_structure.get("primary_language")
    return [str(primary_language)] if primary_language else []


def _frameworks(project_structure: dict[str, object]) -> list[str]:
    frameworks = project_structure.get("frameworks", [])
    if not isinstance(frameworks, list):
        return []

    return [str(framework) for framework in frameworks]


def _static_issues(static_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for document in static_documents:
        tool_name = str(document.get("tool", "static"))
        for issue in _as_list(document.get("parsed_issues")):
            if not isinstance(issue, dict):
                continue
            normalized_issue = dict(issue)
            normalized_issue["source"] = tool_name
            issues.append(normalized_issue)

    return issues


def _static_analysis_summary(static_documents: list[dict[str, Any]]) -> str:
    if not static_documents:
        return "No static analysis results found."

    severity_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    issue_count = 0
    for document in static_documents:
        tool_name = str(document.get("tool", "static"))
        parsed_issues = _as_list(document.get("parsed_issues"))
        issue_count += len(parsed_issues)
        tool_counts[tool_name] += len(parsed_issues)
        for issue in parsed_issues:
            if isinstance(issue, dict):
                severity_counts[str(issue.get("severity", "unknown"))] += 1

    tool_summary = ", ".join(
        f"{tool_name}: {count}" for tool_name, count in sorted(tool_counts.items())
    )
    severity_summary = ", ".join(
        f"{severity}: {count}" for severity, count in sorted(severity_counts.items())
    )
    if severity_summary:
        return (
            f"{issue_count} issues found ({severity_summary}); by tool: {tool_summary}."
        )

    return f"{issue_count} issues found; by tool: {tool_summary}."


def _compact_roadmap_context(
    roadmap_context: dict[str, object],
) -> dict[str, object]:
    """Return roadmap metadata without duplicating full rule text in the prompt."""

    review_rules = _as_list(roadmap_context.get("review_rules"))
    ai_rules = _as_list(roadmap_context.get("ai_verification_rules"))
    review_category_counts: Counter[str] = Counter()
    for raw_rule in review_rules:
        if not isinstance(raw_rule, dict):
            continue
        category = raw_rule.get("review_category") or raw_rule.get("category")
        review_category_counts[str(category or "requirement")] += 1

    return {
        "profile_id": roadmap_context.get("profile_id"),
        "weeks_included": roadmap_context.get("weeks_included"),
        "applicable_rule_ids": roadmap_context.get("applicable_rule_ids", []),
        "review_rule_count": len(review_rules),
        "ai_verification_rule_count": len(ai_rules),
        "review_category_counts": dict(sorted(review_category_counts.items())),
        "details_location": (
            "Use semantic_audit_plan category_probe items for roadmap rule "
            "requirements, verification hints, and focused probe grouping."
        ),
    }


def _files_to_review(
    *,
    file_tree: list[object],
    static_issues: list[dict[str, Any]],
    chunk_counts: dict[str, int],
) -> list[dict[str, object]]:
    issue_by_file: dict[str, list[dict[str, Any]]] = {}
    for issue in static_issues:
        file_path = issue.get("file_path")
        if not file_path:
            continue
        issue_by_file.setdefault(str(file_path), []).append(issue)

    file_paths = {
        str(entry["path"])
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    }
    file_paths.update(issue_by_file)

    files = [
        {
            "file_path": file_path,
            "priority": _file_priority(file_path, issue_by_file.get(file_path, [])),
            "risk_area": _file_risk_area(file_path, issue_by_file.get(file_path, [])),
            "total_chunks": chunk_counts.get(file_path, 0),
        }
        for file_path in sorted(file_paths)
    ]
    return sorted(
        files,
        key=lambda item: (
            _priority_rank(str(item["priority"])),
            str(item["file_path"]),
        ),
    )


def _chunk_counts_by_file(chunk_documents: list[object]) -> dict[str, int]:
    chunk_counts: dict[str, int] = {}
    for document in chunk_documents:
        if not isinstance(document, dict):
            continue

        file_path = document.get("file_path")
        chunk_index = document.get("chunk_index")
        total_chunks = document.get("total_chunks")
        if not isinstance(file_path, str):
            continue

        known_count = chunk_counts.get(file_path, 0)
        if isinstance(total_chunks, int) and total_chunks > known_count:
            chunk_counts[file_path] = total_chunks
            continue

        if isinstance(chunk_index, int) and chunk_index + 1 > known_count:
            chunk_counts[file_path] = chunk_index + 1

    return chunk_counts


def _file_priority(file_path: str, issues: list[dict[str, Any]]) -> str:
    severities = {str(issue.get("severity")) for issue in issues}
    categories = {str(issue.get("category")) for issue in issues}
    if severities & {"critical", "high"} or categories & HIGH_RISK_AREAS:
        return "high"

    path_parts = {part.lower() for part in file_path.replace("\\", "/").split("/")}
    if path_parts & HIGH_RISK_PATH_PARTS:
        return "high"

    if issues:
        return "medium"

    return "low"


def _file_risk_area(file_path: str, issues: list[dict[str, Any]]) -> str:
    categories = {str(issue.get("category")) for issue in issues}
    if "security" in categories:
        return "security"
    if "performance" in categories:
        return "performance"
    if "bug" in categories:
        return "bug"

    path_parts = {part.lower() for part in file_path.replace("\\", "/").split("/")}
    if path_parts & HIGH_RISK_PATH_PARTS:
        return "security"

    return "maintainability" if issues else "general"


def _priority_rank(priority: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 3)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
