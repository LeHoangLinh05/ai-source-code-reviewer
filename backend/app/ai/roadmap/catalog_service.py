"""Deterministic roadmap catalog materialization for AI reviews."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.ai.roadmap.knowledge import RoadmapRequirement, load_roadmap_requirements
from app.ai.roadmap.selection import (
    RoadmapProfile,
    get_applicable_rule_ids,
    parse_roadmap_profile,
)
from app.ai.tools.call_logging import MongoToolCallLogger
from app.ai.tools.runtime import get_ai_tool_runtime
from app.db.mongodb import TOOL_CALL_LOGS_COLLECTION
from app.models.review_job import ReviewJob

ROADMAP_RULE_CATALOG_TOOL_NAME = "roadmap_rule_catalog"


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
