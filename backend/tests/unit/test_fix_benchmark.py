"""Tests for the executable fix-pipeline benchmark gate."""

from types import SimpleNamespace
from uuid import uuid4

from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixScenarioResult,
    FixScenarioStatus,
    FixVerificationScenario,
)
from benchmarks.fix_pipeline.evaluator import evaluate_fix_job


def test_fix_benchmark_requires_contract_and_executable_evidence() -> None:
    issue_id = uuid4()
    probe_id = "security.mass_assignment"
    exploit = FixVerificationScenario(
        scenario_id="exploit",
        kind=FixScenarioKind.EXPLOIT,
        description="Public cost_price assignment is rejected.",
    )
    positive = FixVerificationScenario(
        scenario_id="positive",
        kind=FixScenarioKind.PRESERVED_BEHAVIOR,
        description="Admin create with cost_price remains available.",
    )
    plan = FixIssuePlan(
        issue_id=issue_id,
        probe_id=probe_id,
        root_cause="Public schema accepts a sensitive field.",
        safety_property="Only admins assign cost_price.",
        editable_files=["schemas.py"],
        context_files=["schemas.py"],
        affected_contracts=["admin create contract"],
        exploit_scenarios=[exploit],
        preserved_behavior_scenarios=[positive],
        status=FixIssuePlanStatus.PLANNED,
    )
    result = FixIssueResult(
        issue_id=issue_id,
        probe_id=probe_id,
        verdict=FixIssueVerdict.FIXED,
        summary="Verified",
        scenario_results=[
            FixScenarioResult(
                scenario_id="exploit",
                kind=FixScenarioKind.EXPLOIT,
                framework="pytest",
                baseline_status=FixScenarioStatus.FAILED,
                patched_status=FixScenarioStatus.PASSED,
            ),
            FixScenarioResult(
                scenario_id="positive",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                framework="pytest",
                baseline_status=FixScenarioStatus.PASSED,
                patched_status=FixScenarioStatus.PASSED,
            ),
        ],
    )
    fix_job = SimpleNamespace(
        id=uuid4(),
        validation_status=FixValidationStatus.PASSED,
        publish_allow_failed_validation=False,
        diff="diff",
        changed_files=["schemas.py"],
        issue_plan=[plan.model_dump(mode="json")],
        issue_results=[result.model_dump(mode="json")],
    )
    manifest = {
        "name": "fixture",
        "required_probes": {
            probe_id: [["admin", "create"], ["cost_price"]],
        },
    }

    evaluation = evaluate_fix_job(fix_job, manifest)

    assert evaluation["passed"] is True

    result.scenario_results[1].patched_status = FixScenarioStatus.FAILED
    fix_job.issue_results = [result.model_dump(mode="json")]
    regression = evaluate_fix_job(fix_job, manifest)
    assert regression["passed"] is False
