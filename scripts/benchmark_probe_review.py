"""Evaluate one AI probe-review job against an external benchmark manifest."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_OUTPUT_DIR = ROOT_DIR / ".tmp" / "benchmarks" / "probe-review"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

RECALL_CUTOFFS = (3, 6, 10)
MAX_JUDGE_CALLS = 9
MAX_RUNTIME_SECONDS = 20 * 60
MIN_RETRIEVAL_HITS = 10
MIN_END_TO_END_HITS = 8


@dataclass(slots=True, frozen=True)
class ExpectedIssue:
    issue_id: str
    file_path: str
    line_start: int
    line_end: int
    category: str
    severity: str | None
    expected_probes: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class FalsePositiveBait:
    bait_id: str
    file_path: str
    line_start: int
    line_end: int


@dataclass(slots=True, frozen=True)
class Finding:
    finding_id: str
    file_path: str
    line_start: int
    line_end: int
    category: str
    severity: str
    title: str
    source: str
    probe_id: str | None
    supporting_evidence: tuple[dict[str, object], ...]


@dataclass(slots=True, frozen=True)
class IssueEvaluation:
    issue_id: str
    indexed: bool
    aligned_rank: int | None
    any_probe_rank: int | None
    judge_hit: bool
    end_to_end_hit: bool
    severity_match: bool


@dataclass(slots=True, frozen=True)
class BenchmarkManifest:
    name: str
    repository_url: str
    commit_sha: str
    required_options: dict[str, object]
    expected_issues: tuple[ExpectedIssue, ...]
    false_positive_baits: tuple[FalsePositiveBait, ...]
    smart_baseline: dict[str, object]
    require_full_recall: bool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, type=UUID)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--false-positives", type=Path)
    parser.add_argument("--probe-map", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()
    asyncio.run(
        run_benchmark(
            job_id=args.job_id,
            manifest_path=args.manifest,
            ground_truth_path=args.ground_truth,
            false_positives_path=args.false_positives,
            probe_map_path=args.probe_map,
            output_dir=args.output_dir,
            run_name=args.run_name,
        )
    )


async def run_benchmark(
    *,
    job_id: UUID,
    manifest_path: Path | None,
    ground_truth_path: Path | None,
    false_positives_path: Path | None,
    probe_map_path: Path | None,
    output_dir: Path,
    run_name: str | None,
) -> None:
    from app.db.mongodb import (
        CHUNK_METADATA_COLLECTION,
        RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
        TOOL_CALL_LOGS_COLLECTION,
        get_mongodb_database,
    )
    from app.db.postgres import AsyncSessionLocal, close_postgres_engine
    from app.models.review_job import ReviewJob

    manifest = _load_benchmark_inputs(
        manifest_path=manifest_path,
        ground_truth_path=ground_truth_path,
        false_positives_path=false_positives_path,
        probe_map_path=probe_map_path,
    )
    expected_issues = list(manifest.expected_issues)
    baits = list(manifest.false_positive_baits)
    database = get_mongodb_database()
    try:
        trace_documents = cast(
            list[dict[str, Any]],
            await database[TOOL_CALL_LOGS_COLLECTION]
            .find({"job_id": str(job_id)})
            .sort("called_at", 1)
            .to_list(length=None),
        )
        trace_documents = _latest_probe_session(trace_documents)
        chunk_documents = cast(
            list[dict[str, Any]],
            await database[CHUNK_METADATA_COLLECTION]
            .find({"job_id": str(job_id)})
            .to_list(length=None),
        )
        static_documents = cast(
            list[dict[str, Any]],
            await database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION]
            .find({"job_id": str(job_id)})
            .to_list(length=None),
        )
        async with AsyncSessionLocal() as session:
            findings = await _load_findings(session, job_id)
            job = await session.scalar(select(ReviewJob).where(ReviewJob.id == job_id))

        payload = evaluate_job(
            job_id=job_id,
            expected_issues=expected_issues,
            baits=baits,
            findings=findings,
            trace_documents=trace_documents,
            chunk_documents=chunk_documents,
            static_documents=static_documents,
            runtime_seconds=_job_runtime_seconds(job),
            manifest=manifest,
            job=job,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = run_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = output_dir / f"{run_id}.json"
        markdown_path = output_dir / f"{run_id}.md"
        json_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        markdown_path.write_text(_markdown_report(payload), encoding="utf-8")
        print(f"Wrote {json_path}")
        print(f"Wrote {markdown_path}")
    finally:
        await close_postgres_engine()


def evaluate_job(
    *,
    job_id: UUID,
    expected_issues: list[ExpectedIssue],
    baits: list[FalsePositiveBait],
    findings: list[Finding],
    trace_documents: list[dict[str, Any]],
    chunk_documents: list[dict[str, Any]],
    static_documents: list[dict[str, Any]],
    runtime_seconds: float | None,
    manifest: BenchmarkManifest,
    job: Any | None,
) -> dict[str, object]:
    retrieval_documents = [
        document
        for document in trace_documents
        if document.get("tool_name") == "probe_retrieval"
    ]
    judge_documents = [
        document
        for document in trace_documents
        if document.get("tool_name") == "probe_judge"
    ]
    evaluations = [
        _evaluate_issue(
            issue,
            retrieval_documents=retrieval_documents,
            judge_documents=judge_documents,
            findings=findings,
            chunk_documents=chunk_documents,
        )
        for issue in expected_issues
    ]
    recall = {
        f"recall_at_{cutoff}": _ratio(
            sum(
                evaluation.aligned_rank is not None
                and evaluation.aligned_rank <= cutoff
                for evaluation in evaluations
            ),
            len(evaluations),
        )
        for cutoff in RECALL_CUTOFFS
    }
    aligned_hits = sum(
        evaluation.aligned_rank is not None for evaluation in evaluations
    )
    any_probe_hits = sum(
        evaluation.any_probe_rank is not None for evaluation in evaluations
    )
    evidence_issue_count = sum(
        evaluation.aligned_rank is not None for evaluation in evaluations
    )
    judge_hits = sum(evaluation.judge_hit for evaluation in evaluations)
    end_to_end_hits = sum(evaluation.end_to_end_hit for evaluation in evaluations)
    bait_hits = _bait_hits(baits, findings)
    matched_finding_ids = {
        finding.finding_id
        for finding in findings
        if any(_finding_matches_issue(finding, issue) for issue in expected_issues)
    }
    bait_finding_ids = {
        finding.finding_id
        for finding in findings
        if any(_finding_matches_bait(finding, bait) for bait in baits)
    }
    out_of_scope = [
        asdict(finding)
        for finding in findings
        if finding.finding_id not in matched_finding_ids | bait_finding_ids
    ]
    lane_metrics = _lane_metrics(retrieval_documents, judge_documents, trace_documents)
    indexed_hits = sum(evaluation.indexed for evaluation in evaluations)
    duplicate_count = sum(
        max(
            0,
            sum(_finding_matches_issue(finding, issue) for finding in findings) - 1,
        )
        for issue in expected_issues
    )
    runtime_is_accepted = (
        runtime_seconds is not None and runtime_seconds <= MAX_RUNTIME_SECONDS
    )
    full_recall_target = len(evaluations)
    required_retrieval_hits = (
        full_recall_target if manifest.require_full_recall else MIN_RETRIEVAL_HITS
    )
    required_end_to_end_hits = (
        full_recall_target if manifest.require_full_recall else MIN_END_TO_END_HITS
    )
    severity_is_accepted = all(evaluation.severity_match for evaluation in evaluations)
    judge_contract = _judge_contract_metrics(judge_documents)
    configuration = _benchmark_configuration(job, manifest)
    acceptance = {
        "configuration": configuration["matches"],
        "retrieval": aligned_hits >= required_retrieval_hits,
        "end_to_end": end_to_end_hits >= required_end_to_end_hits,
        "severity": severity_is_accepted,
        "false_positive_bait": not bait_hits,
        "judge_contract": judge_contract["is_complete"],
        "judge_calls": (
            True
            if manifest.require_full_recall
            else len(judge_documents) <= MAX_JUDGE_CALLS
        ),
        "runtime": True if manifest.require_full_recall else runtime_is_accepted,
    }
    acceptance["passed"] = all(acceptance.values())
    return {
        "job_id": str(job_id),
        "benchmark": {
            "name": manifest.name,
            "repository_url": manifest.repository_url,
            "commit_sha": manifest.commit_sha,
            "required_options": manifest.required_options,
        },
        "configuration": configuration,
        "generated_at": datetime.now(UTC).isoformat(),
        "ground_truth_count": len(expected_issues),
        "indexed_chunk_count": len(chunk_documents),
        "indexed_coverage": {
            "hits": indexed_hits,
            "total": len(evaluations),
            "ratio": _ratio(indexed_hits, len(evaluations)),
        },
        "aligned_retrieval": {
            "hits": aligned_hits,
            "total": len(evaluations),
            "ratio": _ratio(aligned_hits, len(evaluations)),
            **recall,
        },
        "any_probe_recall": {
            "hits": any_probe_hits,
            "total": len(evaluations),
            "ratio": _ratio(any_probe_hits, len(evaluations)),
        },
        "judge_recall_given_evidence": {
            "hits": judge_hits,
            "total": evidence_issue_count,
            "ratio": _ratio(judge_hits, evidence_issue_count),
        },
        "end_to_end_recall": {
            "hits": end_to_end_hits,
            "total": len(evaluations),
            "ratio": _ratio(end_to_end_hits, len(evaluations)),
        },
        "duplicate_count": duplicate_count,
        "false_positive_baits": bait_hits,
        "out_of_scope_findings": out_of_scope,
        "finding_count": len(findings),
        "judge_call_count": len(judge_documents),
        "judge_contract": judge_contract,
        "runtime_seconds": runtime_seconds,
        "token_totals": _token_totals(trace_documents),
        "smart_baseline": manifest.smart_baseline,
        "cost_comparison": _cost_comparison(
            token_totals=_token_totals(trace_documents),
            runtime_seconds=runtime_seconds,
            smart_baseline=manifest.smart_baseline,
        ),
        "static_analysis": _static_analysis_metrics(static_documents),
        "lane_metrics": lane_metrics,
        "issues": [asdict(evaluation) for evaluation in evaluations],
        "acceptance": acceptance,
    }


async def _load_findings(session: Any, job_id: UUID) -> list[Finding]:
    from app.models.review_issue import IssueCategory, IssueSource, ReviewIssue

    ai_sources = (IssueSource.AI_REVIEW, IssueSource.KB)
    result = await session.scalars(
        select(ReviewIssue)
        .where(
            ReviewIssue.job_id == job_id,
            ReviewIssue.source.in_(ai_sources),
            ReviewIssue.category != IssueCategory.STYLE,
        )
        .order_by(ReviewIssue.created_at, ReviewIssue.id)
    )
    return [
        Finding(
            finding_id=str(issue.id),
            file_path=issue.file_path,
            line_start=issue.line_start,
            line_end=issue.line_end,
            category=issue.category.value,
            severity=issue.severity.value,
            title=issue.title,
            source=issue.source.value,
            probe_id=_finding_probe_id(issue.raw_output),
            supporting_evidence=_finding_supporting_evidence(issue.raw_output),
        )
        for issue in result.all()
    ]


def _load_expected_issues(
    ground_truth_path: Path,
    probe_map_path: Path,
) -> list[ExpectedIssue]:
    ground_truth = _json_list(ground_truth_path)
    raw_probe_map = _json_object(probe_map_path)
    return _expected_issues_from_items(ground_truth, raw_probe_map=raw_probe_map)


def _expected_issues_from_items(
    ground_truth: list[dict[str, object]],
    *,
    raw_probe_map: dict[str, object] | None = None,
) -> list[ExpectedIssue]:
    issues: list[ExpectedIssue] = []
    for item in ground_truth:
        detectors = _string_list(item.get("detector_expected"))
        if detectors and "ai_review" not in detectors:
            continue
        issue_id = _required_string(item, "id")
        expected_probes = _string_list(item.get("expected_probes"))
        if not expected_probes and raw_probe_map is not None:
            expected_probes = _string_list(raw_probe_map.get(issue_id))
        if not expected_probes:
            raise ValueError(f"Missing expected probe mapping for {issue_id}")
        severity = item.get("severity")
        issues.append(
            ExpectedIssue(
                issue_id=issue_id,
                file_path=_required_string(item, "file_path"),
                line_start=_required_int(item, "line_start"),
                line_end=_required_int(item, "line_end"),
                category=_required_string(item, "category"),
                severity=severity if isinstance(severity, str) else None,
                expected_probes=tuple(expected_probes),
            )
        )
    return issues


def _load_false_positive_baits(path: Path) -> list[FalsePositiveBait]:
    return _false_positive_baits_from_items(_json_list(path))


def _false_positive_baits_from_items(
    items: list[dict[str, object]],
) -> list[FalsePositiveBait]:
    baits: list[FalsePositiveBait] = []
    for item in items:
        line_start = _safe_int(item.get("line_start") or item.get("line"))
        if line_start <= 0:
            raise ValueError("False-positive bait requires line or line_start")
        baits.append(
            FalsePositiveBait(
                bait_id=_required_string(item, "id"),
                file_path=_required_string(item, "file_path"),
                line_start=line_start,
                line_end=_safe_int(item.get("line_end"), default=line_start),
            )
        )
    return baits


def _load_benchmark_inputs(
    *,
    manifest_path: Path | None,
    ground_truth_path: Path | None,
    false_positives_path: Path | None,
    probe_map_path: Path | None,
) -> BenchmarkManifest:
    if manifest_path is not None:
        payload = _json_object(manifest_path)
        expected_items = payload.get("expected_issues")
        bait_items = payload.get("false_positive_baits", [])
        if not isinstance(expected_items, list) or not isinstance(bait_items, list):
            raise ValueError("Benchmark manifest issues and baits must be arrays")
        expected = [item for item in expected_items if isinstance(item, dict)]
        baits = [item for item in bait_items if isinstance(item, dict)]
        return BenchmarkManifest(
            name=_required_string(payload, "name"),
            repository_url=_required_string(payload, "repository_url"),
            commit_sha=_required_string(payload, "commit_sha"),
            required_options=_mapping(payload.get("review_options")),
            expected_issues=tuple(_expected_issues_from_items(expected)),
            false_positive_baits=tuple(_false_positive_baits_from_items(baits)),
            smart_baseline=_mapping(payload.get("smart_baseline")),
            require_full_recall=payload.get("require_full_recall") is True,
        )

    if not ground_truth_path or not false_positives_path or not probe_map_path:
        raise ValueError(
            "Use --manifest or provide --ground-truth, --false-positives, "
            "and --probe-map"
        )
    return BenchmarkManifest(
        name="legacy_probe_review",
        repository_url="",
        commit_sha="",
        required_options={},
        expected_issues=tuple(_load_expected_issues(ground_truth_path, probe_map_path)),
        false_positive_baits=tuple(_load_false_positive_baits(false_positives_path)),
        smart_baseline={},
        require_full_recall=False,
    )


def _evaluate_issue(
    issue: ExpectedIssue,
    *,
    retrieval_documents: list[dict[str, Any]],
    judge_documents: list[dict[str, Any]],
    findings: list[Finding],
    chunk_documents: list[dict[str, Any]],
) -> IssueEvaluation:
    return IssueEvaluation(
        issue_id=issue.issue_id,
        indexed=any(
            _document_matches_issue(document, issue) for document in chunk_documents
        ),
        aligned_rank=_first_trace_matching_rank(
            retrieval_documents,
            issue,
            allowed_probes=set(issue.expected_probes),
        ),
        any_probe_rank=_first_trace_matching_rank(
            retrieval_documents,
            issue,
        ),
        judge_hit=any(
            _judge_candidate_matches_issue(candidate, issue)
            for document in judge_documents
            for candidate in _judge_candidates(document)
        ),
        end_to_end_hit=any(
            _finding_matches_issue(finding, issue) for finding in findings
        ),
        severity_match=any(
            _finding_matches_issue(finding, issue)
            and _severities_compatible(finding.severity, issue.severity)
            for finding in findings
        ),
    )


def _first_trace_matching_rank(
    documents: list[dict[str, Any]],
    issue: ExpectedIssue,
    *,
    allowed_probes: set[str] | None = None,
) -> int | None:
    ranks = [
        rank
        for document in documents
        if allowed_probes is None or _probe_id(document) in allowed_probes
        for rank, result in enumerate(_trace_results(document), start=1)
        if _document_matches_issue(result, issue)
    ]
    return min(ranks, default=None)


def _document_matches_issue(
    document: dict[str, object],
    issue: ExpectedIssue,
) -> bool:
    return _path_matches(document.get("file_path"), issue.file_path) and _overlaps(
        document.get("line_start"),
        document.get("line_end"),
        issue.line_start,
        issue.line_end,
    )


def _judge_candidate_matches_issue(
    candidate: dict[str, object],
    issue: ExpectedIssue,
) -> bool:
    if candidate.get("verdict") != "issue":
        return False
    if str(candidate.get("probe_id") or "") not in issue.expected_probes:
        return False
    category = str(candidate.get("category") or "")
    return _categories_compatible(category, issue.category) and (
        _document_matches_issue(candidate, issue)
        or any(
            _document_matches_issue(evidence, issue)
            for evidence in _object_list(candidate.get("supporting_evidence"))
        )
    )


def _finding_matches_issue(finding: Finding, issue: ExpectedIssue) -> bool:
    if finding.probe_id not in issue.expected_probes:
        return False
    location_matches = _path_matches(
        finding.file_path, issue.file_path
    ) and _ranges_overlap(
        finding.line_start,
        finding.line_end,
        issue.line_start,
        issue.line_end,
    )
    evidence_matches = any(
        _document_matches_issue(evidence, issue)
        for evidence in finding.supporting_evidence
    )
    return _categories_compatible(finding.category, issue.category) and (
        location_matches or evidence_matches
    )


def _finding_probe_id(raw_output: object) -> str | None:
    probe_review = _mapping(_mapping(raw_output).get("probe_review"))
    probe_id = probe_review.get("probe_id")
    return probe_id if isinstance(probe_id, str) and probe_id else None


def _finding_supporting_evidence(
    raw_output: object,
) -> tuple[dict[str, object], ...]:
    probe_review = _mapping(_mapping(raw_output).get("probe_review"))
    return tuple(_object_list(probe_review.get("supporting_evidence")))


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _finding_matches_bait(finding: Finding, bait: FalsePositiveBait) -> bool:
    return _path_matches(finding.file_path, bait.file_path) and _ranges_overlap(
        finding.line_start,
        finding.line_end,
        bait.line_start,
        bait.line_end,
    )


def _bait_hits(
    baits: list[FalsePositiveBait],
    findings: list[Finding],
) -> list[dict[str, object]]:
    return [
        {
            "bait_id": bait.bait_id,
            "finding_ids": [
                finding.finding_id
                for finding in findings
                if _finding_matches_bait(finding, bait)
            ],
        }
        for bait in baits
        if any(_finding_matches_bait(finding, bait) for finding in findings)
    ]


def _lane_metrics(
    retrieval_documents: list[dict[str, Any]],
    judge_documents: list[dict[str, Any]],
    all_trace_documents: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    probe_lanes = {
        _probe_id(document): _probe_lane(document) for document in retrieval_documents
    }
    metrics: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {
            "retrieval_calls": 0,
            "retrieved_chunks": 0,
            "judged_chunks": 0,
            "judge_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0,
        }
    )
    for document in retrieval_documents:
        lane = _probe_lane(document)
        output = _mapping(document.get("output"))
        metrics[lane]["retrieval_calls"] += 1
        metrics[lane]["retrieved_chunks"] += _safe_int(
            output.get("selected_count") or output.get("result_count") or 0
        )
        metrics[lane]["judged_chunks"] += _safe_int(
            output.get("sent_to_judge") or output.get("result_count") or 0
        )
        metrics[lane]["latency_ms"] += _safe_int(output.get("duration_ms"))

    llm_documents = [
        document
        for document in all_trace_documents
        if document.get("event_type") == "llm"
        and document.get("tool_name") == "llm_call"
    ]
    for index, document in enumerate(judge_documents):
        raw_probe_ids = _mapping(document.get("input")).get("probe_ids")
        probe_ids = _string_list(raw_probe_ids)
        lanes = {probe_lanes.get(probe_id, "unknown") for probe_id in probe_ids}
        lane = lanes.pop() if len(lanes) == 1 else "mixed"
        output = _mapping(document.get("output"))
        metrics[lane]["judge_calls"] += 1
        metrics[lane]["latency_ms"] += _safe_int(output.get("duration_ms"))
        if index >= len(llm_documents):
            continue
        usage = _mapping(llm_documents[index].get("token_usage"))
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            metrics[lane][key] += _safe_int(usage.get(key))
    return dict(sorted(metrics.items()))


def _judge_contract_metrics(
    judge_documents: list[dict[str, Any]],
) -> dict[str, object]:
    missing_count = 0
    duplicate_count = 0
    unknown_count = 0
    contract_error_count = 0
    retry_count = 0
    observed_violation_count = 0
    for document in judge_documents:
        tool_input = _mapping(document.get("input"))
        output = _mapping(document.get("output"))
        requested = _string_list(tool_input.get("probe_ids"))
        candidates = output.get("candidates")
        candidate_items = candidates if isinstance(candidates, list) else []
        received = [
            str(candidate.get("probe_id"))
            for candidate in candidate_items
            if isinstance(candidate, dict) and candidate.get("probe_id")
        ]
        counts = Counter(received)
        missing = _string_list(output.get("missing_probe_ids")) or [
            probe_id for probe_id in requested if counts[probe_id] == 0
        ]
        duplicates = _string_list(output.get("duplicate_probe_ids")) or [
            probe_id for probe_id in requested if counts[probe_id] > 1
        ]
        unknown = _string_list(output.get("unknown_probe_ids")) or [
            probe_id for probe_id in counts if probe_id not in set(requested)
        ]
        missing_count += len(missing)
        duplicate_count += len(duplicates)
        unknown_count += len(unknown)
        retry_count += _safe_int(output.get("retry_count"))
        contract_error_count += output.get("status") == "contract_error"
        attempts = output.get("contract_attempts")
        if isinstance(attempts, list):
            observed_violation_count += sum(
                bool(_string_list(_mapping(attempt).get("missing_probe_ids")))
                or bool(_string_list(_mapping(attempt).get("duplicate_probe_ids")))
                or bool(_string_list(_mapping(attempt).get("unknown_probe_ids")))
                or _safe_int(_mapping(attempt).get("schema_rejected_count")) > 0
                for attempt in attempts
            )
    return {
        "is_complete": not (
            missing_count or duplicate_count or unknown_count or contract_error_count
        ),
        "missing_verdict_count": missing_count,
        "duplicate_verdict_count": duplicate_count,
        "unknown_verdict_count": unknown_count,
        "contract_error_count": contract_error_count,
        "retry_count": retry_count,
        "observed_violation_count": observed_violation_count,
    }


def _token_totals(trace_documents: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_input_tokens": 0,
    }
    for document in trace_documents:
        usage = _mapping(document.get("token_usage"))
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            totals[key] += _safe_int(usage.get(key))
        totals["estimated_input_tokens"] += _safe_int(
            _mapping(document.get("output")).get("estimated_input_tokens")
        )
    return totals


def _static_analysis_metrics(
    static_documents: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    metrics: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"runs": 0, "finding_count": 0, "nonzero_exit_count": 0}
    )
    for document in static_documents:
        tool = str(document.get("tool") or "unknown")
        parsed_issues = document.get("parsed_issues")
        metrics[tool]["runs"] += 1
        metrics[tool]["finding_count"] += (
            len(parsed_issues) if isinstance(parsed_issues, list) else 0
        )
        metrics[tool]["nonzero_exit_count"] += _safe_int(document.get("exit_code")) != 0
    return dict(sorted(metrics.items()))


def _benchmark_configuration(
    job: Any | None,
    manifest: BenchmarkManifest,
) -> dict[str, object]:
    actual_commit_sha = getattr(job, "commit_sha", None) if job is not None else None
    actual_options = _mapping(getattr(job, "options", None))
    commit_matches = not manifest.commit_sha or actual_commit_sha == manifest.commit_sha
    option_mismatches = {
        key: {"expected": expected, "actual": actual_options.get(key)}
        for key, expected in manifest.required_options.items()
        if actual_options.get(key) != expected
    }
    return {
        "matches": commit_matches and not option_mismatches,
        "actual_commit_sha": actual_commit_sha,
        "commit_matches": commit_matches,
        "actual_options": actual_options,
        "option_mismatches": option_mismatches,
    }


def _cost_comparison(
    *,
    token_totals: dict[str, int],
    runtime_seconds: float | None,
    smart_baseline: dict[str, object],
) -> dict[str, float | None]:
    baseline_tokens = _safe_int(smart_baseline.get("total_tokens"))
    baseline_runtime = smart_baseline.get("runtime_seconds")
    runtime_value = (
        float(baseline_runtime)
        if isinstance(baseline_runtime, int | float)
        and not isinstance(baseline_runtime, bool)
        else 0.0
    )
    return {
        "token_ratio": (
            round(token_totals["total_tokens"] / baseline_tokens, 4)
            if baseline_tokens > 0
            else None
        ),
        "runtime_ratio": (
            round(runtime_seconds / runtime_value, 4)
            if runtime_seconds is not None and runtime_value > 0
            else None
        ),
    }


def _latest_probe_session(
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    judge_documents = [
        document for document in documents if document.get("tool_name") == "probe_judge"
    ]
    if not judge_documents:
        return documents
    latest_judge = max(
        judge_documents, key=lambda item: str(item.get("called_at") or "")
    )
    session_id = latest_judge.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return documents
    return [
        document for document in documents if document.get("session_id") == session_id
    ]


def _job_runtime_seconds(job: Any | None) -> float | None:
    if job is None or job.started_at is None or job.completed_at is None:
        return None
    return max(0.0, (job.completed_at - job.started_at).total_seconds())


def _trace_results(document: dict[str, Any]) -> list[dict[str, object]]:
    results = _mapping(document.get("output")).get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _judge_candidates(document: dict[str, Any]) -> list[dict[str, object]]:
    candidates = _mapping(document.get("output")).get("candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _probe_id(document: dict[str, Any]) -> str:
    return str(_mapping(document.get("input")).get("probe_id") or "")


def _probe_lane(document: dict[str, Any]) -> str:
    tool_input = _mapping(document.get("input"))
    lane = tool_input.get("lane")
    if isinstance(lane, str) and lane:
        return lane
    probe_id = str(tool_input.get("probe_id") or "")
    if probe_id.startswith("coverage."):
        return "coverage"
    if tool_input.get("related_rule_ids"):
        return "roadmap"
    return "defect"


def _categories_compatible(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    return expected == "maintainability" and actual in {"requirement", "style"}


def _severities_compatible(actual: str, expected: str | None) -> bool:
    return expected is None or actual.strip().lower() == expected.strip().lower()


def _path_matches(value: object, expected: str) -> bool:
    if not isinstance(value, str):
        return False
    actual_path = _normalize_path(value)
    expected_path = _normalize_path(expected)
    return actual_path == expected_path or actual_path.endswith(f"/{expected_path}")


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().removeprefix("./").lower()


def _overlaps(
    line_start: object,
    line_end: object,
    expected_start: int,
    expected_end: int,
) -> bool:
    if not isinstance(line_start, int) or not isinstance(line_end, int):
        return False
    return _ranges_overlap(line_start, line_end, expected_start, expected_end)


def _ranges_overlap(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> bool:
    return first_start <= second_end and first_end >= second_start


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _json_list(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return [item for item in payload if isinstance(item, dict)]


def _json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _required_string(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing string field {key}")
    return value


def _required_int(item: dict[str, object], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int):
        raise ValueError(f"Missing integer field {key}")
    return value


def _safe_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _markdown_report(payload: dict[str, object]) -> str:
    indexed = _mapping(payload["indexed_coverage"])
    aligned = _mapping(payload["aligned_retrieval"])
    any_probe = _mapping(payload["any_probe_recall"])
    judge = _mapping(payload["judge_recall_given_evidence"])
    end_to_end = _mapping(payload["end_to_end_recall"])
    acceptance = _mapping(payload["acceptance"])
    token_totals = _mapping(payload["token_totals"])
    judge_contract = _mapping(payload["judge_contract"])
    cost_comparison = _mapping(payload["cost_comparison"])
    bait_count = len(cast(list[object], payload["false_positive_baits"]))
    lines = [
        "# AI Probe Benchmark",
        "",
        f"- Job: `{payload['job_id']}`",
        f"- Indexed coverage: `{indexed.get('hits')}/{indexed.get('total')}`",
        f"- Aligned retrieval: `{aligned.get('hits')}/{aligned.get('total')}`",
        f"- Recall@3/6/10: `{aligned.get('recall_at_3')}` / "
        f"`{aligned.get('recall_at_6')}` / `{aligned.get('recall_at_10')}`",
        f"- Any-probe recall: `{any_probe.get('hits')}/{any_probe.get('total')}`",
        f"- Judge recall given evidence: `{judge.get('hits')}/{judge.get('total')}`",
        f"- End-to-end recall: `{end_to_end.get('hits')}/{end_to_end.get('total')}`",
        f"- False-positive baits: `{bait_count}`",
        f"- Duplicates: `{payload['duplicate_count']}`",
        f"- Judge calls: `{payload['judge_call_count']}`",
        f"- Judge contract complete: `{judge_contract.get('is_complete')}`",
        f"- Judge retries: `{judge_contract.get('retry_count')}`",
        f"- Total tokens: `{token_totals.get('total_tokens')}`",
        f"- Token ratio vs smart: `{cost_comparison.get('token_ratio')}`",
        f"- Runtime: `{payload['runtime_seconds']}` seconds",
        f"- Runtime ratio vs smart: `{cost_comparison.get('runtime_ratio')}`",
        f"- Acceptance: `{'PASS' if acceptance.get('passed') else 'FAIL'}`",
        "",
        "## Per Issue",
        "",
        "| Issue | Indexed | Aligned rank | Any rank | Judge | End-to-end | Severity |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for issue in cast(list[dict[str, object]], payload["issues"]):
        lines.append(
            f"| {issue['issue_id']} | {issue['indexed']} | "
            f"{issue['aligned_rank']} | {issue['any_probe_rank']} | "
            f"{issue['judge_hit']} | {issue['end_to_end_hit']} | "
            f"{issue['severity_match']} |"
        )
    lines.extend(["", "## Lane Metrics", "", "```json"])
    lines.append(json.dumps(payload["lane_metrics"], indent=2, sort_keys=True))
    lines.extend(["```", "", "## Static Analysis", "", "```json"])
    lines.append(json.dumps(payload["static_analysis"], indent=2, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
