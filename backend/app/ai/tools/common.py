"""Shared helpers for AI review tools."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import get_ai_tool_runtime
from app.models.review_job import ReviewJob

EMPTY_AGENT_JOB_ID_INPUTS = {"", "{}", "none", "null"}


async def get_job(job_id: str | UUID, session: AsyncSession | None = None) -> ReviewJob:
    """Load a review job or fail with a clear tool error."""

    runtime = get_ai_tool_runtime()
    job_uuid = parse_job_uuid(job_id)
    db_session = session or runtime.postgres_session
    result = await db_session.execute(select(ReviewJob).where(ReviewJob.id == job_uuid))
    job = result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Review job not found: {job_uuid}")

    return job


def parse_job_uuid(job_id: str | UUID) -> UUID:
    """Parse a job UUID from direct or JSON-shaped LangChain tool input."""

    if isinstance(job_id, UUID):
        return job_id

    raw_job_id = str(job_id).strip()
    if raw_job_id.startswith("{"):
        try:
            parsed = json.loads(raw_job_id)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict) and "job_id" in parsed:
                raw_job_id = str(parsed["job_id"]).strip()

    return UUID(raw_job_id)


def parse_job_uuid_or_current(job_id: str | UUID | None) -> UUID:
    """Parse a job UUID, falling back to the current tool runtime for empty input."""

    runtime = get_ai_tool_runtime()
    if job_id is None:
        return runtime.job_id
    if isinstance(job_id, UUID):
        return job_id

    raw_job_id = str(job_id).strip()
    if raw_job_id.lower() in EMPTY_AGENT_JOB_ID_INPUTS:
        return runtime.job_id
    if raw_job_id.startswith("{"):
        parsed = parse_json_object_text(raw_job_id)
        if parsed is not None:
            parsed_job_id = parsed.get("job_id")
            if parsed_job_id is None or str(parsed_job_id).strip() == "":
                return runtime.job_id

    return parse_job_uuid(raw_job_id)


def unwrap_react_json_input(data: Any, first_field: str) -> Any:
    """Unwrap JSON strings that text ReAct sometimes maps into the first field."""

    if not isinstance(data, dict):
        return data

    raw_value = data.get(first_field)
    if not isinstance(raw_value, str):
        return data

    parsed = parse_json_object_text(raw_value)
    if parsed is None:
        return data

    if set(data) == {first_field}:
        return parsed

    merged = dict(data)
    merged.update(parsed)
    return merged


def parse_json_object_text(value: object) -> dict[str, Any] | None:
    """Return a JSON object when a ReAct action string contains one."""

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
    if len(lines) < 2:
        return raw_text

    opening_fence = lines[0].strip().lower()
    if opening_fence not in {"```", "```json"}:
        return raw_text

    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return "\n".join(lines[1:]).strip()


def resolve_sandbox_file(file_path: str) -> Path:
    """Resolve a user-supplied repo path inside the current sandbox."""

    runtime = get_ai_tool_runtime()
    sandbox_root = runtime.sandbox_path.resolve()
    source_path = (sandbox_root / file_path).resolve()
    try:
        source_path.relative_to(sandbox_root)
    except ValueError as error:
        raise ValueError(f"File path escapes sandbox: {file_path}") from error

    if not source_path.is_file():
        raise ValueError(f"File does not exist in sandbox: {file_path}")

    return source_path


def serialize_mongo_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-friendly MongoDB document copy."""

    serialized = dict(document)
    if "_id" in serialized:
        serialized["_id"] = str(serialized["_id"])

    return serialized
