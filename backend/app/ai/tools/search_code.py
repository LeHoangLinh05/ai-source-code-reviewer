"""AI tool for job-isolated semantic source-code search."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from app.ai.rag.code_retriever import CodeSemanticRetriever, RetrievedCodeChunk
from app.ai.tool_runtime import (
    ensure_ai_job_active,
    get_ai_tool_runtime,
)
from app.ai.tools.common import (
    parse_job_uuid_or_current,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
from app.core.config import get_settings
from app.db.mongodb import CHUNK_METADATA_COLLECTION

_retriever: CodeSemanticRetriever | None = None
DEFAULT_SEMANTIC_CODE_QUERY = (
    "Review high-risk authentication, authorization, input validation, database, "
    "websocket, and AI pipeline behavior in the current repository"
)


class SearchCodeSemanticInput(BaseModel):
    """Input schema for semantic source-code retrieval."""

    job_id: str | None = None
    query: str | None = None
    file_path: str | None = None
    top_k: int = 3
    language: str | None = None
    risk_area: str | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        data = unwrap_react_json_input(data, "job_id")
        data = unwrap_react_json_input(data, "query")
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        file_path = normalized.get("file_path")
        if (
            "query" not in normalized
            and isinstance(file_path, str)
            and file_path.strip()
        ):
            normalized["query"] = f"Review relevant behavior in {file_path.strip()}"

        return normalized


@tool(args_schema=SearchCodeSemanticInput)
async def search_code_semantic(
    job_id: str | None = None,
    query: str | None = None,
    file_path: str | None = None,
    top_k: int = 3,
    language: str | None = None,
    risk_area: str | None = None,
) -> dict[str, object]:
    """Tìm code theo hành vi trong đúng review job và trả full exact indexed chunk content để dùng trực tiếp làm evidence."""

    return await _search_code_semantic_impl(
        job_id=job_id,
        query=query,
        file_path=file_path,
        top_k=top_k,
        language=language,
        risk_area=risk_area,
    )


async def _search_code_semantic_impl(
    *,
    job_id: str | None,
    query: str | None,
    file_path: str | None = None,
    top_k: int,
    language: str | None,
    risk_area: str | None,
) -> dict[str, object]:
    await ensure_ai_job_active()
    runtime = get_ai_tool_runtime()
    job_uuid = parse_job_uuid_or_current(job_id)
    if job_uuid != runtime.job_id:
        raise ValueError("job_id must match the active AI review job")
    normalized_query = _semantic_query(query=query, file_path=file_path)
    if not get_settings().enable_code_semantic_search:
        return {
            "status": "unavailable",
            "reason": "code semantic search is disabled",
            "results": [],
        }

    results = await asyncio.to_thread(
        get_code_retriever().search,
        query=normalized_query,
        job_id=job_uuid,
        top_k=top_k,
        language=language,
        risk_area=risk_area,
    )
    validated_results = [
        await _validate_and_serialize_result(result, job_id=str(job_uuid))
        for result in results
    ]
    return {"status": "ok", "results": validated_results}


async def _validate_and_serialize_result(
    result: RetrievedCodeChunk,
    *,
    job_id: str,
) -> dict[str, object]:
    metadata = result.metadata
    file_path = _required_string(metadata, "file_path")
    chunk_index = _required_int(metadata, "chunk_index")
    runtime = get_ai_tool_runtime()
    persisted_chunk = await runtime.mongodb_database[
        CHUNK_METADATA_COLLECTION
    ].find_one(
        {
            "job_id": job_id,
            "file_path": file_path,
            "chunk_index": chunk_index,
        }
    )
    if persisted_chunk is None:
        raise ValueError(
            "Semantic result metadata does not belong to the active review job"
        )
    if persisted_chunk.get("chunk_text") != result.content:
        raise ValueError("Semantic result content does not match the indexed job chunk")

    resolve_sandbox_file(file_path)
    serialized_result: dict[str, object] = {
        "file_path": file_path,
        "chunk_index": chunk_index,
        "line_start": _required_int(metadata, "line_start"),
        "line_end": _required_int(metadata, "line_end"),
        "function_name": _optional_string(metadata.get("function_name")),
        "class_name": _optional_string(metadata.get("class_name")),
        "language": _required_string(metadata, "language"),
        "risk_area": _required_string(metadata, "risk_area"),
        "semantic_score": result.semantic_score,
    }
    is_new_content, content_sha256, content_size = runtime.register_chunk_content(
        file_path=file_path,
        chunk_index=chunk_index,
        content=result.content,
    )
    if not is_new_content:
        serialized_result.update(
            {
                "status": "deduplicated",
                "summary": "chunk content was already provided in this review session",
                "content_sha256": content_sha256,
                "content_size": content_size,
            }
        )
        return serialized_result

    serialized_result["content"] = result.content
    return serialized_result


def get_code_retriever() -> CodeSemanticRetriever:
    """Return the lazy code retriever used by tool calls."""

    global _retriever

    if _retriever is None:
        _retriever = CodeSemanticRetriever()

    return _retriever


def _semantic_query(*, query: str | None, file_path: str | None) -> str:
    if isinstance(query, str) and query.strip():
        return query.strip()
    if isinstance(file_path, str) and file_path.strip():
        return f"Review relevant behavior in {file_path.strip()}"

    return DEFAULT_SEMANTIC_CODE_QUERY


def _required_string(metadata: dict[str, object], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Semantic result has invalid {field} metadata")

    return value


def _required_int(metadata: dict[str, object], field: str) -> int:
    value: Any = metadata.get(field)
    if not isinstance(value, int):
        raise ValueError(f"Semantic result has invalid {field} metadata")

    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
