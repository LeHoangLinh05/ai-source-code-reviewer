"""Validate and generate JSON final-report drafts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ai.json_utils import parse_json_object_text
from app.ai.prompts import FINAL_REPORT_STRUCTURED_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

FALLBACK_TOP_PRIORITY_LIMIT = 10
TechStackInput = dict[str, object] | list[str]
QUANTITATIVE_OVERVIEW_PATTERN = re.compile(
    r"(?:\b\d+(?:\.\d+)?%?\b|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|total|count)\s+"
    r"(?:critical|high|medium|low|informational|issues?|findings?|files?)\b)",
    re.IGNORECASE,
)
PLACEHOLDER_OVERVIEW_MARKERS = (
    "(as above)",
    "(as prepared above)",
    "(the json input above)",
    "as above",
    "as prepared above",
    "json input above",
    "successfully generated",
    "final report has been",
)


class FinalReportDraft(BaseModel):
    """Structured LLM contract for the final report synthesis step."""

    analysis_overview: str | None = Field(default=None, max_length=2000)
    top_priorities: list[str] = Field(default_factory=list)
    tech_stack: TechStackInput | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_top_priorities(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if "analysis_overview" not in normalized and "executive_summary" in normalized:
            normalized["analysis_overview"] = normalized.get("executive_summary")
        if "top_priorities" in normalized:
            top_priorities = _optional_str_list(normalized.get("top_priorities"))
            if top_priorities is not None:
                normalized["top_priorities"] = top_priorities

        return normalized

    @field_validator("analysis_overview", mode="before")
    @classmethod
    def validate_qualitative_overview(cls, value: object) -> str | None:
        """Discard placeholders and prose that claims authoritative counts."""

        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        lowered = normalized.casefold()
        if any(marker in lowered for marker in PLACEHOLDER_OVERVIEW_MARKERS):
            return None
        if QUANTITATIVE_OVERVIEW_PATTERN.search(normalized):
            return None
        return normalized


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
                    "analysis_overview and top_priorities. analysis_overview must be "
                    "qualitative: do not state counts, percentages, scores, or totals. "
                    "Do not infer the technology stack.",
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
    top_priorities = _context_top_priorities(context.get("top_issues"))
    return FinalReportDraft(
        analysis_overview=None,
        top_priorities=top_priorities,
    )


def _parse_report_context(report_context: str) -> dict[str, object]:
    try:
        parsed = json.loads(report_context)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


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
