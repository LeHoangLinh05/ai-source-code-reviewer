"""Tests for fix pipeline orchestration helpers."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.models.fix_job import FixJob, FixValidationStatus
from app.models.review_issue import ReviewIssue
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixValidationResult,
    FixVerificationScenario,
)
from app.services.fix_pipeline import service as fix_pipeline_service
from app.services.fix_pipeline.contracts import FixIssueSpec
from app.services.fix_pipeline.execution import FixCommandExecutor
from app.services.fix_pipeline.service import FixPipelineService


@pytest.mark.asyncio
async def test_logic_review_repairs_without_running_runtime_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    repair_calls: list[list[str]] = []

    async def verify_fix_issues(**kwargs: object) -> list[FixIssueResult]:
        assert kwargs["scenario_results_by_issue"] is None
        attempt = cast(int, kwargs["attempt"])
        verdict = FixIssueVerdict.UNRESOLVED if attempt == 1 else FixIssueVerdict.FIXED
        return [
            FixIssueResult(
                issue_id=issue_id,
                probe_id="security.mass_assignment",
                verdict=verdict,
                summary="verification",
                planned_files=["src/app.py"],
                changed_files=["src/app.py"],
                verification_attempts=attempt,
            )
        ]

    async def repair_fix_failures(
        *,
        sandbox_path: Path,
        specs: list[FixIssueSpec],
        plans: list[FixIssuePlan],
        issue_results: list[FixIssueResult],
        validation_result: FixValidationResult | None,
    ) -> bool:
        assert validation_result is None
        del sandbox_path, specs, plans, issue_results, validation_result
        repair_calls.append(["src/app.py"])
        return True

    async def publish_fix_job_progress(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(
        fix_pipeline_service,
        "repair_fix_failures",
        repair_fix_failures,
    )
    monkeypatch.setattr(
        fix_pipeline_service,
        "verify_fix_issues",
        verify_fix_issues,
    )
    monkeypatch.setattr(
        fix_pipeline_service,
        "get_changed_files",
        lambda _sandbox_path: ["src/app.py", "src/auth.py"],
    )
    monkeypatch.setattr(
        fix_pipeline_service,
        "publish_fix_job_progress",
        publish_fix_job_progress,
    )
    service = FixPipelineService(
        settings=cast(
            Settings,
            SimpleNamespace(analysis_subprocess_timeout_seconds=30),
        ),
        fix_job_repository=cast(FixJobRepository, object()),
        report_repository=cast(ReportRepository, object()),
        executor=cast(FixCommandExecutor, object()),
    )

    result, changed_files, issue_results = await service._review_and_repair_patch(
        fix_job=cast(FixJob, SimpleNamespace(id=uuid4())),
        sandbox_path=tmp_path,
        changed_files=["src/app.py"],
        issues=cast(list[ReviewIssue], []),
        specs=[
            FixIssueSpec(
                issue_id=issue_id,
                probe_id="security.mass_assignment",
                file_path="src/app.py",
                line_start=1,
                line_end=1,
                severity="high",
                category="security",
                source="ai_review",
                title="Finding",
                description="Description",
                source_files={"src/app.py": "value = 1\n"},
            )
        ],
        plans=[
            FixIssuePlan(
                issue_id=issue_id,
                probe_id="security.mass_assignment",
                root_cause="Root cause",
                safety_property="Safety property",
                editable_files=["src/app.py"],
                context_files=["src/app.py"],
                affected_contracts=["request contract"],
                exploit_scenarios=[
                    FixVerificationScenario(
                        scenario_id="exploit",
                        kind=FixScenarioKind.EXPLOIT,
                        description="Exploit is rejected.",
                        related_files=["src/app.py"],
                    )
                ],
                preserved_behavior_scenarios=[
                    FixVerificationScenario(
                        scenario_id="positive",
                        kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                        description="Valid request succeeds.",
                        related_files=["src/app.py"],
                    )
                ],
                acceptance_checks=["Check"],
                forbidden_shortcuts=[],
                status=FixIssuePlanStatus.PLANNED,
            )
        ],
    )

    assert result.status == FixValidationStatus.NOT_RUN
    assert "logic consistency only" in result.summary
    assert result.checks == []
    assert changed_files == ["src/app.py", "src/auth.py"]
    assert repair_calls == [["src/app.py"]]
    assert issue_results[0].verdict == FixIssueVerdict.FIXED
