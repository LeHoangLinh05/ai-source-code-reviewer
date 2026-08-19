"""Evidence judging and accepted probe candidate persistence."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

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
    _json_object_from_text,
    _judge_batches,
    _judge_prompt,
    _message_content,
    _probe_issue_severity,
    _probe_judge_response_from_payload,
)
from app.ai.probe.models import (
    ProbeBatchProgressCallback,
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssueCandidate,
    ProbeJudgeResponse,
    ProbeJudgeResult,
    ProbeJudgeSummary,
    SyntheticTraceWriter,
    _probe_id,
)
from app.ai.roadmap.knowledge import load_roadmap_requirements
from app.core.review_targets import is_review_target_path
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.repositories.report_repository import ReportRepository
from app.services.reporting.finding_identity import (
    CLAIM_TYPE_FIELD,
    FINDING_KEY_FIELD,
    build_finding_key,
    canonical_probe_claim_type,
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
    finding_key: str
    claim_type: str


@dataclass(slots=True, frozen=True)
class _ProbeVerdictContract:
    requested_probe_ids: tuple[str, ...]
    received_probe_ids: tuple[str, ...]
    missing_probe_ids: tuple[str, ...]
    duplicate_probe_ids: tuple[str, ...]
    unknown_probe_ids: tuple[str, ...]
    schema_rejected_count: int
    has_unscoped_schema_error: bool

    @property
    def is_valid(self) -> bool:
        return not (
            self.missing_probe_ids
            or self.duplicate_probe_ids
            or self.unknown_probe_ids
            or self.has_unscoped_schema_error
        )


@dataclass(slots=True, frozen=True)
class _JudgeBatchOutcome:
    response: ProbeJudgeResponse
    contract_attempts: tuple[_ProbeVerdictContract, ...]
    duration_ms: int


@dataclass(slots=True, frozen=True)
class _BatchPersistenceSummary:
    reported_issues: int
    created_issues: int
    rejected_issues: int
    no_issue_results: int
    uncertain_results: int
    issues: tuple[ReviewIssue, ...] = ()


class ProbeJudgeContractError(RuntimeError):
    """Raised when retries cannot produce one verdict per requested probe."""

    def __init__(
        self,
        *,
        batch: list[ProbeEvidenceBundle],
        response: ProbeJudgeResponse,
        contract_attempts: tuple[_ProbeVerdictContract, ...],
    ) -> None:
        super().__init__("Probe judge did not return exactly one result per probe")
        self.batch = batch
        self.response = response
        self.contract_attempts = contract_attempts
        self.duration_ms = 0


class ProbeJudgeService:
    """Ask the LLM to judge backend-selected evidence batches."""

    def __init__(
        self,
        *,
        llm: Any,
        report_repository: ReportRepository,
        max_probes_per_batch: int = 8,
        max_chunks_per_batch: int = 24,
        max_concurrency: int = 1,
        min_confidence: float = MIN_PROBE_ISSUE_CONFIDENCE,
    ) -> None:
        self.llm = llm
        self.report_repository = report_repository
        self.max_probes_per_batch = max(1, max_probes_per_batch)
        self.max_chunks_per_batch = max(1, max_chunks_per_batch)
        self.max_concurrency = max(1, max_concurrency)
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
        on_batch_completed: ProbeBatchProgressCallback | None = None,
    ) -> ProbeJudgeSummary:
        """Judge evidence batches and persist accepted issues."""

        summary = ProbeJudgeSummary()
        pending_issues: list[ReviewIssue] = []
        batches = _judge_batches(
            bundles,
            max_probes=self.max_probes_per_batch,
            max_chunks=self.max_chunks_per_batch,
        )
        total_batches = len(batches)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        tasks = [
            asyncio.create_task(
                self._judge_batch_with_limit(
                    batch_index=batch_index,
                    batch=batch,
                    semaphore=semaphore,
                )
            )
            for batch_index, batch in enumerate(batches)
        ]
        try:
            completed_tasks = asyncio.as_completed(tasks)
            for completed_task in completed_tasks:
                try:
                    _, batch, outcome = await completed_task
                except ProbeJudgeContractError as error:
                    await _write_probe_judge_trace(
                        trace_writer=trace_writer,
                        batch=error.batch,
                        response=error.response,
                        persistence_summary=_response_persistence_summary(
                            error.response
                        ),
                        duration_ms=error.duration_ms,
                        contract_attempts=error.contract_attempts,
                        status="contract_error",
                    )
                    raise
                batch_summary = self._build_batch_issues(
                    job_id=job_id,
                    results=outcome.response.results,
                    bundles=batch,
                )
                pending_issues.extend(batch_summary.issues)
                summary = ProbeJudgeSummary(
                    judged_batches=summary.judged_batches + 1,
                    reported_issues=(
                        summary.reported_issues + batch_summary.reported_issues
                    ),
                    created_issues=(
                        summary.created_issues + batch_summary.created_issues
                    ),
                    rejected_issues=(
                        summary.rejected_issues + batch_summary.rejected_issues
                    ),
                    no_issue_results=(
                        summary.no_issue_results + batch_summary.no_issue_results
                    ),
                    uncertain_results=(
                        summary.uncertain_results + batch_summary.uncertain_results
                    ),
                )
                await _write_probe_judge_trace(
                    trace_writer=trace_writer,
                    batch=batch,
                    response=outcome.response,
                    persistence_summary=batch_summary,
                    duration_ms=outcome.duration_ms,
                    contract_attempts=outcome.contract_attempts,
                )
                if on_batch_completed is not None:
                    await on_batch_completed(summary.judged_batches, total_batches)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        deduplicated_issues = _deduplicate_issues(pending_issues)
        summary = ProbeJudgeSummary(
            judged_batches=summary.judged_batches,
            reported_issues=summary.reported_issues,
            created_issues=len(deduplicated_issues),
            rejected_issues=(
                summary.rejected_issues
                + (len(pending_issues) - len(deduplicated_issues))
            ),
            no_issue_results=summary.no_issue_results,
            uncertain_results=summary.uncertain_results,
        )

        await self.report_repository.replace_ai_issues(
            job_id=job_id,
            issues=deduplicated_issues,
        )

        return summary

    async def _judge_batch_with_limit(
        self,
        *,
        batch_index: int,
        batch: list[ProbeEvidenceBundle],
        semaphore: asyncio.Semaphore,
    ) -> tuple[int, list[ProbeEvidenceBundle], _JudgeBatchOutcome]:
        """Run one LLM batch under the configured concurrency limit."""

        async with semaphore:
            started_at = time.perf_counter()
            try:
                response, contract_attempts = await self._judge_batch_with_retries(
                    batch
                )
            except ProbeJudgeContractError as error:
                error.duration_ms = int((time.perf_counter() - started_at) * 1000)
                raise
        return (
            batch_index,
            batch,
            _JudgeBatchOutcome(
                response=response,
                contract_attempts=contract_attempts,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            ),
        )

    async def _judge_batch_with_retries(
        self,
        batch: list[ProbeEvidenceBundle],
    ) -> tuple[ProbeJudgeResponse, tuple[_ProbeVerdictContract, ...]]:
        expected_by_id = {bundle.probe.probe_id: bundle for bundle in batch}
        expected_ids = tuple(expected_by_id)
        accepted: dict[str, ProbeJudgeResult] = {}
        attempts: list[_ProbeVerdictContract] = []

        response = await self._judge_batch(batch)
        contract = _probe_verdict_contract(response, expected_ids)
        attempts.append(contract)
        if contract.is_valid:
            return response, tuple(attempts)

        accepted.update(_valid_results_by_probe(response, expected_ids))
        retry_ids = _contract_retry_probe_ids(contract)
        retry_batch = [expected_by_id[probe_id] for probe_id in retry_ids]
        retry_response = await self._judge_batch(retry_batch)
        retry_contract = _probe_verdict_contract(retry_response, retry_ids)
        attempts.append(retry_contract)
        if retry_contract.is_valid:
            accepted.update(_valid_results_by_probe(retry_response, retry_ids))
            return _ordered_probe_response(accepted, expected_ids), tuple(attempts)

        retry_results = _valid_results_by_probe(retry_response, retry_ids)
        accepted.update(retry_results)
        unresolved_ids = _contract_retry_probe_ids(retry_contract)
        for probe_id in unresolved_ids:
            single_response = await self._judge_batch([expected_by_id[probe_id]])
            single_contract = _probe_verdict_contract(
                single_response,
                (probe_id,),
            )
            attempts.append(single_contract)
            if not single_contract.is_valid:
                raise ProbeJudgeContractError(
                    batch=batch,
                    response=_ordered_probe_response(accepted, expected_ids),
                    contract_attempts=tuple(attempts),
                )
            accepted.update(_valid_results_by_probe(single_response, (probe_id,)))

        if set(accepted) != set(expected_ids):
            raise ProbeJudgeContractError(
                batch=batch,
                response=_ordered_probe_response(accepted, expected_ids),
                contract_attempts=tuple(attempts),
            )
        return _ordered_probe_response(accepted, expected_ids), tuple(attempts)

    def _build_batch_issues(
        self,
        *,
        job_id: UUID,
        results: list[ProbeJudgeResult],
        bundles: list[ProbeEvidenceBundle],
    ) -> _BatchPersistenceSummary:
        created_issues: list[ReviewIssue] = []
        issue_results = [result for result in results if result.verdict == "issue"]
        reported_issues = sum(len(result.issues) for result in issue_results)
        for result in issue_results:
            for issue in result.issues:
                issue_payload = issue.model_dump()
                if issue_payload.get("confidence") is None:
                    issue_payload["confidence"] = result.confidence
                candidate = ProbeJudgeIssueCandidate(
                    **issue_payload,
                    verdict="issue",
                    probe_id=result.probe_id,
                )
                review_issue = self._build_candidate_issue(
                    job_id=job_id,
                    candidate=candidate,
                    bundles=bundles,
                )
                if review_issue is not None:
                    created_issues.append(review_issue)
        return _BatchPersistenceSummary(
            reported_issues=reported_issues,
            created_issues=len(created_issues),
            rejected_issues=reported_issues - len(created_issues),
            no_issue_results=sum(result.verdict == "no_issue" for result in results),
            uncertain_results=sum(result.verdict == "uncertain" for result in results),
            issues=tuple(created_issues),
        )

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

    def _build_candidate_issue(
        self,
        *,
        job_id: UUID,
        candidate: ProbeJudgeIssueCandidate,
        bundles: list[ProbeEvidenceBundle],
    ) -> ReviewIssue | None:
        context = self._candidate_persistence_context(
            candidate=candidate,
            bundles=bundles,
        )
        if context is None:
            return None
        return self._build_review_issue(
            job_id=job_id,
            candidate=candidate,
            context=context,
        )

    def _candidate_persistence_context(
        self,
        *,
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
        if not is_review_target_path(file_path):
            return None
        category = _issue_category(candidate.category)
        title = str(candidate.title or "AI review finding")
        rule_id = _candidate_rule_id(
            candidate,
            bundles,
            roadmap_by_id=self.roadmap_by_id,
        )
        claim_type = canonical_probe_claim_type(
            probe_id=candidate.probe_id,
            claim_type=candidate.claim_type,
            fallback_title=title,
            context_text=_candidate_claim_context(candidate, evidence_chunks),
        )
        allowed_claim_types = next(
            (
                bundle.probe.allowed_claim_types
                for bundle in bundles
                if bundle.probe.probe_id == candidate.probe_id
            ),
            (),
        )
        if allowed_claim_types and claim_type not in allowed_claim_types:
            return None
        finding_key = build_finding_key(
            category=category,
            claim_type=claim_type,
            title=title,
            roadmap_rule_id=rule_id,
        )
        if self._evidence_contradicts_candidate(candidate, rule_id, evidence_chunks):
            return None
        return _CandidatePersistenceContext(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            evidence_chunk=evidence_chunks[0],
            category=category,
            severity=_probe_issue_severity(candidate.probe_id, candidate.severity),
            rule_id=rule_id,
            finding_key=finding_key,
            claim_type=claim_type,
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
                CLAIM_TYPE_FIELD: context.claim_type,
                FINDING_KEY_FIELD: context.finding_key,
                "references": references,
                "probe_review": {
                    "probe_id": candidate.probe_id,
                    "rule_id": context.rule_id,
                    "claim_type": context.claim_type,
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


def _candidate_claim_context(
    candidate: ProbeJudgeIssueCandidate,
    evidence_chunks: list[ProbeCandidateChunk],
) -> str:
    context_parts = [candidate.description or "", candidate.suggestion or ""]
    context_parts.extend(chunk.content for chunk in evidence_chunks)
    return "\n".join(context_parts)


def _deduplicate_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    """Remove duplicate issues with same finding_key and overlapping lines."""

    if not issues:
        return []

    # Group by finding_key
    by_finding_key: dict[str, list[ReviewIssue]] = {}
    for issue in issues:
        raw_output = issue.raw_output if isinstance(issue.raw_output, dict) else {}
        finding_key = raw_output.get(FINDING_KEY_FIELD, "")
        if not isinstance(finding_key, str) or not finding_key:
            finding_key = f"{issue.file_path}:{issue.title}"
        by_finding_key.setdefault(finding_key, []).append(issue)

    deduplicated: list[ReviewIssue] = []
    for _finding_key, group in by_finding_key.items():
        # Within each finding_key group, dedupe by file_path + line overlap
        deduplicated.extend(_dedupe_overlapping_issues(group))

    return deduplicated


def _dedupe_overlapping_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    """Keep one issue per file_path when line ranges overlap."""

    if len(issues) <= 1:
        return issues

    # Group by file_path
    by_file: dict[str, list[ReviewIssue]] = {}
    for issue in issues:
        by_file.setdefault(issue.file_path, []).append(issue)

    result: list[ReviewIssue] = []
    for _file_path, file_issues in by_file.items():
        # Sort by line_start, then by confidence descending
        sorted_issues = sorted(
            file_issues,
            key=lambda i: (
                i.line_start,
                -(i.confidence or 0),
            ),
        )
        kept: list[ReviewIssue] = []
        for issue in sorted_issues:
            # Check if this issue overlaps with any kept issue
            overlaps = any(_lines_overlap(issue, kept_issue) for kept_issue in kept)
            if not overlaps:
                kept.append(issue)
            else:
                # If overlaps, keep the one with higher confidence
                for i, kept_issue in enumerate(kept):
                    if _lines_overlap(issue, kept_issue):
                        if (issue.confidence or 0) > (kept_issue.confidence or 0):
                            kept[i] = issue
                        break
        result.extend(kept)

    return result


def _lines_overlap(a: ReviewIssue, b: ReviewIssue) -> bool:
    """Check if two issues have overlapping line ranges."""
    return a.line_start <= b.line_end and b.line_start <= a.line_end


def _probe_verdict_contract(
    response: ProbeJudgeResponse,
    requested_probe_ids: tuple[str, ...],
) -> _ProbeVerdictContract:
    received_probe_ids = tuple(result.probe_id for result in response.results)
    counts = Counter(received_probe_ids)
    expected = set(requested_probe_ids)
    return _ProbeVerdictContract(
        requested_probe_ids=requested_probe_ids,
        received_probe_ids=received_probe_ids,
        missing_probe_ids=tuple(
            probe_id for probe_id in requested_probe_ids if counts[probe_id] == 0
        ),
        duplicate_probe_ids=tuple(
            probe_id for probe_id in requested_probe_ids if counts[probe_id] > 1
        ),
        unknown_probe_ids=tuple(
            sorted(probe_id for probe_id in counts if probe_id not in expected)
        ),
        schema_rejected_count=response.schema_rejected_count,
        has_unscoped_schema_error=response.has_unscoped_schema_error,
    )


def _valid_results_by_probe(
    response: ProbeJudgeResponse,
    requested_probe_ids: tuple[str, ...],
) -> dict[str, ProbeJudgeResult]:
    counts = Counter(result.probe_id for result in response.results)
    requested = set(requested_probe_ids)
    return {
        result.probe_id: result
        for result in response.results
        if result.probe_id in requested and counts[result.probe_id] == 1
    }


def _contract_retry_probe_ids(
    contract: _ProbeVerdictContract,
) -> tuple[str, ...]:
    invalid_ids = set(contract.missing_probe_ids) | set(contract.duplicate_probe_ids)
    if not invalid_ids and (
        contract.unknown_probe_ids or contract.has_unscoped_schema_error
    ):
        return contract.requested_probe_ids
    return tuple(
        probe_id for probe_id in contract.requested_probe_ids if probe_id in invalid_ids
    )


def _ordered_probe_response(
    results_by_probe: dict[str, ProbeJudgeResult],
    requested_probe_ids: tuple[str, ...],
) -> ProbeJudgeResponse:
    return ProbeJudgeResponse(
        results=[
            results_by_probe[probe_id]
            for probe_id in requested_probe_ids
            if probe_id in results_by_probe
        ]
    )


def _response_persistence_summary(
    response: ProbeJudgeResponse,
) -> _BatchPersistenceSummary:
    reported_issues = sum(
        len(result.issues) for result in response.results if result.verdict == "issue"
    )
    return _BatchPersistenceSummary(
        reported_issues=reported_issues,
        created_issues=0,
        rejected_issues=reported_issues,
        no_issue_results=sum(
            result.verdict == "no_issue" for result in response.results
        ),
        uncertain_results=sum(
            result.verdict == "uncertain" for result in response.results
        ),
    )


async def _write_probe_judge_trace(
    *,
    trace_writer: SyntheticTraceWriter,
    batch: list[ProbeEvidenceBundle],
    response: ProbeJudgeResponse,
    persistence_summary: _BatchPersistenceSummary,
    duration_ms: int,
    contract_attempts: tuple[_ProbeVerdictContract, ...] = (),
    status: str = "ok",
) -> None:
    requested_probe_ids = tuple(_probe_id(bundle.probe) for bundle in batch)
    final_contract = _probe_verdict_contract(response, requested_probe_ids)
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_JUDGE_TOOL_NAME,
        tool_input={
            "probe_ids": [_probe_id(bundle.probe) for bundle in batch],
            "probe_count": len(batch),
            "chunk_count": sum(len(bundle.candidate_chunks) for bundle in batch),
        },
        output={
            "status": status,
            "duration_ms": duration_ms,
            "result_count": len(response.results),
            "reported_issue_count": persistence_summary.reported_issues,
            "schema_rejected_count": response.schema_rejected_count,
            "requested_probe_ids": list(requested_probe_ids),
            "received_probe_ids": list(final_contract.received_probe_ids),
            "missing_probe_ids": list(final_contract.missing_probe_ids),
            "duplicate_probe_ids": list(final_contract.duplicate_probe_ids),
            "unknown_probe_ids": list(final_contract.unknown_probe_ids),
            "retry_count": max(0, len(contract_attempts) - 1),
            "contract_attempts": [
                {
                    "requested_probe_ids": list(attempt.requested_probe_ids),
                    "received_probe_ids": list(attempt.received_probe_ids),
                    "missing_probe_ids": list(attempt.missing_probe_ids),
                    "duplicate_probe_ids": list(attempt.duplicate_probe_ids),
                    "unknown_probe_ids": list(attempt.unknown_probe_ids),
                    "schema_rejected_count": attempt.schema_rejected_count,
                    "has_unscoped_schema_error": (attempt.has_unscoped_schema_error),
                }
                for attempt in contract_attempts
            ],
            "created_count": persistence_summary.created_issues,
            "rejected_issue_count": persistence_summary.rejected_issues,
            "no_issue_count": persistence_summary.no_issue_results,
            "uncertain_count": persistence_summary.uncertain_results,
            "results": [
                result.model_dump(
                    mode="json",
                    exclude={
                        "issues": {
                            "__all__": {"description", "suggestion"},
                        }
                    },
                )
                for result in response.results
            ],
        },
    )
