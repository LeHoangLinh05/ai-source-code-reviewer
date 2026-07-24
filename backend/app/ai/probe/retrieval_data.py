"""Persistence and vector-search adapters for probe retrieval."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.rag.bm25_index import BM25Document, BM25Index
from app.ai.rag.code_retriever import CodeSemanticRetriever, CodeSemanticSearchRequest
from app.db.mongodb import CHUNK_METADATA_COLLECTION


async def _load_chunk_documents(
    database: AsyncIOMotorDatabase,
    job_id: UUID,
) -> list[dict[str, Any]]:
    documents = (
        await database[CHUNK_METADATA_COLLECTION]
        .find({"job_id": str(job_id)})
        .to_list(length=None)
    )
    return [document for document in documents if isinstance(document, dict)]


def _repo_branch_key_from_documents(documents: list[dict[str, Any]]) -> str | None:
    for document in documents:
        repo_branch_key = document.get("repo_branch_key")
        if isinstance(repo_branch_key, str) and repo_branch_key:
            return repo_branch_key

    return None


def _index_generation_key_from_documents(
    documents: list[dict[str, Any]],
) -> str | None:
    for document in documents:
        index_generation_key = document.get("index_generation_key")
        if isinstance(index_generation_key, str) and index_generation_key:
            return index_generation_key

    return None


def _build_bm25_index(chunk_documents: list[dict[str, Any]]) -> BM25Index:
    documents: list[BM25Document] = []
    for document in chunk_documents:
        content = document.get("chunk_text")
        if not isinstance(content, str) or not content:
            continue
        searchable = " ".join(
            [
                str(document.get("file_path") or ""),
                str(document.get("module") or ""),
                str(document.get("function_name") or ""),
                str(document.get("class_name") or ""),
                " ".join(str(value) for value in document.get("imports", [])),
                content,
            ]
        )
        documents.append(
            BM25Document(
                id=_document_key(document),
                content=searchable,
                metadata=document,
            )
        )

    index = BM25Index()
    index.add_documents(documents)
    return index


async def _semantic_search(
    *,
    retriever: CodeSemanticRetriever,
    job_id: UUID,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    query: str,
    top_k: int,
) -> list[Any]:
    return await _to_thread_search(
        retriever,
        query=query,
        job_id=job_id,
        repo_branch_key=repo_branch_key,
        index_generation_key=index_generation_key,
        top_k=top_k,
    )


async def _semantic_search_many(
    *,
    retriever: CodeSemanticRetriever,
    requests: list[CodeSemanticSearchRequest],
) -> list[list[Any]]:
    return await _to_thread_search_many(retriever, requests=requests)


async def _to_thread_search_many(
    retriever: CodeSemanticRetriever,
    *,
    requests: list[CodeSemanticSearchRequest],
) -> list[list[Any]]:
    import asyncio

    return await asyncio.to_thread(retriever.search_many, requests)


async def _to_thread_search(
    retriever: CodeSemanticRetriever,
    *,
    query: str,
    job_id: UUID,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    top_k: int,
) -> list[Any]:
    import asyncio

    return await asyncio.to_thread(
        retriever.search,
        query=query,
        job_id=job_id,
        repo_branch_key=repo_branch_key,
        index_generation_key=index_generation_key,
        top_k=top_k,
    )


def _document_key(document: dict[str, Any]) -> str:
    return f"{document.get('file_path')}:{document.get('chunk_index')}"
