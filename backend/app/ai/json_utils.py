"""JSON parsing helpers for LLM text outputs."""

from __future__ import annotations

import json
import re
from typing import Any

MIN_MARKDOWN_FENCE_LINES = 2


def parse_json_object_text(value: object) -> dict[str, Any] | None:
    """Return the first JSON object embedded in text."""

    if not isinstance(value, str):
        return None

    raw_text = value.strip()
    candidates = [_strip_json_markdown_fence(raw_text)]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw_text, re.DOTALL)
    )
    object_start = raw_text.find("{")
    if object_start >= 0:
        candidates.append(raw_text[object_start:])

    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _strip_json_markdown_fence(raw_text: str) -> str:
    if not raw_text.startswith("```"):
        return raw_text

    lines = raw_text.splitlines()
    if len(lines) < MIN_MARKDOWN_FENCE_LINES:
        return raw_text

    opening_fence = lines[0].strip().lower()
    if opening_fence not in {"```", "```json"}:
        return raw_text

    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return "\n".join(lines[1:]).strip()
