"""Integration coverage for executable fix contracts across supported runtimes."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixScenarioStatus,
    FixVerificationScenario,
)
from app.services.fix_jobs.pipeline import scenario_testing, verification
from app.services.fix_jobs.pipeline.contracts import (
    FixEnvironmentStatus,
    FixGeneratedTest,
    FixIssueSpec,
    FixProjectEnvironment,
    FixTestFramework,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
VITEST_MODULE = REPOSITORY_ROOT / "frontend" / "node_modules" / "vitest" / "vitest.mjs"


class _HostIntegrationExecutor:
    """Execute only test-owned commands for framework integration coverage."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        completed = subprocess.run(  # noqa: S603 - test-owned argv only
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=timeout_seconds,
        )
        return completed.returncode, completed.stdout, completed.stderr, 1


@pytest.mark.asyncio
async def test_pytest_contract_ignores_unchanged_related_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    source_path = tmp_path / "app.py"
    source_path.write_text("is_safe = False\nvalue = 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "import app\n\ndef test_preexisting_failure():\n    assert app.value == 999\n",
        encoding="utf-8",
    )
    plan = _plan(issue_id, "app.py")
    environment = FixProjectEnvironment(
        project_root=tmp_path,
        framework=FixTestFramework.PYTEST,
        status=FixEnvironmentStatus.READY,
        runner_command=(sys.executable, "-m", "pytest"),
    )
    _mock_verification_dependencies(
        monkeypatch,
        project_root=tmp_path,
        environment=environment,
        generated_tests=[
            _generated_test(
                issue_id,
                "exploit",
                "import app\n\ndef test_exploit():\n    assert app.is_safe\n",
            ),
            _generated_test(
                issue_id,
                "positive",
                "import app\n\ndef test_positive():\n    assert app.value == 1\n",
            ),
        ],
    )

    context = await scenario_testing.prepare_fix_verification(
        sandbox_path=tmp_path,
        specs=[_spec(issue_id, "app.py")],
        plans=[plan],
        timeout_seconds=15,
        executor=_HostIntegrationExecutor(),
    )
    try:
        source_path.write_text("is_safe = True\nvalue = 1\n", encoding="utf-8")
        patched = scenario_testing.run_patched_verification(
            sandbox_path=tmp_path,
            plans=[plan],
            context=context,
            timeout_seconds=15,
            executor=_HostIntegrationExecutor(),
        )[issue_id]
        related = next(
            result for result in patched if result.kind == FixScenarioKind.RELATED_TEST
        )
        assert related.baseline_status == FixScenarioStatus.FAILED
        assert related.patched_status == FixScenarioStatus.FAILED

        verdict = verification._enforce_scenario_contract(
            _semantic_result(issue_id),
            patched,
        )
        assert verdict.verdict == FixIssueVerdict.FIXED
    finally:
        scenario_testing.cleanup_temporary_tests(context)


@pytest.mark.asyncio
async def test_vitest_contract_detects_positive_behavior_regression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node_executable = shutil.which("node")
    if node_executable is None or not VITEST_MODULE.is_file():
        pytest.skip("The repository Vitest runtime is unavailable")

    issue_id = uuid4()
    source_path = tmp_path / "app.js"
    source_path.write_text(
        "export const isSafe = false;\nexport const value = 1;\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (tmp_path / "app.test.js").write_text(
        "import { value } from './app.js';\n"
        "test('related behavior', () => expect(value).toBe(1));\n",
        encoding="utf-8",
    )
    plan = _plan(issue_id, "app.js")
    environment = FixProjectEnvironment(
        project_root=tmp_path,
        framework=FixTestFramework.VITEST,
        status=FixEnvironmentStatus.READY,
        runner_command=(node_executable, str(VITEST_MODULE), "--globals"),
    )
    _mock_verification_dependencies(
        monkeypatch,
        project_root=tmp_path,
        environment=environment,
        generated_tests=[
            _generated_test(
                issue_id,
                "exploit",
                "import { isSafe } from '../../app.js';\n"
                "test('exploit', () => expect(isSafe).toBe(true));\n",
            ),
            _generated_test(
                issue_id,
                "positive",
                "import { value } from '../../app.js';\n"
                "test('positive', () => expect(value).toBe(1));\n",
            ),
        ],
    )

    context = await scenario_testing.prepare_fix_verification(
        sandbox_path=tmp_path,
        specs=[_spec(issue_id, "app.js")],
        plans=[plan],
        timeout_seconds=30,
        executor=_HostIntegrationExecutor(),
    )
    try:
        source_path.write_text(
            "export const isSafe = true;\nexport const value = 2;\n",
            encoding="utf-8",
        )
        patched = scenario_testing.run_patched_verification(
            sandbox_path=tmp_path,
            plans=[plan],
            context=context,
            timeout_seconds=30,
            executor=_HostIntegrationExecutor(),
        )[issue_id]
        positive = next(
            result for result in patched if result.scenario_id == "positive"
        )
        assert positive.baseline_status == FixScenarioStatus.PASSED
        assert positive.patched_status == FixScenarioStatus.FAILED

        verdict = verification._enforce_scenario_contract(
            _semantic_result(issue_id),
            patched,
        )
        assert verdict.verdict == FixIssueVerdict.UNRESOLVED
    finally:
        scenario_testing.cleanup_temporary_tests(context)


def _mock_verification_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_root: Path,
    environment: FixProjectEnvironment,
    generated_tests: list[FixGeneratedTest],
) -> None:
    monkeypatch.setattr(
        scenario_testing,
        "prepare_project_environments",
        lambda **_kwargs: {project_root: environment},
    )

    async def request_tests_with_contract(**_kwargs: object) -> list[FixGeneratedTest]:
        return generated_tests

    monkeypatch.setattr(
        scenario_testing,
        "_request_tests_with_contract",
        request_tests_with_contract,
    )


def _plan(issue_id: UUID, file_path: str) -> FixIssuePlan:
    return FixIssuePlan(
        issue_id=issue_id,
        probe_id="custom.behavior",
        root_cause="Unsafe public behavior",
        safety_property="The exploit is blocked without breaking valid behavior",
        editable_files=[file_path],
        context_files=[file_path],
        affected_contracts=["public application behavior"],
        exploit_scenarios=[
            FixVerificationScenario(
                scenario_id="exploit",
                kind=FixScenarioKind.EXPLOIT,
                description="The vulnerable behavior is reproduced and blocked.",
                related_files=[file_path],
            )
        ],
        preserved_behavior_scenarios=[
            FixVerificationScenario(
                scenario_id="positive",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                description="The valid behavior remains available.",
                related_files=[file_path],
            )
        ],
        status=FixIssuePlanStatus.PLANNED,
    )


def _spec(issue_id: UUID, file_path: str) -> FixIssueSpec:
    return FixIssueSpec(
        issue_id=issue_id,
        probe_id="custom.behavior",
        file_path=file_path,
        line_start=1,
        line_end=1,
        severity="high",
        category="security",
        source="ai_review",
        title="Unsafe behavior",
        description="Unsafe behavior remains observable.",
        source_files={file_path: "source"},
    )


def _generated_test(issue_id: UUID, scenario_id: str, content: str) -> FixGeneratedTest:
    return FixGeneratedTest(
        issue_id=issue_id,
        scenario_id=scenario_id,
        content=content,
    )


def _semantic_result(issue_id: UUID) -> FixIssueResult:
    return FixIssueResult(
        issue_id=issue_id,
        probe_id="custom.behavior",
        verdict=FixIssueVerdict.FIXED,
        summary="Supplemental semantic checks passed.",
    )
