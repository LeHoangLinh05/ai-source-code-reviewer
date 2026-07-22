"""Main AI agent orchestration for repository review."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from langchain_core.callbacks import AsyncCallbackHandler
except ImportError:

    class AsyncCallbackHandler:  # type: ignore[no-redef]
        """Fallback base class when LangChain is not installed at app startup."""


from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.ai.final_report import synthesize_final_report
from app.ai.probe_review import run_backend_directed_probe_review
from app.ai.roadmap.knowledge import RoadmapRequirement, load_roadmap_requirements
from app.ai.roadmap.selection import (
    RoadmapProfile,
    get_applicable_rule_ids,
    parse_roadmap_profile,
)
from app.ai.tool_runtime import (
    AIToolRuntime,
    ai_tool_runtime,
    get_ai_tool_runtime,
)
from app.db.mongodb import TOOL_CALL_LOGS_COLLECTION
from app.models.review_issue import ReviewIssue
from app.models.review_job import ReviewJob
from app.repositories.mongodb_repository import ToolCallLogRepository
from app.schemas.mongodb import ToolCallLogDocument

logger = logging.getLogger(__name__)

PROBE_REVIEW_COVERAGE_MISSING_CHUNK_LIMIT = 60
ROADMAP_RULE_CATALOG_TOOL_NAME = "roadmap_rule_catalog"


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

    from app.ai.llm_config import get_pipeline_llm, llm_session
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
                enable_semantic_search=True,
                code_retriever=runtime.code_retriever,
                chunks_per_probe=settings.probe_retrieval_chunks_per_probe,
                max_chunks=settings.probe_retrieval_max_chunks,
                max_probes_per_batch=settings.probe_judge_max_probes_per_batch,
                max_chunks_per_batch=settings.probe_judge_max_chunks_per_batch,
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


async def _ensure_roadmap_catalog_loaded(
    *,
    job_id: UUID,
    callback: MongoToolCallLogger,
) -> None:
    roadmap_required, roadmap_catalog_loaded = await _load_roadmap_catalog_state(job_id)
    if roadmap_required and not roadmap_catalog_loaded:
        await _materialize_roadmap_rule_catalog(
            job_id=job_id,
            callback=callback,
        )


async def _load_roadmap_catalog_state(job_id: UUID) -> tuple[bool, bool]:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    profile = parse_roadmap_profile(options if isinstance(options, dict) else None)
    if profile is None:
        return False, False

    applicable_rule_ids = set(get_applicable_rule_ids(profile))
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(job_id),
                "agent_type": "review",
                "tool_name": ROADMAP_RULE_CATALOG_TOOL_NAME,
                "output.status": "ok",
            }
        )
        .to_list(length=None)
    )
    loaded_rule_ids = _loaded_roadmap_rule_ids(documents)
    return True, applicable_rule_ids.issubset(loaded_rule_ids)


async def _materialize_roadmap_rule_catalog(
    *,
    job_id: UUID,
    callback: MongoToolCallLogger,
) -> None:
    """Record deterministic roadmap rule catalog outside LLM call budget."""

    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    profile = parse_roadmap_profile(options if isinstance(options, dict) else None)
    if profile is None:
        return

    requirements = _roadmap_requirements_for_profile(profile)
    if not requirements:
        return

    existing_documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(job_id),
                "agent_type": "review",
                "tool_name": ROADMAP_RULE_CATALOG_TOOL_NAME,
                "output.status": "ok",
            }
        )
        .to_list(length=None)
    )
    loaded_rule_ids = _loaded_roadmap_rule_ids(existing_documents)
    missing_requirements = [
        requirement
        for requirement in requirements
        if requirement.rule_id not in loaded_rule_ids
    ]
    if not missing_requirements:
        return

    await callback.write_synthetic_tool_log(
        tool_name=ROADMAP_RULE_CATALOG_TOOL_NAME,
        tool_input={
            "job_id": str(job_id),
            "profile_id": profile.profile_id,
            "rule_count": len(missing_requirements),
            "reason": "deterministic_roadmap_rule_catalog_load",
        },
        output={
            "status": "ok",
            "summary": (
                "Loaded roadmap rule catalog before the LLM judge. "
                "This is not a code-review verdict; source-code verification "
                "still requires AI source evidence."
            ),
            "result_count": len(missing_requirements),
            "results": [
                _roadmap_requirement_tool_result(requirement)
                for requirement in missing_requirements
            ],
        },
    )


def _roadmap_requirements_for_profile(
    profile: RoadmapProfile,
) -> list[RoadmapRequirement]:
    selected_weeks = (
        set(profile.weeks_included) if profile.weeks_included is not None else None
    )
    return [
        requirement
        for requirement in load_roadmap_requirements()
        if requirement.week == "GEN"
        or selected_weeks is None
        or requirement.week in selected_weeks
    ]


def _roadmap_requirement_tool_result(
    requirement: RoadmapRequirement,
) -> dict[str, object]:
    document = requirement.to_rag_document()
    metadata = {
        "source": document.source,
        "chunk_index": 0,
        "language": document.language,
        "doc_type": document.doc_type,
        "category": document.category,
        **document.extra_metadata,
    }
    return {
        "source": document.source,
        "content": document.content,
        "metadata": metadata,
        "vector_score": 0.0,
        "bm25_score": 1.0,
        "final_score": 1.0,
        "retrieval_mode": "deterministic_profile_catalog",
    }


def _loaded_roadmap_rule_ids(documents: list[object]) -> set[str]:
    """Return roadmap rule ids loaded from successful KB/catalog traces."""

    loaded_rule_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        output = document.get("output")
        if not isinstance(output, dict):
            continue
        results = output.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            metadata = result.get("metadata")
            if not isinstance(metadata, dict):
                continue
            rule_id = metadata.get("rule_id")
            if metadata.get("doc_type") == "roadmap_rule" and isinstance(rule_id, str):
                loaded_rule_ids.add(rule_id)

    return loaded_rule_ids


async def _load_review_coverage(
    job_id: UUID,
    *,
    missing_limit: int | None = 25,
) -> tuple[int, int, list[dict[str, object]]]:
    from app.ai.review_coverage import load_chunk_review_coverage

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
