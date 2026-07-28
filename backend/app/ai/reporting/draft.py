"""Validate and generate JSON final-report drafts."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.ai.json_utils import parse_json_object_text
from app.ai.prompts import FINAL_REPORT_STRUCTURED_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

FALLBACK_TOP_PRIORITY_LIMIT = 10
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


async def build_final_report_draft(
    *,
    report_input: str,
    report_context: str,
) -> tuple[FinalReportDraft, str]:
    """Return a valid draft without unsupported structured tool calls."""

    try:
        return await _invoke_raw_json_final_report(report_input), "raw_json_output"
    except Exception as error:
        logger.warning("Final report JSON generation failed: %s", error)

    return build_deterministic_final_report_draft(report_context), (
        "deterministic_fallback"
    )


def parse_final_report_draft_text(text: str) -> FinalReportDraft:
    """Parse a final-report draft from raw model text."""

    payload = parse_json_object_text(text)
    if payload is None:
        raise ValueError("Final report JSON object was not found")

    return FinalReportDraft.model_validate(payload)


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


async def _invoke_raw_json_final_report(report_input: str) -> FinalReportDraft:
    from app.ai.llm.config import run_with_configured_llm

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
