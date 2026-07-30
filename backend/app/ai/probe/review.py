"""Backend-directed probe retrieval and evidence-only issue judging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.probe.contracts import ProbeDefinition
from app.ai.probe.judge_service import ProbeJudgeService
from app.ai.probe.models import (
    ProbeBatchProgressCallback,
    ProbeEvidenceBundle,
    ProbeReviewConfig,
    ProbeReviewResult,
    SyntheticTraceWriter,
)
from app.ai.probe.plan import build_semantic_audit_plan
from app.ai.probe.retrieval_data import _load_chunk_documents
from app.ai.probe.retrieval_service import ProbeRetrievalService
from app.ai.rag.code_retriever import CodeSemanticRetriever
from app.ai.review.plan import REVIEW_MODE_FULL_AUDIT, get_review_mode
from app.ai.roadmap.knowledge import RoadmapRequirement, load_roadmap_requirements
from app.ai.roadmap.selection import build_roadmap_context
from app.db.mongodb import FILE_ANALYSIS_RESULTS_COLLECTION
from app.models.review_job import ReviewJob

logger = logging.getLogger(__name__)


async def run_backend_directed_probe_review(
    *,
    job_id: UUID,
    llm: Any,
    postgres_session: AsyncSession,
    mongodb_database: AsyncIOMotorDatabase,
    trace_writer: SyntheticTraceWriter,
    config: ProbeReviewConfig,
    code_retriever: CodeSemanticRetriever | None = None,
    on_batch_completed: ProbeBatchProgressCallback | None = None,
) -> ProbeReviewResult:
    """Run the default backend-directed review path."""

    job_options = await _load_job_options(postgres_session, job_id)
    probes = await build_backend_probe_plan(
        job_id=job_id,
        database=mongodb_database,
        job_options=job_options,
    )
    retrieval_service = ProbeRetrievalService(
        database=mongodb_database,
        code_retriever=code_retriever,
        enable_semantic_search=config.enable_semantic_search,
        max_chunks=config.max_chunks,
        defect_max_chunks=config.defect_max_chunks,
        coverage_max_chunks=config.coverage_max_chunks,
        roadmap_max_chunks=config.roadmap_max_chunks,
        max_probes_per_batch=config.max_probes_per_batch,
        max_chunks_per_batch=config.max_chunks_per_batch,
        full_audit=get_review_mode(job_options) == REVIEW_MODE_FULL_AUDIT,
    )
    bundles = await retrieval_service.retrieve(
        job_id=job_id,
        probes=probes,
        trace_writer=trace_writer,
    )
    judge_service = ProbeJudgeService(
        llm=llm,
        postgres_session=postgres_session,
        max_probes_per_batch=config.max_probes_per_batch,
        max_chunks_per_batch=config.max_chunks_per_batch,
        max_concurrency=config.max_concurrency,
    )
    judge_counts = await judge_service.judge_and_persist(
        job_id=job_id,
        bundles=bundles,
        trace_writer=trace_writer,
        on_batch_completed=on_batch_completed,
    )
    return _build_probe_review_result(probes, bundles, judge_counts)


def _build_probe_review_result(
    probes: list[ProbeDefinition],
    bundles: list[ProbeEvidenceBundle],
    judge_counts: tuple[int, int, int],
) -> ProbeReviewResult:
    judged_batches, created_issues, rejected_candidates = judge_counts
    retrieved_probes = sum(1 for bundle in bundles if bundle.candidate_chunks)
    no_evidence_probes = len(bundles) - retrieved_probes
    return ProbeReviewResult(
        total_probes=len(probes),
        retrieved_probes=retrieved_probes,
        no_evidence_probes=no_evidence_probes,
        judged_batches=judged_batches,
        created_issues=created_issues,
        rejected_candidates=rejected_candidates,
        handoff=(
            "Backend-directed probe review completed: "
            f"{len(probes)} probes, {retrieved_probes} with evidence, "
            f"{no_evidence_probes} without candidate evidence, "
            f"{judged_batches} judge batches, {created_issues} issues created, "
            f"{rejected_candidates} candidates rejected."
        ),
    )


async def build_backend_probe_plan(
    *,
    job_id: UUID,
    database: AsyncIOMotorDatabase,
    job_options: dict[str, object] | None,
) -> list[ProbeDefinition]:
    """Build the same unified category probe plan without a tool loop."""

    structure_document = await database[FILE_ANALYSIS_RESULTS_COLLECTION].find_one(
        {"job_id": str(job_id)},
        sort=[("analyzed_at", -1)],
    )
    chunk_documents = await _load_chunk_documents(database, job_id)
    roadmap_context = _build_roadmap_context_without_vectorstore(job_options)
    file_tree = []
    if isinstance(structure_document, dict):
        file_tree = _as_list(structure_document.get("file_tree"))
    return build_semantic_audit_plan(
        roadmap_context=roadmap_context,
        files_to_review=_files_to_review(
            file_tree=file_tree,
            chunk_counts=_chunk_counts_by_file(chunk_documents),
            chunk_risks=_chunk_risks_by_file(chunk_documents),
        ),
        static_issues=[],
    )


async def _load_job_options(
    postgres_session: AsyncSession,
    job_id: UUID,
) -> dict[str, object] | None:
    result = await postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    return options if isinstance(options, dict) else None


def _build_roadmap_context_without_vectorstore(
    options: dict[str, object] | None,
) -> dict[str, object] | None:
    try:
        return build_roadmap_context(options, vectorstore=_RoadmapMetadataStore())
    except ValueError as error:
        logger.warning("Roadmap context unavailable for probe review: %s", error)
        return None


class _RoadmapMetadataStore:
    """Metadata-only store backed by the generated roadmap YAML."""

    def all_documents(self) -> list[Any]:
        return [
            _RoadmapMetadataDocument(requirement)
            for requirement in load_roadmap_requirements()
        ]


@dataclass(slots=True, frozen=True)
class _RoadmapMetadataDocument:
    requirement: RoadmapRequirement

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "doc_type": "roadmap_rule",
            "profile_id": "roadmap_bootcamp_v1",
            "rule_id": self.requirement.rule_id,
            "week": self.requirement.week,
            "priority": self.requirement.priority,
        }


def _files_to_review(
    *,
    file_tree: list[object],
    chunk_counts: dict[str, int],
    chunk_risks: dict[str, set[str]],
) -> list[dict[str, object]]:
    file_paths = {
        str(entry["path"])
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    }
    file_paths.update(chunk_counts)
    return [
        {
            "file_path": file_path,
            "priority": _file_priority(file_path, chunk_risks.get(file_path, set())),
            "risk_area": _file_risk_area(file_path, chunk_risks.get(file_path, set())),
            "total_chunks": chunk_counts.get(file_path, 0),
        }
        for file_path in sorted(file_paths)
    ]


def _chunk_counts_by_file(chunk_documents: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for document in chunk_documents:
        file_path = document.get("file_path")
        total_chunks = document.get("total_chunks")
        chunk_index = document.get("chunk_index")
        if not isinstance(file_path, str):
            continue
        if isinstance(total_chunks, int):
            counts[file_path] = max(counts.get(file_path, 0), total_chunks)
        elif isinstance(chunk_index, int):
            counts[file_path] = max(counts.get(file_path, 0), chunk_index + 1)
    return counts


def _chunk_risks_by_file(
    chunk_documents: list[dict[str, Any]],
) -> dict[str, set[str]]:
    risks: dict[str, set[str]] = {}
    for document in chunk_documents:
        file_path = document.get("file_path")
        risk_area = document.get("risk_area")
        if isinstance(file_path, str) and isinstance(risk_area, str):
            risks.setdefault(file_path, set()).add(risk_area)
    return risks


def _file_priority(file_path: str, risk_areas: set[str]) -> str:
    if risk_areas & {"security", "api"}:
        return "high"
    if _path_category(file_path) == "security":
        return "high"
    if risk_areas & {"database", "config"}:
        return "medium"
    return "low"


def _file_risk_area(file_path: str, risk_areas: set[str]) -> str:
    if "security" in risk_areas or _path_category(file_path) == "security":
        return "security"
    if "database" in risk_areas:
        return "database"
    if "api" in risk_areas:
        return "api"
    if "config" in risk_areas:
        return "config"
    return "general"


def _path_category(file_path: str) -> str:
    path = PurePosixPath(file_path.replace("\\", "/"))
    parts = {part.lower() for part in path.parts}
    parts.add(path.stem.lower())
    if parts & {"auth", "security", "crypto", "middleware"}:
        return "security"
    return "general"


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
