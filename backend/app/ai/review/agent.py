"""Main AI agent orchestration for repository review."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.probe.models import ProbeReviewConfig
from app.ai.probe.review import run_backend_directed_probe_review
from app.ai.reporting.final_report import synthesize_final_report
from app.ai.roadmap.catalog_service import _ensure_roadmap_catalog_loaded
from app.ai.tools.call_logging import MongoToolCallLogger
from app.ai.tools.runtime import (
    AIToolRuntime,
    ai_tool_runtime,
)
from app.models.review_issue import ReviewIssue
from app.repositories.mongodb_repository import ToolCallLogRepository

logger = logging.getLogger(__name__)

PROBE_REVIEW_COVERAGE_MISSING_CHUNK_LIMIT = 60


async def run_ai_review(
    *,
    job_id: UUID,
    sandbox_path: Path,
    postgres_session: AsyncSession,
    mongodb_database: AsyncIOMotorDatabase,
    code_embedding_store: Any | None = None,
) -> dict[str, Any]:
    """Run the AI review agent for one job using OpenAI."""

    session_id = uuid4()
    code_retriever = None
    if code_embedding_store is not None:
        from app.ai.rag.code_retriever import CodeSemanticRetriever

        code_retriever = CodeSemanticRetriever(code_embedding_store)
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=session_id,
        sandbox_path=sandbox_path,
        postgres_session=postgres_session,
        mongodb_database=mongodb_database,
        code_retriever=code_retriever,
    )

    from app.ai.llm.config import get_pipeline_llm, llm_session
    from app.core.config import get_settings

    async def run_with_model(llm: Any) -> dict[str, Any]:
        settings = get_settings()
        callback = MongoToolCallLogger(
            job_id=job_id,
            session_id=session_id,
            repository=ToolCallLogRepository(mongodb_database),
        )
        with ai_tool_runtime(runtime):
            await _ensure_roadmap_catalog_loaded(
                job_id=job_id,
                callback=callback,
            )
            probe_result = await run_backend_directed_probe_review(
                job_id=job_id,
                llm=llm,
                postgres_session=postgres_session,
                mongodb_database=mongodb_database,
                trace_writer=callback,
                code_retriever=runtime.code_retriever,
                config=ProbeReviewConfig(
                    enable_semantic_search=True,
                    max_chunks=settings.probe_retrieval_max_chunks,
                    defect_max_chunks=settings.probe_defect_max_chunks,
                    coverage_max_chunks=settings.probe_coverage_max_chunks,
                    roadmap_max_chunks=settings.probe_roadmap_max_chunks,
                    max_probes_per_batch=(settings.probe_judge_max_probes_per_batch),
                    max_chunks_per_batch=settings.probe_judge_max_chunks_per_batch,
                ),
            )
            review_result = {"output": probe_result.handoff}
            (
                reviewed_chunks,
                total_chunks,
                _missing_chunks,
            ) = await _load_review_coverage(
                job_id,
                missing_limit=PROBE_REVIEW_COVERAGE_MISSING_CHUNK_LIMIT,
            )
            report_context = await _build_report_context(
                job_id=job_id,
                postgres_session=postgres_session,
            )
            callback.agent_type = "report"
            report_result = await synthesize_final_report(
                job_id=job_id,
                review_handoff=str(review_result.get("output", "")),
                report_context=report_context,
                reviewed_chunks=reviewed_chunks,
                total_chunks=total_chunks,
                callback=callback,
            )
        return {"review": dict(review_result), "report": dict(report_result)}

    async with llm_session():
        return await run_with_model(get_pipeline_llm())


async def _load_review_coverage(
    job_id: UUID,
    *,
    missing_limit: int | None = 25,
) -> tuple[int, int, list[dict[str, object]]]:
    from app.ai.review.coverage import load_chunk_review_coverage

    return await load_chunk_review_coverage(job_id, missing_limit=missing_limit)


async def _build_report_context(
    *,
    job_id: UUID,
    postgres_session: AsyncSession,
) -> str:
    result = await postgres_session.execute(
        select(ReviewIssue).where(ReviewIssue.job_id == job_id)
    )
    issues = list(result.scalars().all())
    issues.sort(key=_persisted_issue_priority)
    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for issue in issues:
        severity_counts[issue.severity.value] = (
            severity_counts.get(issue.severity.value, 0) + 1
        )
        category_counts[issue.category.value] = (
            category_counts.get(issue.category.value, 0) + 1
        )
        source_counts[issue.source.value] = source_counts.get(issue.source.value, 0) + 1

    top_issues = [
        {
            "severity": issue.severity.value,
            "category": issue.category.value,
            "source": issue.source.value,
            "title": issue.title,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
        }
        for issue in issues[:20]
    ]
    return json.dumps(
        {
            "total_issues": len(issues),
            "severity_counts": severity_counts,
            "category_counts": category_counts,
            "source_counts": source_counts,
            "top_issues": top_issues,
        },
        ensure_ascii=False,
    )


def _persisted_issue_priority(issue: ReviewIssue) -> tuple[int, int]:
    raw_output = issue.raw_output or {}
    if raw_output.get("priority") == "P0":
        category_priority = 0
    else:
        category_priority = {
            "security": 1,
            "bug": 2,
            "performance": 3,
            "maintainability": 4,
            "requirement": 5,
            "style": 6,
        }.get(issue.category.value, 7)

    severity_priority = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "info": 4,
    }[issue.severity.value]
    return category_priority, severity_priority
