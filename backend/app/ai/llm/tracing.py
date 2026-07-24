"""Redacted LLM input/output trace serialization and persistence."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from uuid import uuid4

MAX_LLM_TRACE_PREVIEW = 500
MAX_LLM_TRACE_MESSAGES = 8
MIN_TRACE_TUPLE_ITEMS = 2


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _model_name(model: object) -> str | None:
    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_token_usage(result: object) -> dict[str, int] | None:
    usage = getattr(result, "usage_metadata", None)
    normalized = _normalize_token_usage(usage)
    if normalized is not None:
        return normalized

    response_metadata = getattr(result, "response_metadata", None)
    if isinstance(response_metadata, dict):
        normalized = _normalize_token_usage(response_metadata.get("token_usage"))
        if normalized is not None:
            return normalized

    if isinstance(result, dict):
        normalized = _normalize_token_usage(result.get("usage_metadata"))
        if normalized is not None:
            return normalized
        response_metadata = result.get("response_metadata")
        if isinstance(response_metadata, dict):
            return _normalize_token_usage(response_metadata.get("token_usage"))

    return None


def _normalize_token_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    input_tokens = _usage_int(value, ("input_tokens", "prompt_tokens"))
    output_tokens = _usage_int(value, ("output_tokens", "completion_tokens"))
    total_tokens = _usage_int(value, ("total_tokens",))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    return usage if any(usage.values()) else None


def _usage_int(value: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            return item
    return 0


def _llm_input_trace(value: object) -> dict[str, object]:
    if isinstance(value, str):
        return {"kind": "text", **_text_trace(value)}
    if isinstance(value, list):
        return {
            "kind": "messages",
            "message_count": len(value),
            "messages": [
                _message_trace(item) for item in value[:MAX_LLM_TRACE_MESSAGES]
            ],
            "truncated_messages": max(0, len(value) - MAX_LLM_TRACE_MESSAGES),
        }
    return {"kind": type(value).__name__, **_object_trace(value)}


def _llm_output_trace(value: object) -> dict[str, object]:
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return {"kind": type(value).__name__, "content": _text_trace(content)}
    if isinstance(content, list):
        return {
            "kind": type(value).__name__,
            "content_item_count": len(content),
            "content_preview": _preview_text(str(content)),
        }
    return {"kind": type(value).__name__, **_object_trace(value)}


def _message_trace(value: object) -> dict[str, object]:
    if isinstance(value, tuple) and len(value) >= MIN_TRACE_TUPLE_ITEMS:
        tuple_role = str(value[0])
        tuple_content = str(value[1])
        return {"role": tuple_role, "content": _text_trace(tuple_content)}
    message_role = getattr(value, "type", None) or getattr(value, "role", None)
    message_content = getattr(value, "content", None)
    if isinstance(message_content, str):
        return {
            "role": str(message_role or type(value).__name__),
            "content": _text_trace(message_content),
        }
    return {"role": str(message_role or type(value).__name__), **_object_trace(value)}


def _object_trace(value: object) -> dict[str, object]:
    text = _json_trace_text(value)
    return {
        "size_chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "preview": _preview_text(text),
    }


def _text_trace(value: str) -> dict[str, object]:
    return {
        "size_chars": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "preview": _preview_text(value),
    }


def _json_trace_text(value: object) -> str:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json.dumps(model_dump(mode="json"), ensure_ascii=False, default=str)
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _preview_text(value: str) -> str:
    if len(value) <= MAX_LLM_TRACE_PREVIEW:
        return value
    return f"{value[:MAX_LLM_TRACE_PREVIEW]}..."


async def _write_llm_trace_event(
    *,
    provider: str,
    model: str | None,
    sequence: int,
    called_at: datetime,
    duration_ms: int,
    status: str,
    input_payload: dict[str, object] | None = None,
    token_usage: dict[str, int] | None = None,
    output: dict[str, object] | None = None,
) -> None:
    try:
        from app.ai.tools.runtime import get_ai_tool_runtime
        from app.repositories.mongodb_repository import ToolCallLogRepository
        from app.schemas.mongodb import ToolCallLogDocument
    except ImportError:
        return

    try:
        runtime = get_ai_tool_runtime()
    except RuntimeError:
        return

    repository = ToolCallLogRepository(runtime.mongodb_database)
    await repository.insert_one(
        ToolCallLogDocument(
            job_id=runtime.job_id,
            session_id=runtime.session_id,
            agent_type="review",
            sequence=sequence,
            tool_name="llm_call",
            called_at=called_at,
            duration_ms=duration_ms,
            input=input_payload or {},
            output=output or {},
            event_type="llm",
            provider=provider,
            model=model,
            phase="model_invoke",
            token_usage=token_usage,
            status=status,
            metadata={"trace_id": str(uuid4())},
        )
    )
