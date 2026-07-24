"""Hybrid evidence retrieval service for semantic audit probes."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.probe.bundle_selection import _full_audit_bundles, _trim_bundles
from app.ai.probe.candidate_service import (
    ProbeCandidateService,
    ProbeRetrievalCorpus,
)
from app.ai.probe.contracts import ProbeDefinition, ProbeLane
from app.ai.probe.models import (
    ProbeEvidenceBundle,
    SyntheticTraceWriter,
)
from app.ai.probe.retrieval_data import (
    _build_bm25_index,
    _index_generation_key_from_documents,
    _load_chunk_documents,
    _repo_branch_key_from_documents,
)
from app.ai.probe.retrieval_tracing import _write_probe_retrieval_trace
from app.ai.rag.code_retriever import CodeSemanticRetriever

logger = logging.getLogger(__name__)

SMART_LANE_CALL_BUDGET_WITH_ROADMAP = {
    ProbeLane.DEFECT: 4,
    ProbeLane.COVERAGE: 1,
    ProbeLane.ROADMAP: 4,
}
SMART_LANE_CALL_BUDGET_WITHOUT_ROADMAP = {
    ProbeLane.DEFECT: 8,
    ProbeLane.COVERAGE: 1,
    ProbeLane.ROADMAP: 0,
}


class ProbeRetrievalService:
    """Retrieve source evidence for every category probe without LLM calls."""

    def __init__(
        self,
        *,
        database: AsyncIOMotorDatabase,
        code_retriever: CodeSemanticRetriever | None = None,
        enable_semantic_search: bool = True,
        max_chunks: int = 188,
        defect_max_chunks: int = 120,
        coverage_max_chunks: int = 24,
        roadmap_max_chunks: int = 44,
        max_probes_per_batch: int = 8,
        max_chunks_per_batch: int = 24,
        full_audit: bool = False,
    ) -> None:
        self.database = database
        self.candidate_service = ProbeCandidateService(
            code_retriever=code_retriever,
            enable_semantic_search=enable_semantic_search,
        )
        self.max_chunks = max(1, max_chunks)
        self.lane_max_chunks = {
            ProbeLane.DEFECT: max(1, defect_max_chunks),
            ProbeLane.COVERAGE: max(1, coverage_max_chunks),
            ProbeLane.ROADMAP: max(1, roadmap_max_chunks),
        }
        self.max_probes_per_batch = max(1, max_probes_per_batch)
        self.max_chunks_per_batch = max(1, max_chunks_per_batch)
        self.full_audit = full_audit

    async def retrieve(
        self,
        *,
        job_id: UUID,
        probes: list[ProbeDefinition],
        trace_writer: SyntheticTraceWriter,
    ) -> list[ProbeEvidenceBundle]:
        """Run hybrid retrieval for every probe and write redacted traces."""

        corpus = await self._prepare_corpus(job_id=job_id, probes=probes)
        bundles, durations_by_probe = await self._retrieve_lanes(
            job_id=job_id,
            probes=probes,
            corpus=corpus,
        )
        trimmed_bundles = self._finalize_bundles(bundles, corpus.chunk_documents)
        await self._write_retrieval_traces(
            trace_writer=trace_writer,
            bundles=trimmed_bundles,
            durations_by_probe=durations_by_probe,
        )
        return trimmed_bundles

    async def _prepare_corpus(
        self,
        *,
        job_id: UUID,
        probes: list[ProbeDefinition],
    ) -> ProbeRetrievalCorpus:
        chunk_documents = await _load_chunk_documents(self.database, job_id)
        repo_branch_key = _repo_branch_key_from_documents(chunk_documents)
        index_generation_key = _index_generation_key_from_documents(chunk_documents)
        semantic_by_query = await self.candidate_service.semantic_results_for_probes(
            job_id=job_id,
            repo_branch_key=repo_branch_key,
            index_generation_key=index_generation_key,
            probes=probes,
        )
        return ProbeRetrievalCorpus(
            chunk_documents=chunk_documents,
            repo_branch_key=repo_branch_key,
            index_generation_key=index_generation_key,
            bm25_index=_build_bm25_index(chunk_documents),
            semantic_by_query=semantic_by_query,
        )

    async def _retrieve_lanes(
        self,
        *,
        job_id: UUID,
        probes: list[ProbeDefinition],
        corpus: ProbeRetrievalCorpus,
    ) -> tuple[list[ProbeEvidenceBundle], dict[str, int]]:
        bundles: list[ProbeEvidenceBundle] = []
        durations_by_probe: dict[str, int] = {}
        defect_evidence_keys: set[tuple[str, int]] = set()
        has_roadmap = any(probe.lane is ProbeLane.ROADMAP for probe in probes)
        for lane in ProbeLane:
            excluded_keys = (
                set(defect_evidence_keys) if lane is ProbeLane.COVERAGE else set()
            )
            lane_bundles, lane_durations = await self._retrieve_lane(
                job_id=job_id,
                probes=probes,
                corpus=corpus,
                lane=lane,
                excluded_keys=excluded_keys,
            )
            durations_by_probe.update(lane_durations)
            trimmed_lane_bundles = self._trim_lane_bundles(
                lane_bundles=lane_bundles,
                lane=lane,
                has_roadmap=has_roadmap,
            )
            bundles.extend(trimmed_lane_bundles)
            if lane is ProbeLane.DEFECT:
                defect_evidence_keys.update(
                    chunk.key
                    for bundle in trimmed_lane_bundles
                    for chunk in bundle.candidate_chunks
                )

        return bundles, durations_by_probe

    async def _retrieve_lane(
        self,
        *,
        job_id: UUID,
        probes: list[ProbeDefinition],
        corpus: ProbeRetrievalCorpus,
        lane: ProbeLane,
        excluded_keys: set[tuple[str, int]],
    ) -> tuple[list[ProbeEvidenceBundle], dict[str, int]]:
        bundles: list[ProbeEvidenceBundle] = []
        durations: dict[str, int] = {}
        for probe_index, probe in enumerate(probes):
            if probe.lane is not lane:
                continue
            started_at = time.perf_counter()
            bundle = await self.candidate_service.retrieve_probe(
                job_id=job_id,
                probe=probe,
                semantic_results=self._probe_semantic_results(
                    corpus.semantic_by_query,
                    probe_index=probe_index,
                    query_count=len(probe.retrieval_queries),
                ),
                semantic_attempted=self._semantic_was_attempted(
                    corpus.semantic_by_query,
                    probe_index=probe_index,
                    query_count=len(probe.retrieval_queries),
                ),
                corpus=corpus,
                excluded_keys=excluded_keys,
            )
            bundles.append(bundle)
            durations[probe.probe_id] = int((time.perf_counter() - started_at) * 1000)
            if lane is ProbeLane.COVERAGE:
                excluded_keys.update(chunk.key for chunk in bundle.candidate_chunks)

        return bundles, durations

    @staticmethod
    def _probe_semantic_results(
        semantic_by_query: dict[tuple[int, int], list[Any]],
        *,
        probe_index: int,
        query_count: int,
    ) -> list[Any]:
        return [
            result
            for query_index in range(query_count)
            for result in semantic_by_query.get((probe_index, query_index), [])
        ]

    @staticmethod
    def _semantic_was_attempted(
        semantic_by_query: dict[tuple[int, int], list[Any]],
        *,
        probe_index: int,
        query_count: int,
    ) -> bool:
        return any(
            (probe_index, query_index) in semantic_by_query
            for query_index in range(query_count)
        )

    def _trim_lane_bundles(
        self,
        *,
        lane_bundles: list[ProbeEvidenceBundle],
        lane: ProbeLane,
        has_roadmap: bool,
    ) -> list[ProbeEvidenceBundle]:
        capped_bundles = self._apply_smart_per_probe_cap(
            bundles=lane_bundles,
            lane=lane,
            has_roadmap=has_roadmap,
        )
        trimmed_bundles = _trim_bundles(
            capped_bundles,
            max_chunks=self._lane_chunk_cap(lane=lane, has_roadmap=has_roadmap),
        )
        self._warn_if_lane_exceeds_judge_budget(
            bundles=trimmed_bundles,
            lane=lane,
            has_roadmap=has_roadmap,
        )
        return trimmed_bundles

    def _warn_if_lane_exceeds_judge_budget(
        self,
        *,
        bundles: list[ProbeEvidenceBundle],
        lane: ProbeLane,
        has_roadmap: bool,
    ) -> None:
        call_budget = self._lane_call_budget(lane=lane, has_roadmap=has_roadmap)
        probe_budget = call_budget * self.max_probes_per_batch
        nonempty_probe_count = sum(bool(bundle.candidate_chunks) for bundle in bundles)
        if self.full_audit or call_budget <= 0 or nonempty_probe_count <= probe_budget:
            return
        logger.warning(
            "Probe lane %s exceeds its smart judge probe budget: %s > %s",
            lane.value,
            nonempty_probe_count,
            probe_budget,
        )

    def _finalize_bundles(
        self,
        bundles: list[ProbeEvidenceBundle],
        chunk_documents: list[dict[str, Any]],
    ) -> list[ProbeEvidenceBundle]:
        if self.full_audit:
            return [
                *bundles,
                *_full_audit_bundles(
                    chunk_documents=chunk_documents,
                    existing_bundles=bundles,
                ),
            ]
        return _trim_bundles(bundles, max_chunks=self.max_chunks)

    @staticmethod
    async def _write_retrieval_traces(
        *,
        trace_writer: SyntheticTraceWriter,
        bundles: list[ProbeEvidenceBundle],
        durations_by_probe: dict[str, int],
    ) -> None:
        for bundle in bundles:
            await _write_probe_retrieval_trace(
                trace_writer=trace_writer,
                probe=bundle.probe,
                bundle=bundle,
                duration_ms=durations_by_probe.get(bundle.probe.probe_id, 0),
            )

    def _lane_chunk_cap(self, *, lane: ProbeLane, has_roadmap: bool) -> int:
        if self.full_audit:
            return self.lane_max_chunks[lane]
        call_budget = self._lane_call_budget(
            lane=lane,
            has_roadmap=has_roadmap,
        )
        if call_budget <= 0:
            return self.lane_max_chunks[lane]
        return min(
            self.lane_max_chunks[lane],
            call_budget * self.max_chunks_per_batch,
        )

    @staticmethod
    def _lane_call_budget(*, lane: ProbeLane, has_roadmap: bool) -> int:
        return (
            SMART_LANE_CALL_BUDGET_WITH_ROADMAP
            if has_roadmap
            else SMART_LANE_CALL_BUDGET_WITHOUT_ROADMAP
        )[lane]

    def _apply_smart_per_probe_cap(
        self,
        *,
        bundles: list[ProbeEvidenceBundle],
        lane: ProbeLane,
        has_roadmap: bool,
    ) -> list[ProbeEvidenceBundle]:
        if self.full_audit or not has_roadmap or lane is not ProbeLane.DEFECT:
            return bundles
        per_probe_cap = max(
            1,
            self.max_chunks_per_batch // self.max_probes_per_batch,
        )
        return [
            replace(
                bundle,
                candidate_chunks=bundle.candidate_chunks[:per_probe_cap],
                trimmed_count=bundle.trimmed_count
                + max(0, len(bundle.candidate_chunks) - per_probe_cap),
            )
            for bundle in bundles
        ]
