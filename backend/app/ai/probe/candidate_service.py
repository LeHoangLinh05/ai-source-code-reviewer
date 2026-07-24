"""Collect and rank source candidates for individual semantic audit probes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.ai.probe.candidate_retrieval import (
    _candidate_from_document,
    _candidate_from_semantic_result,
    _exact_candidates,
    _expand_related_candidates,
    _fuse_probe_candidates,
    _unique_candidates,
)
from app.ai.probe.contracts import ProbeDefinition
from app.ai.probe.models import ProbeCandidateChunk, ProbeEvidenceBundle
from app.ai.probe.retrieval_data import _semantic_search, _semantic_search_many
from app.ai.probe.structural_matching import (
    _candidates_in_file,
    _exclude_candidates,
    _file_scope_candidates,
    _structural_candidates,
)
from app.ai.rag.bm25_index import BM25Index
from app.ai.rag.code_retriever import CodeSemanticRetriever, CodeSemanticSearchRequest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProbeRetrievalCorpus:
    """Indexes and source metadata shared across probe retrieval calls."""

    chunk_documents: list[dict[str, Any]]
    repo_branch_key: str | None
    index_generation_key: str | None
    bm25_index: BM25Index
    semantic_by_query: dict[tuple[int, int], list[Any]]


@dataclass(slots=True)
class _ProbeCandidateGroups:
    semantic: list[ProbeCandidateChunk]
    bm25: list[ProbeCandidateChunk]
    exact: list[ProbeCandidateChunk]
    structural: list[ProbeCandidateChunk]
    strategies: list[str]


class ProbeCandidateService:
    """Retrieve and fuse candidate chunks for one probe at a time."""

    def __init__(
        self,
        *,
        code_retriever: CodeSemanticRetriever | None,
        enable_semantic_search: bool,
    ) -> None:
        self.code_retriever = code_retriever
        self.enable_semantic_search = enable_semantic_search

    async def semantic_results_for_probes(
        self,
        *,
        job_id: UUID,
        repo_branch_key: str | None,
        index_generation_key: str | None,
        probes: list[ProbeDefinition],
    ) -> dict[tuple[int, int], list[Any]]:
        """Batch semantic queries so all probes share one retriever call."""

        if not self.enable_semantic_search:
            return {}

        requests: list[CodeSemanticSearchRequest] = []
        request_indexes: list[tuple[int, int]] = []
        for probe_index, probe in enumerate(probes):
            for query_index, query in enumerate(probe.retrieval_queries):
                normalized_query = query.strip()
                if not normalized_query:
                    continue
                requests.append(
                    CodeSemanticSearchRequest(
                        query=normalized_query,
                        job_id=job_id,
                        repo_branch_key=repo_branch_key,
                        index_generation_key=index_generation_key,
                        top_k=10,
                    )
                )
                request_indexes.append((probe_index, query_index))

        if not requests:
            return {}

        try:
            results_by_request = await _semantic_search_many(
                retriever=self._get_code_retriever(),
                requests=requests,
            )
        except Exception as error:
            logger.warning("Probe semantic retrieval batch failed: %s", error)
            return {request_index: [] for request_index in request_indexes}

        return {
            request_index: results
            for request_index, results in zip(
                request_indexes,
                results_by_request,
                strict=True,
            )
        }

    async def retrieve_probe(
        self,
        *,
        job_id: UUID,
        probe: ProbeDefinition,
        semantic_results: list[Any] | None,
        semantic_attempted: bool,
        corpus: ProbeRetrievalCorpus,
        excluded_keys: set[tuple[str, int]],
    ) -> ProbeEvidenceBundle:
        """Collect, filter, rank and expand candidates for one probe."""

        query = " ".join(probe.retrieval_queries)
        top_k = min(max(probe.top_k, 1), 10)
        candidates = await self._collect_probe_candidates(
            job_id=job_id,
            probe=probe,
            query=query,
            semantic_results=semantic_results,
            semantic_attempted=semantic_attempted,
            corpus=corpus,
        )
        candidates = self._filter_probe_candidates(
            candidates,
            file_scope=probe.file_scope,
            excluded_keys=excluded_keys,
        )
        selected = _fuse_probe_candidates(
            semantic_candidates=candidates.semantic,
            bm25_candidates=candidates.bm25,
            exact_candidates=candidates.exact,
            structural_candidates=candidates.structural,
            probe=probe,
            query=query,
            top_k=top_k,
        )
        selected = _expand_related_candidates(
            selected=selected,
            chunk_documents=corpus.chunk_documents,
            probe=probe,
            top_k=top_k,
            excluded_keys=excluded_keys,
        )
        return ProbeEvidenceBundle(
            probe=probe,
            retrieval_status="ok" if selected else "no_candidate_evidence",
            candidate_chunks=selected,
            strategies_used=candidates.strategies,
            strategy_candidate_counts={
                "semantic": len(_unique_candidates(candidates.semantic)),
                "bm25": len(_unique_candidates(candidates.bm25)),
                "exact": len(_unique_candidates(candidates.exact)),
                "structural": len(_unique_candidates(candidates.structural)),
            },
            selected_count_before_trim=len(selected),
        )

    async def _collect_probe_candidates(
        self,
        *,
        job_id: UUID,
        probe: ProbeDefinition,
        query: str,
        semantic_results: list[Any] | None,
        semantic_attempted: bool,
        corpus: ProbeRetrievalCorpus,
    ) -> _ProbeCandidateGroups:
        semantic, semantic_strategy = await self._semantic_candidates(
            job_id=job_id,
            probe=probe,
            query=query,
            semantic_results=semantic_results,
            semantic_attempted=semantic_attempted,
            corpus=corpus,
        )
        structural = _structural_candidates(
            chunk_documents=corpus.chunk_documents,
            probe=probe,
        )
        file_candidates = _file_scope_candidates(
            chunk_documents=corpus.chunk_documents,
            probe=probe,
        )
        strategies = [semantic_strategy] if semantic_strategy else []
        strategies.extend(["bm25", "exact", "structural"])
        if file_candidates:
            structural = [*file_candidates, *structural]
            strategies.append("file_scope")
        return _ProbeCandidateGroups(
            semantic=semantic,
            bm25=self._bm25_candidates(corpus.bm25_index, probe),
            exact=_exact_candidates(
                chunk_documents=corpus.chunk_documents,
                probe=probe,
                query=query,
                top_k=len(corpus.chunk_documents),
            ),
            structural=structural,
            strategies=strategies,
        )

    async def _semantic_candidates(
        self,
        *,
        job_id: UUID,
        probe: ProbeDefinition,
        query: str,
        semantic_results: list[Any] | None,
        semantic_attempted: bool,
        corpus: ProbeRetrievalCorpus,
    ) -> tuple[list[ProbeCandidateChunk], str | None]:
        results = semantic_results or []
        strategy = "semantic" if semantic_attempted else None
        if query and self.enable_semantic_search and not semantic_attempted:
            try:
                results = await _semantic_search(
                    retriever=self._get_code_retriever(),
                    job_id=job_id,
                    repo_branch_key=corpus.repo_branch_key,
                    index_generation_key=corpus.index_generation_key,
                    query=probe.primary_query,
                    top_k=10,
                )
                strategy = "semantic"
            except Exception as error:
                logger.warning(
                    "Probe semantic retrieval failed; using exact only: %s", error
                )
                strategy = "semantic_error"

        candidates = [
            chunk
            for result in results
            if (
                chunk := _candidate_from_semantic_result(
                    result,
                    probe=probe,
                    query=query,
                )
            )
            is not None
        ]
        return candidates, strategy

    @staticmethod
    def _bm25_candidates(
        bm25_index: BM25Index,
        probe: ProbeDefinition,
    ) -> list[ProbeCandidateChunk]:
        candidates: list[ProbeCandidateChunk] = []
        for retrieval_query in probe.retrieval_queries:
            for result in bm25_index.search(retrieval_query, top_k=50):
                chunk = _candidate_from_document(
                    result.metadata,
                    probe=probe,
                    query=retrieval_query,
                    semantic_score=0.0,
                    lexical_score=result.score,
                    strategy="bm25",
                )
                if chunk is not None:
                    candidates.append(chunk)
        return candidates

    @staticmethod
    def _filter_probe_candidates(
        candidates: _ProbeCandidateGroups,
        *,
        file_scope: str | None,
        excluded_keys: set[tuple[str, int]],
    ) -> _ProbeCandidateGroups:
        def filtered(group: list[ProbeCandidateChunk]) -> list[ProbeCandidateChunk]:
            scoped = _candidates_in_file(group, file_scope) if file_scope else group
            return _exclude_candidates(scoped, excluded_keys)

        return _ProbeCandidateGroups(
            semantic=filtered(candidates.semantic),
            bm25=filtered(candidates.bm25),
            exact=filtered(candidates.exact),
            structural=filtered(candidates.structural),
            strategies=candidates.strategies,
        )

    def _get_code_retriever(self) -> CodeSemanticRetriever:
        if self.code_retriever is None:
            self.code_retriever = CodeSemanticRetriever()

        return self.code_retriever
