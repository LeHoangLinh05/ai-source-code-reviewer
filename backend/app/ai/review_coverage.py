"""Coverage helpers for backend-directed AI review evidence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.ai.review_plan import (
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)
from app.ai.source_evidence import SOURCE_TOOL_NAMES, source_chunk_keys
from app.ai.tool_runtime import get_ai_tool_runtime
from app.db.mongodb import CHUNK_METADATA_COLLECTION, TOOL_CALL_LOGS_COLLECTION
from app.models.review_job import ReviewJob


async def load_chunk_review_coverage(
    job_id: UUID,
    *,
    missing_limit: int | None = 25,
) -> tuple[int, int, list[dict[str, object]]]:
    """Return reviewed, target, and missing source chunk coverage for one job."""

    runtime = get_ai_tool_runtime()
    job_filter = {"job_id": str(job_id)}
    chunk_documents = (
        await runtime.mongodb_database[CHUNK_METADATA_COLLECTION]
        .find(job_filter)
        .to_list(length=None)
    )
    job_options = await _load_job_options(job_id)
    review_mode = get_review_mode(job_options)
    review_plan = build_chunk_review_plan(
        chunk_documents=chunk_documents,
        review_mode=review_mode,
        max_smart_chunks=get_smart_review_max_chunks(job_options),
    )
    expected_chunks = expected_chunk_keys_from_plan(review_plan)
    if not expected_chunks:
        return 0, 0, []

    tool_documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find({**job_filter, "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)}})
        .to_list(length=None)
    )
    reviewed_chunks = source_chunk_keys(tool_documents)
    missing_chunk_keys = sorted(expected_chunks - reviewed_chunks)
    missing_chunks = (
        missing_chunk_keys
        if missing_limit is None
        else missing_chunk_keys[:missing_limit]
    )
    return (
        len(reviewed_chunks & expected_chunks),
        len(expected_chunks),
        [
            {"file_path": file_path, "chunk_index": chunk_index}
            for file_path, chunk_index in missing_chunks
        ],
    )


async def _load_job_options(job_id: UUID) -> dict[str, object] | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    return options if isinstance(options, dict) else None
