"""Compact and aggregate persisted AI trace events."""

from __future__ import annotations

from typing import Any

from app.schemas.ai_trace import AITokenTotals, AIToolCallTrace

MAX_TEXT_PREVIEW = 700
MAX_LIST_PREVIEW_ITEMS = 6
MAX_DICT_PREVIEW_ITEMS = 10


def to_tool_call_trace(document: dict[str, Any]) -> AIToolCallTrace:
    """Convert a raw MongoDB event into its bounded API representation."""

    output = _preview_value(document.get("output", {}))
    output_dict = output if isinstance(output, dict) else {"output": output}
    tool_name = str(document.get("tool_name", "unknown"))
    tool_input = _preview_dict(document.get("input", {}))
    return AIToolCallTrace(
        sequence=int(document.get("sequence", 0)),
        tool_name=tool_name,
        called_at=document["called_at"],
        duration_ms=int(document.get("duration_ms", 0)),
        input=tool_input,
        output=output_dict,
        status=str(document.get("status") or _tool_call_status(tool_name, output_dict)),
        event_type=str(document.get("event_type") or "tool"),
        provider=_optional_str(document.get("provider")),
        model=_optional_str(document.get("model")),
        phase=_optional_str(document.get("phase")),
        token_usage=_token_usage_dict(document.get("token_usage")),
        metadata=_preview_dict(document.get("metadata", {})),
    )


def _token_totals(events: list[AIToolCallTrace]) -> AITokenTotals:
    totals = AITokenTotals()
    for event in events:
        usage = event.token_usage or {}
        totals.input_tokens += _safe_int(usage.get("input_tokens"))
        totals.output_tokens += _safe_int(usage.get("output_tokens"))
        totals.total_tokens += _safe_int(usage.get("total_tokens"))
        totals.estimated_input_tokens += _safe_int(usage.get("estimated_input_tokens"))
    return totals


def _token_usage_dict(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    usage: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_input_tokens",
    ):
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            usage[key] = item
    return usage or None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _preview_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"value": _preview_value(value)}

    preview: dict[str, object] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_DICT_PREVIEW_ITEMS:
            preview["..."] = f"{len(value) - MAX_DICT_PREVIEW_ITEMS} more fields"
            break
        preview[str(key)] = _preview_value(item)

    return preview


def _preview_value(value: object) -> object:
    if isinstance(value, str):
        return _preview_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        items = [_preview_value(item) for item in value[:MAX_LIST_PREVIEW_ITEMS]]
        if len(value) > MAX_LIST_PREVIEW_ITEMS:
            items.append(f"... {len(value) - MAX_LIST_PREVIEW_ITEMS} more items")
        return items
    if isinstance(value, dict):
        return _preview_dict(value)

    return _preview_text(str(value))


def _preview_text(value: str) -> str:
    if len(value) <= MAX_TEXT_PREVIEW:
        return value

    return f"{value[:MAX_TEXT_PREVIEW]}..."


def _tool_call_status(tool_name: str, output: dict[str, object]) -> str:
    if tool_name == "_Exception" or "error" in output:
        return "error"

    status = output.get("status")
    if isinstance(status, str) and status:
        return status

    return "ok"


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) else 0
