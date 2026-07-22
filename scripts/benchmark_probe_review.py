"""Evaluate one AI probe-review job against an external benchmark manifest."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
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
    title: str
    source: str


@dataclass(slots=True, frozen=True)
class IssueEvaluation:
    issue_id: str
    indexed: bool
    aligned_rank: int | None
    any_probe_rank: int | None
    judge_hit: bool
    end_to_end_hit: bool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, type=UUID)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--false-positives", required=True, type=Path)
    parser.add_argument("--probe-map", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()
    asyncio.run(
        run_benchmark(
            job_id=args.job_id,
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
    ground_truth_path: Path,
    false_positives_path: Path,
    probe_map_path: Path,
    output_dir: Path,
    run_name: str | None,
) -> None:
    from app.db.mongodb import (
        CHUNK_METADATA_COLLECTION,
        TOOL_CALL_LOGS_COLLECTION,
        get_mongodb_database,
    )
    from app.db.postgres import AsyncSessionLocal, close_postgres_engine
    from app.models.review_job import ReviewJob

    expected_issues = _load_expected_issues(ground_truth_path, probe_map_path)
    baits = _load_false_positive_baits(false_positives_path)
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
            runtime_seconds=_job_runtime_seconds(job),
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
    runtime_seconds: float | None,
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
    acceptance = {
        "retrieval": aligned_hits >= MIN_RETRIEVAL_HITS,
        "end_to_end": end_to_end_hits >= MIN_END_TO_END_HITS,
        "false_positive_bait": not bait_hits,
        "judge_calls": len(judge_documents) <= MAX_JUDGE_CALLS,
        "runtime": runtime_is_accepted,
    }
    acceptance["passed"] = all(acceptance.values())
    return {
        "job_id": str(job_id),
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
        "runtime_seconds": runtime_seconds,
        "lane_metrics": lane_metrics,
        "issues": [asdict(evaluation) for evaluation in evaluations],
        "acceptance": acceptance,
    }


async def _load_findings(session: Any, job_id: UUID) -> list[Finding]:
    from app.models.review_issue import IssueSource, ReviewIssue

    ai_sources = (IssueSource.AI_REVIEW, IssueSource.KB)
    result = await session.scalars(
        select(ReviewIssue)
        .where(ReviewIssue.job_id == job_id, ReviewIssue.source.in_(ai_sources))
        .order_by(ReviewIssue.created_at, ReviewIssue.id)
    )
    return [
        Finding(
            finding_id=str(issue.id),
            file_path=issue.file_path,
            line_start=issue.line_start,
            line_end=issue.line_end,
            category=issue.category.value,
            title=issue.title,
            source=issue.source.value,
        )
        for issue in result.all()
    ]


def _load_expected_issues(
    ground_truth_path: Path,
    probe_map_path: Path,
) -> list[ExpectedIssue]:
    ground_truth = _json_list(ground_truth_path)
    raw_probe_map = _json_object(probe_map_path)
    issues: list[ExpectedIssue] = []
    for item in ground_truth:
        detectors = _string_list(item.get("detector_expected"))
        if "ai_review" not in detectors:
            continue
        issue_id = _required_string(item, "id")
        expected_probes = _string_list(raw_probe_map.get(issue_id))
        if not expected_probes:
            raise ValueError(f"Missing expected probe mapping for {issue_id}")
        issues.append(
            ExpectedIssue(
                issue_id=issue_id,
                file_path=_required_string(item, "file_path"),
                line_start=_required_int(item, "line_start"),
                line_end=_required_int(item, "line_end"),
                category=_required_string(item, "category"),
                expected_probes=tuple(expected_probes),
            )
        )
    return issues


def _load_false_positive_baits(path: Path) -> list[FalsePositiveBait]:
    baits: list[FalsePositiveBait] = []
    for item in _json_list(path):
        line_start = _required_int(item, "line")
        baits.append(
            FalsePositiveBait(
                bait_id=_required_string(item, "id"),
                file_path=_required_string(item, "file_path"),
                line_start=line_start,
                line_end=_safe_int(item.get("line_end"), default=line_start),
            )
        )
    return baits


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
    category = str(candidate.get("category") or "")
    return _categories_compatible(category, issue.category) and _document_matches_issue(
        candidate,
        issue,
    )


def _finding_matches_issue(finding: Finding, issue: ExpectedIssue) -> bool:
    return (
        _path_matches(finding.file_path, issue.file_path)
        and _ranges_overlap(
            finding.line_start,
            finding.line_end,
            issue.line_start,
            issue.line_end,
        )
        and _categories_compatible(finding.category, issue.category)
    )


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
        f"- Runtime: `{payload['runtime_seconds']}` seconds",
        f"- Acceptance: `{'PASS' if acceptance.get('passed') else 'FAIL'}`",
        "",
        "## Per Issue",
        "",
        "| Issue | Indexed | Aligned rank | Any rank | Judge | End-to-end |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for issue in cast(list[dict[str, object]], payload["issues"]):
        lines.append(
            f"| {issue['issue_id']} | {issue['indexed']} | "
            f"{issue['aligned_rank']} | {issue['any_probe_rank']} | "
            f"{issue['judge_hit']} | {issue['end_to_end_hit']} |"
        )
    lines.extend(["", "## Lane Metrics", "", "```json"])
    lines.append(json.dumps(payload["lane_metrics"], indent=2, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
