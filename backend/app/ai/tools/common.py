"""Shared helpers for AI review tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import get_ai_tool_runtime
from app.models.review_job import ReviewJob


async def get_job(job_id: str | UUID, session: AsyncSession | None = None) -> ReviewJob:
    """Load a review job or fail with a clear tool error."""

    runtime = get_ai_tool_runtime()
    job_uuid = UUID(str(job_id))
    db_session = session or runtime.postgres_session
    result = await db_session.execute(select(ReviewJob).where(ReviewJob.id == job_uuid))
    job = result.scalar_one_or_none()
    if job is None:
        raise ValueError(f"Review job not found: {job_uuid}")

    return job


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
