"""Deterministic evaluation for persisted executable fix contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixScenarioStatus,
)


class FixJobBenchmarkRecord(Protocol):
    """Persisted fields consumed by the benchmark evaluator."""

    id: object
    validation_status: FixValidationStatus
    publish_allow_failed_validation: bool
    diff: str | None
    changed_files: list[str] | None
    issue_plan: list[dict[str, object]]
    issue_results: list[dict[str, object]]


def evaluate_fix_job(
    fix_job: FixJobBenchmarkRecord,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Return deterministic pass/fail checks for one completed fix job."""

    plans = [FixIssuePlan.model_validate(payload) for payload in fix_job.issue_plan]
    results = [
        FixIssueResult.model_validate(payload) for payload in fix_job.issue_results
    ]
    plans_by_probe = {plan.probe_id: plan for plan in plans if plan.probe_id}
    results_by_probe = {
        result.probe_id: result for result in results if result.probe_id
    }
    required_probes = _required_probes(manifest)
    checks: dict[str, bool] = {
        "validation_passed": fix_job.validation_status == FixValidationStatus.PASSED,
        "not_manually_overridden": not fix_job.publish_allow_failed_validation,
        "has_patch": bool(fix_job.diff and fix_job.changed_files),
    }

    for probe_id, term_groups in required_probes.items():
        plan = plans_by_probe.get(probe_id)
        result = results_by_probe.get(probe_id)
        checks[f"{probe_id}:present"] = plan is not None and result is not None
        if plan is None or result is None:
            continue
        checks[f"{probe_id}:fixed"] = result.verdict == FixIssueVerdict.FIXED
        exploit_results = [
            scenario
            for scenario in result.scenario_results
            if scenario.kind == FixScenarioKind.EXPLOIT
        ]
        preserved_results = [
            scenario
            for scenario in result.scenario_results
            if scenario.kind == FixScenarioKind.PRESERVED_BEHAVIOR
        ]
        checks[f"{probe_id}:exploit_reproduced_and_blocked"] = bool(
            exploit_results
        ) and all(
            scenario.baseline_status == FixScenarioStatus.FAILED
            and scenario.patched_status == FixScenarioStatus.PASSED
            for scenario in exploit_results
        )
        checks[f"{probe_id}:behavior_preserved"] = bool(preserved_results) and all(
            scenario.baseline_status == FixScenarioStatus.PASSED
            and scenario.patched_status == FixScenarioStatus.PASSED
            for scenario in preserved_results
        )
        contract_text = " ".join(
            [
                *plan.affected_contracts,
                *(scenario.description for scenario in plan.exploit_scenarios),
                *(
                    scenario.description
                    for scenario in plan.preserved_behavior_scenarios
                ),
            ]
        ).casefold()
        for index, terms in enumerate(term_groups):
            checks[f"{probe_id}:contract_{index}"] = all(
                term.casefold() in contract_text for term in terms
            )

    return {
        "name": manifest.get("name", "fix_pipeline_benchmark"),
        "fix_job_id": str(fix_job.id),
        "passed": all(checks.values()),
        "checks": checks,
    }


def _required_probes(
    manifest: Mapping[str, object],
) -> dict[str, list[list[str]]]:
    value = manifest.get("required_probes")
    if not isinstance(value, dict):
        raise ValueError("Benchmark manifest requires a required_probes object")
    result: dict[str, list[list[str]]] = {}
    for probe_id, groups in value.items():
        if not isinstance(probe_id, str) or not isinstance(groups, list):
            raise ValueError("Invalid required probe contract")
        result[probe_id] = [
            [str(term) for term in group] for group in groups if isinstance(group, list)
        ]
    return result
