"""Unified, job-isolated source-code discovery for the Review Agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from app.ai.rag.bm25_index import BM25Document, BM25Index
from app.ai.rag.code_retriever import CodeSemanticRetriever, RetrievedCodeChunk
from app.ai.tool_runtime import (
    MAX_DISTINCT_SEARCHES_PER_INVESTIGATION,
    MAX_SOURCE_SEARCHES_PER_PASS,
    SearchDecision,
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

SearchMode = Literal["auto", "semantic", "exact"]

_retriever: CodeSemanticRetriever | None = None
DEFAULT_SEMANTIC_CODE_QUERY = (
    "Review high-risk authentication, authorization, input validation, database, "
    "websocket, and AI pipeline behavior in the current repository"
)
SEMANTIC_PREVIEW_MAX_CHARS = 400
REVIEW_SOURCE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".conf",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".sql",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".vue",
        ".yaml",
        ".yml",
    }
)
REVIEW_SOURCE_FILENAMES = frozenset(
    {
        ".env.example",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yaml",
        "docker-compose.yml",
        "dockerfile",
        "makefile",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
    }
)
NON_SOURCE_PATH_PARTS = frozenset(
    {
        "docs",
        "documentation",
        "spec",
        "specs",
    }
)
_SEARCH_STOP_WORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "has",
        "have",
        "implementation",
        "inspect",
        "repository",
        "requirement",
        "the",
        "this",
        "verify",
        "whether",
        "with",
    }
)
ROADMAP_RULE_ID_PATTERN = re.compile(r"\b(rc-[a-z0-9]+-\d+)\b", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class CodeSearchResult:
    """One preview-only result from semantic or exact retrieval."""

    content: str
    metadata: dict[str, object]
    semantic_score: float | None
    lexical_score: float | None
    final_score: float


@dataclass(slots=True)
class JobCodeCorpus:
    """In-memory source corpus shared by code search calls in one review pass."""

    documents: list[dict[str, object]]
    bm25_index: BM25Index


class SearchCodeInput(BaseModel):
    """Input schema for unified source-code discovery."""

    query: str = Field(min_length=1)
    investigation_id: str | None = Field(default=None, min_length=1)
    mode: SearchMode = "auto"
    job_id: str | None = None
    file_path: str | None = None
    rule_id: str | None = None
    audit_plan_item_id: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)
    language: str | None = None
    risk_area: str | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        data = unwrap_react_json_input(data, "investigation_id")
        return unwrap_react_json_input(data, "query")


class SearchCodeSemanticInput(BaseModel):
    """Compatibility schema for callers not yet migrated to unified search."""

    job_id: str | None = None
    query: str | None = None
    file_path: str | None = None
    top_k: int = 3
    language: str | None = None
    risk_area: str | None = None
    investigation_id: str = "legacy-semantic-search"

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


@tool(args_schema=SearchCodeInput)
async def search_code(
    query: str,
    investigation_id: str | None = None,
    mode: SearchMode = "auto",
    job_id: str | None = None,
    file_path: str | None = None,
    rule_id: str | None = None,
    audit_plan_item_id: str | None = None,
    top_k: int = 3,
    language: str | None = None,
    risk_area: str | None = None,
) -> dict[str, object]:
    """Discover source with stable hybrid, semantic, or exact retrieval."""

    return await _search_code_impl(
        investigation_id=investigation_id,
        job_id=job_id,
        query=query,
        mode=mode,
        file_path=file_path,
        rule_id=rule_id,
        audit_plan_item_id=audit_plan_item_id,
        top_k=top_k,
        language=language,
        risk_area=risk_area,
    )


@tool(args_schema=SearchCodeSemanticInput)
async def search_code_semantic(
    job_id: str | None = None,
    query: str | None = None,
    file_path: str | None = None,
    top_k: int = 3,
    language: str | None = None,
    risk_area: str | None = None,
    investigation_id: str = "legacy-semantic-search",
) -> dict[str, object]:
    """Compatibility wrapper for semantic-only code discovery."""

    return await _search_code_semantic_impl(
        job_id=job_id,
        query=query,
        file_path=file_path,
        top_k=top_k,
        language=language,
        risk_area=risk_area,
        investigation_id=investigation_id,
    )


async def _search_code_semantic_impl(
    *,
    job_id: str | None,
    query: str | None,
    file_path: str | None = None,
    top_k: int,
    language: str | None,
    risk_area: str | None,
    investigation_id: str = "legacy-semantic-search",
) -> dict[str, object]:
    """Compatibility implementation retained for existing internal callers/tests."""

    return await _search_code_impl(
        investigation_id=investigation_id,
        job_id=job_id,
        query=_semantic_query(query=query, file_path=file_path),
        mode="semantic",
        file_path=file_path,
        rule_id=None,
        audit_plan_item_id=None,
        top_k=top_k,
        language=language,
        risk_area=risk_area,
    )


async def _search_code_impl(
    *,
    investigation_id: str | None,
    job_id: str | None,
    query: str,
    mode: SearchMode,
    file_path: str | None,
    rule_id: str | None = None,
    audit_plan_item_id: str | None = None,
    top_k: int,
    language: str | None,
    risk_area: str | None,
) -> dict[str, object]:
    await ensure_ai_job_active()
    runtime = get_ai_tool_runtime()
    job_uuid = parse_job_uuid_or_current(job_id)
    if job_uuid != runtime.job_id:
        raise ValueError("job_id must match the active AI review job")

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query is required")
    normalized_rule_id = _normalize_roadmap_rule_id(rule_id)
    normalized_audit_plan_item_id = _normalize_audit_plan_item_id(audit_plan_item_id)
    normalized_investigation_id = _normalize_investigation_id(
        investigation_id=investigation_id,
        rule_id=normalized_rule_id,
        query=normalized_query,
    )
    if normalized_investigation_id == str(runtime.job_id):
        return {
            "status": "invalid_investigation_scope",
            "investigation_id": normalized_investigation_id,
            "results": [],
            "next_action": (
                "Create a stable hypothesis-specific investigation_id. Never use "
                "job_id or session_id, and use a distinct ID for an unrelated "
                "requirement or defect hypothesis."
            ),
        }
    if mode == "semantic" and not get_settings().enable_code_semantic_search:
        return {
            "status": "unavailable",
            "reason": "code semantic search is disabled",
            "results": [],
        }

    decision = runtime.prepare_code_search(
        investigation_id=normalized_investigation_id,
        query=normalized_query,
        mode=mode,
        filters=(file_path, language, risk_area),
    )
    if decision.status != "ready":
        return _stopped_search_response(
            decision=decision,
            investigation_id=normalized_investigation_id,
            mode=mode,
            runtime=runtime,
        )

    results, strategies = await _retrieve_results(
        job_id=str(job_uuid),
        query=normalized_query,
        mode=mode,
        file_path=file_path,
        top_k=min(max(top_k, 1), 10),
        language=language,
        risk_area=risk_area,
    )
    serialized_results = [
        await _validate_and_serialize_result(
            result,
            job_id=str(job_uuid),
            result_rank=index,
        )
        for index, result in enumerate(results, start=1)
    ]
    chunk_keys = {
        (
            str(result["file_path"]),
            _required_int(result, "chunk_index"),
        )
        for result in serialized_results
    }
    near_duplicate = runtime.record_code_search(
        investigation_id=normalized_investigation_id,
        decision=decision,
        mode=mode,
        result_chunk_keys=chunk_keys,
    )
    if near_duplicate is not None:
        return {
            "status": "no_new_evidence",
            "query_id": decision.query_id,
            "previous_query_id": near_duplicate.query_id,
            "investigation_id": normalized_investigation_id,
            "normalized_query": decision.normalized_query,
            "requested_mode": mode,
            "rule_id": normalized_rule_id,
            "audit_plan_item_id": normalized_audit_plan_item_id,
            "strategy_used": strategies,
            "results": [],
            "next_action": "Change search mode or narrow the investigation intent.",
            **_remaining_budget(runtime, normalized_investigation_id),
        }

    return {
        "status": "ok",
        "query_id": decision.query_id,
        "investigation_id": normalized_investigation_id,
        "normalized_query": decision.normalized_query,
        "requested_mode": mode,
        "rule_id": normalized_rule_id,
        "audit_plan_item_id": normalized_audit_plan_item_id,
        "strategy_used": strategies,
        "results": serialized_results,
        "evidence_gain": len(chunk_keys),
        "next_action": "Read only results needed to test the current hypothesis.",
        **_remaining_budget(runtime, normalized_investigation_id),
    }


def _normalize_investigation_id(
    *,
    investigation_id: str | None,
    rule_id: str | None,
    query: str,
) -> str:
    if isinstance(investigation_id, str) and investigation_id.strip():
        return investigation_id.strip()

    if rule_id is not None:
        return rule_id.lower()

    rule_match = ROADMAP_RULE_ID_PATTERN.search(query)
    if rule_match is not None:
        return f"auto-{rule_match.group(1).lower()}"

    query_terms = re.findall(r"[a-z0-9_/.:-]+", query.lower())
    readable_terms = [
        term.strip("/:.")
        for term in query_terms
        if term not in _SEARCH_STOP_WORDS and len(term.strip("/:.")) >= 3
    ][:3]
    readable_prefix = "-".join(readable_terms) or "query"
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:8]
    return f"auto-{readable_prefix[:24]}-{query_hash}"


def _normalize_roadmap_rule_id(rule_id: str | None) -> str | None:
    if not isinstance(rule_id, str):
        return None
    match = ROADMAP_RULE_ID_PATTERN.search(rule_id)
    return match.group(1).upper() if match is not None else None


def _normalize_audit_plan_item_id(audit_plan_item_id: str | None) -> str | None:
    if not isinstance(audit_plan_item_id, str):
        return None
    normalized = audit_plan_item_id.strip()
    return normalized or None


async def _retrieve_results(
    *,
    job_id: str,
    query: str,
    mode: SearchMode,
    file_path: str | None,
    top_k: int,
    language: str | None,
    risk_area: str | None,
) -> tuple[list[CodeSearchResult], list[str]]:
    semantic_results: list[CodeSearchResult] = []
    strategies: list[str] = []
    if mode in {"auto", "semantic"} and get_settings().enable_code_semantic_search:
        index_scope = await _index_scope_for_job(job_id)
        retrieved = await asyncio.to_thread(
            get_code_retriever().search,
            query=query,
            job_id=job_id,
            repo_branch_key=index_scope.repo_branch_key,
            index_generation_key=index_scope.index_generation_key,
            top_k=top_k,
            language=language,
            risk_area=risk_area,
        )
        semantic_results = [
            CodeSearchResult(
                content=result.content,
                metadata=result.metadata,
                semantic_score=result.semantic_score,
                lexical_score=None,
                final_score=result.semantic_score,
            )
            for result in retrieved
            if _is_allowed_search_result(result.metadata, file_path=file_path)
        ]
        strategies.append("semantic")
    elif mode == "semantic":
        return [], []

    exact_results: list[CodeSearchResult] = []
    if mode in {"auto", "exact"}:
        exact_results = await _exact_search(
            job_id=job_id,
            query=query,
            file_path=file_path,
            language=language,
            risk_area=risk_area,
            top_k=top_k,
        )
        strategies.append("exact")

    return _merge_results(semantic_results, exact_results, top_k), strategies


async def _exact_search(
    *,
    job_id: str,
    query: str,
    file_path: str | None,
    language: str | None,
    risk_area: str | None,
    top_k: int,
) -> list[CodeSearchResult]:
    filters: dict[str, object] = {"job_id": job_id}
    if file_path:
        filters["file_path"] = file_path
    if language:
        filters["language"] = language
    if risk_area:
        filters["risk_area"] = risk_area
    corpus = await _job_code_corpus(job_id)
    documents = [
        document
        for document in corpus.documents
        if _document_matches_filters(document, filters)
    ]
    terms = _query_terms(query)
    if not terms:
        return []

    scored: list[CodeSearchResult] = []
    for document in documents:
        if not _is_review_source_path(str(document.get("file_path") or "")):
            continue
        content = document.get("chunk_text")
        if not isinstance(content, str):
            continue
        searchable = " ".join(
            [
                content,
                str(document.get("module") or ""),
                str(document.get("function_name") or ""),
                str(document.get("class_name") or ""),
                _imports_text(document.get("imports")),
            ]
        ).lower()
        matched = sum(term in searchable for term in terms)
        if matched == 0:
            continue
        score = matched / len(terms)
        scored.append(
            CodeSearchResult(
                content=content,
                metadata=dict(document),
                semantic_score=None,
                lexical_score=score,
                final_score=score,
            )
        )
    return sorted(scored, key=lambda item: item.final_score, reverse=True)[:top_k]


async def _job_code_corpus(job_id: str) -> JobCodeCorpus:
    runtime = get_ai_tool_runtime()
    corpus = runtime.code_corpus
    if isinstance(corpus, JobCodeCorpus):
        return corpus

    documents = (
        await runtime.mongodb_database[CHUNK_METADATA_COLLECTION]
        .find({"job_id": job_id})
        .to_list(length=None)
    )
    normalized_documents = [
        dict(document) for document in documents if isinstance(document, dict)
    ]
    bm25_documents: list[BM25Document] = []
    for document in normalized_documents:
        content = document.get("chunk_text")
        if not isinstance(content, str) or not content:
            continue
        bm25_documents.append(
            BM25Document(
                id=(f"{document.get('file_path')}:{document.get('chunk_index')}"),
                content=_document_searchable_text(document, content),
                metadata=document,
            )
        )

    bm25_index = BM25Index()
    bm25_index.add_documents(bm25_documents)
    runtime.code_corpus = JobCodeCorpus(
        documents=normalized_documents,
        bm25_index=bm25_index,
    )
    return runtime.code_corpus


def _document_matches_filters(
    document: dict[str, object],
    filters: dict[str, object],
) -> bool:
    return all(document.get(key) == value for key, value in filters.items())


def _document_searchable_text(
    document: dict[str, object],
    content: str,
) -> str:
    return " ".join(
        [
            content,
            str(document.get("module") or ""),
            str(document.get("function_name") or ""),
            str(document.get("class_name") or ""),
            _imports_text(document.get("imports")),
        ]
    )


def _imports_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return " ".join(str(item) for item in value)


@dataclass(slots=True, frozen=True)
class _IndexScope:
    repo_branch_key: str | None
    index_generation_key: str | None


async def _index_scope_for_job(job_id: str) -> _IndexScope:
    runtime = get_ai_tool_runtime()
    try:
        document = await runtime.mongodb_database[CHUNK_METADATA_COLLECTION].find_one(
            {"job_id": job_id},
        )
    except (AttributeError, TypeError):
        return _IndexScope(repo_branch_key=None, index_generation_key=None)
    if not isinstance(document, dict):
        return _IndexScope(repo_branch_key=None, index_generation_key=None)

    repo_branch_key = document.get("repo_branch_key")
    index_generation_key = document.get("index_generation_key")
    return _IndexScope(
        repo_branch_key=repo_branch_key if isinstance(repo_branch_key, str) else None,
        index_generation_key=(
            index_generation_key if isinstance(index_generation_key, str) else None
        ),
    )


def _is_allowed_search_result(
    metadata: dict[str, object],
    *,
    file_path: str | None,
) -> bool:
    result_path = metadata.get("file_path")
    if not isinstance(result_path, str):
        return False
    if file_path is not None and result_path != file_path:
        return False
    return _is_review_source_path(result_path)


def _is_review_source_path(file_path: str) -> bool:
    normalized_path = file_path.replace("\\", "/").strip().lower()
    if not normalized_path:
        return False

    path_parts = [part for part in normalized_path.split("/") if part]
    if any(part in NON_SOURCE_PATH_PARTS for part in path_parts[:-1]):
        return False

    filename = path_parts[-1]
    if filename in REVIEW_SOURCE_FILENAMES:
        return True

    if "." not in filename:
        return False

    suffix = "." + filename.rsplit(".", 1)[-1]
    return suffix in REVIEW_SOURCE_EXTENSIONS


def _merge_results(
    semantic: list[CodeSearchResult],
    exact: list[CodeSearchResult],
    top_k: int,
) -> list[CodeSearchResult]:
    merged: dict[tuple[str, int], CodeSearchResult] = {}
    for result in [*semantic, *exact]:
        key = (
            str(result.metadata.get("file_path") or ""),
            _required_int(result.metadata, "chunk_index"),
        )
        previous = merged.get(key)
        if previous is None:
            merged[key] = result
            continue
        semantic_score = previous.semantic_score or result.semantic_score
        lexical_score = previous.lexical_score or result.lexical_score
        merged[key] = CodeSearchResult(
            content=result.content,
            metadata=result.metadata,
            semantic_score=semantic_score,
            lexical_score=lexical_score,
            final_score=(semantic_score or 0.0) * 0.7 + (lexical_score or 0.0) * 0.3,
        )
    return sorted(merged.values(), key=lambda item: item.final_score, reverse=True)[
        :top_k
    ]


async def _validate_and_serialize_result(
    result: CodeSearchResult | RetrievedCodeChunk,
    *,
    job_id: str,
    result_rank: int,
) -> dict[str, object]:
    metadata = result.metadata
    file_path = _required_string(metadata, "file_path")
    chunk_index = _required_int(metadata, "chunk_index")
    persisted_chunk = await _find_persisted_result_chunk(
        job_id=job_id,
        metadata=metadata,
        file_path=file_path,
        chunk_index=chunk_index,
    )
    if persisted_chunk is None:
        raise ValueError(
            "Search result metadata does not belong to the active review job"
        )
    if persisted_chunk.get("chunk_text") != result.content:
        raise ValueError("Search result content does not match the indexed job chunk")
    resolve_sandbox_file(file_path)

    semantic_score = result.semantic_score
    lexical_score = (
        result.lexical_score if isinstance(result, CodeSearchResult) else None
    )
    final_score = (
        result.final_score if isinstance(result, CodeSearchResult) else semantic_score
    )
    return {
        "result_index": result_rank,
        "file_path": file_path,
        "chunk_index": chunk_index,
        "line_start": _required_int(metadata, "line_start"),
        "line_end": _required_int(metadata, "line_end"),
        "function_name": _optional_string(metadata.get("function_name")),
        "class_name": _optional_string(metadata.get("class_name")),
        "language": _required_string(metadata, "language"),
        "risk_area": _required_string(metadata, "risk_area"),
        "semantic_score": semantic_score,
        "lexical_score": lexical_score,
        "final_score": final_score,
        "preview": _preview_content(result.content),
        "preview_truncated": len(result.content) > SEMANTIC_PREVIEW_MAX_CHARS,
        "evidence_status": "preview_only",
    }


async def _find_persisted_result_chunk(
    *,
    job_id: str,
    metadata: dict[str, object],
    file_path: str,
    chunk_index: int,
) -> dict[str, object] | None:
    runtime = get_ai_tool_runtime()
    embedding_cache_id = metadata.get("embedding_cache_id")
    if isinstance(embedding_cache_id, str) and embedding_cache_id:
        document = await runtime.mongodb_database[CHUNK_METADATA_COLLECTION].find_one(
            {"job_id": job_id, "embedding_cache_id": embedding_cache_id}
        )
        if isinstance(document, dict):
            return document

    document = await runtime.mongodb_database[CHUNK_METADATA_COLLECTION].find_one(
        {"job_id": job_id, "file_path": file_path, "chunk_index": chunk_index}
    )
    return document if isinstance(document, dict) else None


def _stopped_search_response(
    *,
    decision: SearchDecision,
    investigation_id: str,
    mode: SearchMode,
    runtime: Any,
) -> dict[str, object]:
    response: dict[str, object] = {
        "status": decision.status,
        "query_id": decision.query_id,
        "investigation_id": investigation_id,
        "normalized_query": decision.normalized_query,
        "requested_mode": mode,
        "strategy_used": [],
        "results": [],
        **_remaining_budget(runtime, investigation_id),
    }
    if decision.previous_query_id:
        response["previous_query_id"] = decision.previous_query_id
    if decision.status == "duplicate_query":
        response["next_action"] = "Use the previous result or change mode/intent."
    elif response["remaining_investigation_searches"] == 0:
        response["reason"] = "Investigation search budget exhausted"
        response["next_action"] = (
            "Use a distinct investigation_id for unrelated requirements, or stop "
            "this investigation and conclude insufficient evidence."
        )
    else:
        response["reason"] = "Pass search budget exhausted"
        response["next_action"] = (
            "Stop searching in this review pass and create only evidence-backed "
            "issues from source already read."
        )
    return response


def _remaining_budget(runtime: Any, investigation_id: str) -> dict[str, int]:
    return {
        "remaining_investigation_searches": max(
            0,
            MAX_DISTINCT_SEARCHES_PER_INVESTIGATION
            - len(runtime.search_attempts.get(investigation_id, [])),
        ),
        "remaining_pass_searches": max(
            0,
            MAX_SOURCE_SEARCHES_PER_PASS - runtime.source_search_count,
        ),
    }


def get_code_retriever() -> CodeSemanticRetriever:
    """Return the lazy semantic retriever used by unified search."""

    try:
        runtime_retriever = get_ai_tool_runtime().code_retriever
    except RuntimeError:
        runtime_retriever = None
    if isinstance(runtime_retriever, CodeSemanticRetriever):
        return runtime_retriever

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


def _query_terms(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_/.:-]+", query.lower())
        if len(token) >= 3 and token not in _SEARCH_STOP_WORDS
    }


def _required_string(metadata: dict[str, object], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Search result has invalid {field} metadata")
    return value


def _required_int(metadata: dict[str, object], field: str) -> int:
    value: Any = metadata.get(field)
    if not isinstance(value, int):
        raise ValueError(f"Search result has invalid {field} metadata")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _preview_content(content: str) -> str:
    preview = content[:SEMANTIC_PREVIEW_MAX_CHARS].rstrip()
    return preview if len(content) <= SEMANTIC_PREVIEW_MAX_CHARS else f"{preview}\n..."
