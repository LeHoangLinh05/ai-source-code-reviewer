"""Mongo-backed tracing callback for AI tools and backend probe events."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

try:
    from langchain_core.callbacks import AsyncCallbackHandler
except ImportError:

    class AsyncCallbackHandler:  # type: ignore[no-redef]
        """Fallback base class when LangChain is not installed at app startup."""


from app.repositories.mongodb_repository import ToolCallLogRepository
from app.schemas.mongodb import ToolCallLogDocument


class MongoToolCallLogger(AsyncCallbackHandler):
    """Persist LangChain callback and backend trace events in MongoDB."""

    def __init__(
        self,
        *,
        job_id: UUID,
        session_id: UUID,
        repository: ToolCallLogRepository,
        agent_type: Literal["review", "report"] = "review",
    ) -> None:
        self.job_id = job_id
        self.session_id = session_id
        self.repository = repository
        self.agent_type = agent_type
        self.sequence = 0
        self.started_at_by_run_id: dict[UUID, datetime] = {}
        self.perf_start_by_run_id: dict[UUID, float] = {}
        self.input_by_run_id: dict[UUID, dict[str, object]] = {}
        self.tool_name_by_run_id: dict[UUID, str] = {}
        self.sequence_by_run_id: dict[UUID, int] = {}

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Remember tool inputs until LangChain emits the matching end event."""

        self.sequence += 1
        self.sequence_by_run_id[run_id] = self.sequence
        self.started_at_by_run_id[run_id] = datetime.now(UTC)
        self.perf_start_by_run_id[run_id] = time.perf_counter()
        self.input_by_run_id[run_id] = _normalize_tool_input(
            _first_present_tool_input(kwargs, input_str),
        )
        self.tool_name_by_run_id[run_id] = str(serialized.get("name", "unknown"))

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Write one successful tool trace."""

        _ = kwargs
        await self._write_log(
            run_id=run_id,
            output=_normalize_tool_output(output),
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Write failed tool traces too so /ai-debug can show validation rejects."""

        _ = kwargs
        await self._write_log(
            run_id=run_id,
            output={"error": str(error), "error_type": type(error).__name__},
        )

    async def _write_log(
        self,
        *,
        run_id: UUID,
        output: dict[str, object],
    ) -> None:
        started_at = self.started_at_by_run_id.pop(run_id, datetime.now(UTC))
        perf_started_at = self.perf_start_by_run_id.pop(run_id, time.perf_counter())
        duration_ms = int((time.perf_counter() - perf_started_at) * 1000)
        tool_input = self.input_by_run_id.pop(run_id, {})
        tool_name = self.tool_name_by_run_id.pop(run_id, "unknown")
        sequence = self.sequence_by_run_id.pop(run_id, self.sequence)
        status = _tool_log_status(output)
        await self.repository.insert_one(
            ToolCallLogDocument(
                job_id=self.job_id,
                session_id=self.session_id,
                agent_type=self.agent_type,
                sequence=sequence,
                tool_name=tool_name,
                called_at=started_at,
                duration_ms=max(0, duration_ms),
                input=tool_input,
                output=output,
                status=status,
            )
        )

    async def write_synthetic_tool_log(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, object],
        output: dict[str, object],
    ) -> None:
        """Write a backend-produced trace entry using the same sequence stream."""

        self.sequence += 1
        duration_ms = output.get("duration_ms")
        status = _tool_log_status(output)
        await self.repository.insert_one(
            ToolCallLogDocument(
                job_id=self.job_id,
                session_id=self.session_id,
                agent_type=self.agent_type,
                sequence=self.sequence,
                tool_name=tool_name,
                called_at=datetime.now(UTC),
                duration_ms=duration_ms if isinstance(duration_ms, int) else 0,
                input=tool_input,
                output=output,
                status=status,
            )
        )


def _first_present_tool_input(kwargs: dict[str, Any], input_str: str) -> Any:
    for key in ("inputs", "input", "tool_input"):
        value = kwargs.get(key)
        if value is not None:
            return value

    return input_str


def _normalize_tool_input(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value}
        if isinstance(parsed, dict):
            return _json_safe_dict(parsed)
        return {"input": parsed}

    return {"input": _json_safe(value)}


def _normalize_tool_output(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, BaseModel):
        return _json_safe_dict(value.model_dump(mode="json"))

    content = getattr(value, "content", None)
    if content is not None:
        return _normalize_tool_output(content)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            normalized_output: object = value
        else:
            if isinstance(parsed, dict):
                return _json_safe_dict(parsed)
            normalized_output = parsed
    else:
        normalized_output = _json_safe(value)

    return {"output": normalized_output}


def _tool_log_status(output: dict[str, object]) -> str:
    if "error" in output:
        return "error"
    status = output.get("status")
    if isinstance(status, str) and status:
        return status
    return "ok"


def _json_safe_dict(value: dict[str, Any]) -> dict[str, object]:
    return {str(key): _json_safe(item) for key, item in value.items()}


def _json_safe(value: Any) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    return str(value)
