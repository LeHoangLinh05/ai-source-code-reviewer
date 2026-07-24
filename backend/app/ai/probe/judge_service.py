"""Evidence judging and accepted probe candidate persistence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.probe.candidate_validation import (
    _candidate_has_required_fields,
    _candidate_references,
    _candidate_rule_id,
    _dependency_manifest_contradicts_candidate,
    _source_context,
    _supporting_bundle_chunks,
)
from app.ai.probe.judging import (
    _issue_category,
    _issue_severity,
    _json_object_from_text,
    _judge_batches,
    _judge_prompt,
    _message_content,
    _probe_judge_response_from_payload,
)
from app.ai.probe.models import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssueCandidate,
    ProbeJudgeResponse,
    SyntheticTraceWriter,
    _probe_id,
)
from app.ai.roadmap.knowledge import load_roadmap_requirements
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)

PROBE_JUDGE_TOOL_NAME = "probe_judge"
MIN_PROBE_ISSUE_CONFIDENCE = 0.7


@dataclass(slots=True, frozen=True)
class _CandidatePersistenceContext:
    file_path: str
    line_start: int
    line_end: int
    evidence_chunk: ProbeCandidateChunk
    category: IssueCategory
    severity: IssueSeverity
    rule_id: str | None


class ProbeJudgeService:
    """Ask the LLM to judge backend-selected evidence batches."""

    def __init__(
        self,
        *,
        llm: Any,
        postgres_session: AsyncSession,
        max_probes_per_batch: int = 8,
        max_chunks_per_batch: int = 24,
        min_confidence: float = MIN_PROBE_ISSUE_CONFIDENCE,
    ) -> None:
        self.llm = llm
        self.postgres_session = postgres_session
        self.max_probes_per_batch = max(1, max_probes_per_batch)
        self.max_chunks_per_batch = max(1, max_chunks_per_batch)
        self.min_confidence = min_confidence
        self.roadmap_by_id = {
            requirement.rule_id: requirement
            for requirement in load_roadmap_requirements()
        }

    async def judge_and_persist(
        self,
        *,
        job_id: UUID,
        bundles: list[ProbeEvidenceBundle],
        trace_writer: SyntheticTraceWriter,
    ) -> tuple[int, int, int]:
        """Judge evidence batches and persist accepted issue candidates."""

        created_count = 0
        rejected_count = 0
        judged_batches = 0
        for batch in _judge_batches(
            bundles,
            max_probes=self.max_probes_per_batch,
            max_chunks=self.max_chunks_per_batch,
        ):
            started_at = time.perf_counter()
            response = await self._judge_batch(batch)
            batch_created, batch_rejected = await self._persist_batch(
                job_id=job_id,
                candidates=response.candidates,
                bundles=batch,
            )
            judged_batches += 1
            created_count += batch_created
            rejected_count += batch_rejected
            await _write_probe_judge_trace(
                trace_writer=trace_writer,
                batch=batch,
                response=response,
                created_count=batch_created,
                rejected_count=batch_rejected,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )

        return judged_batches, created_count, rejected_count

    async def _persist_batch(
        self,
        *,
        job_id: UUID,
        candidates: list[ProbeJudgeIssueCandidate],
        bundles: list[ProbeEvidenceBundle],
    ) -> tuple[int, int]:
        created_count = 0
        for candidate in candidates:
            created_count += await self._persist_candidate(
                job_id=job_id,
                candidate=candidate,
                bundles=bundles,
            )
        return created_count, len(candidates) - created_count

    async def _judge_batch(
        self,
        batch: list[ProbeEvidenceBundle],
    ) -> ProbeJudgeResponse:
        result = await self.llm.ainvoke(
            [
                (
                    "system",
                    "You are an evidence-only senior code review judge. "
                    "Judge only from the provided source chunks. Return JSON only.",
                ),
                ("user", _judge_prompt(batch)),
            ]
        )
        payload = _json_object_from_text(_message_content(result))
        return _probe_judge_response_from_payload(payload)

    async def _persist_candidate(
        self,
        *,
        job_id: UUID,
        candidate: ProbeJudgeIssueCandidate,
        bundles: list[ProbeEvidenceBundle],
    ) -> bool:
        context = await self._candidate_persistence_context(
            job_id=job_id,
            candidate=candidate,
            bundles=bundles,
        )
        if context is None:
            return False
        self.postgres_session.add(
            self._build_review_issue(
                job_id=job_id,
                candidate=candidate,
                context=context,
            )
        )
        await self.postgres_session.commit()
        return True

    async def _candidate_persistence_context(
        self,
        *,
        job_id: UUID,
        candidate: ProbeJudgeIssueCandidate,
        bundles: list[ProbeEvidenceBundle],
    ) -> _CandidatePersistenceContext | None:
        if not self._candidate_is_eligible(candidate):
            return None
        location = self._candidate_location(candidate)
        evidence_chunks = _supporting_bundle_chunks(candidate, bundles)
        if location is None or not evidence_chunks:
            return None
        file_path, line_start, line_end = location
        category = _issue_category(candidate.category)
        if await self._find_existing_issue(
            job_id=job_id,
            file_path=file_path,
            line_start=line_start,
            category=category,
        ):
            return None
        rule_id = _candidate_rule_id(
            candidate,
            bundles,
            roadmap_by_id=self.roadmap_by_id,
        )
        if self._evidence_contradicts_candidate(candidate, rule_id, evidence_chunks):
            return None
        return _CandidatePersistenceContext(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            evidence_chunk=evidence_chunks[0],
            category=category,
            severity=_issue_severity(candidate.severity),
            rule_id=rule_id,
        )

    def _candidate_is_eligible(self, candidate: ProbeJudgeIssueCandidate) -> bool:
        return (
            candidate.verdict == "issue"
            and candidate.confidence is not None
            and candidate.confidence >= self.min_confidence
            and _candidate_has_required_fields(candidate)
        )

    @staticmethod
    def _candidate_location(
        candidate: ProbeJudgeIssueCandidate,
    ) -> tuple[str, int, int] | None:
        if (
            candidate.file_path is None
            or candidate.line_start is None
            or candidate.line_end is None
        ):
            return None
        return candidate.file_path, candidate.line_start, candidate.line_end

    def _evidence_contradicts_candidate(
        self,
        candidate: ProbeJudgeIssueCandidate,
        rule_id: str | None,
        evidence_chunks: list[ProbeCandidateChunk],
    ) -> bool:
        return any(
            _dependency_manifest_contradicts_candidate(
                candidate=candidate,
                rule=self.roadmap_by_id.get(rule_id or ""),
                evidence_chunk=evidence_chunk,
            )
            for evidence_chunk in evidence_chunks
        )

    def _build_review_issue(
        self,
        *,
        job_id: UUID,
        candidate: ProbeJudgeIssueCandidate,
        context: _CandidatePersistenceContext,
    ) -> ReviewIssue:
        references = _candidate_references(
            category=context.category,
            rule_id=context.rule_id,
            roadmap_by_id=self.roadmap_by_id,
        )
        return ReviewIssue(
            job_id=job_id,
            file_path=context.file_path,
            line_start=context.line_start,
            line_end=context.line_end,
            severity=context.severity,
            category=context.category,
            title=str(candidate.title or "AI review finding")[:255],
            description=str(candidate.description or ""),
            suggestion=candidate.suggestion,
            source=IssueSource.KB if context.rule_id else IssueSource.AI_REVIEW,
            confidence=candidate.confidence,
            raw_output={
                "references": references,
                "probe_review": {
                    "probe_id": candidate.probe_id,
                    "rule_id": context.rule_id,
                    "claim_type": candidate.claim_type,
                    "supporting_evidence": [
                        item.model_dump(mode="json")
                        for item in candidate.supporting_evidence
                    ],
                    "contradicting_evidence": [
                        item.model_dump(mode="json")
                        for item in candidate.contradicting_evidence
                    ],
                    "source_context": _source_context(context.evidence_chunk),
                },
            },
        )

    async def _find_existing_issue(
        self,
        *,
        job_id: UUID,
        file_path: str,
        line_start: int,
        category: IssueCategory,
    ) -> ReviewIssue | None:
        result = await self.postgres_session.execute(
            select(ReviewIssue)
            .where(
                ReviewIssue.job_id == job_id,
                ReviewIssue.file_path == file_path,
                ReviewIssue.line_start == line_start,
                ReviewIssue.category == category,
            )
            .order_by(ReviewIssue.created_at.asc())
        )
        return result.scalars().first()


async def _write_probe_judge_trace(
    *,
    trace_writer: SyntheticTraceWriter,
    batch: list[ProbeEvidenceBundle],
    response: ProbeJudgeResponse,
    created_count: int,
    rejected_count: int,
    duration_ms: int,
) -> None:
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_JUDGE_TOOL_NAME,
        tool_input={
            "probe_ids": [_probe_id(bundle.probe) for bundle in batch],
            "probe_count": len(batch),
            "chunk_count": sum(len(bundle.candidate_chunks) for bundle in batch),
        },
        output={
            "status": "ok",
            "duration_ms": duration_ms,
            "candidate_count": len(response.candidates),
            "schema_rejected_count": response.schema_rejected_count,
            "created_count": created_count,
            "rejected_count": rejected_count,
            "candidates": [
                candidate.model_dump(
                    mode="json",
                    exclude={"description", "suggestion"},
                )
                for candidate in response.candidates
            ],
        },
    )
