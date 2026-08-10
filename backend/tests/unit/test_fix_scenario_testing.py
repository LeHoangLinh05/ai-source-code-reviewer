"""Behavior tests for temporary baseline and patched fix verification."""

import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixScenarioKind,
    FixScenarioStatus,
    FixVerificationScenario,
)
from app.services.fix_pipeline import scenario_testing
from app.services.fix_pipeline.contracts import (
    FixEnvironmentStatus,
    FixGeneratedTest,
    FixIssueSpec,
    FixProjectEnvironment,
    FixTestFramework,
)
from app.services.fix_pipeline.errors import FixPipelineError


class _HostTestExecutor:
    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        resolved = [sys.executable, *command[1:]]
        completed = subprocess.run(  # noqa: S603 - executes test-owned argv only
            resolved,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        return completed.returncode, completed.stdout, completed.stderr, 1


class _ResultExecutor:
    def __init__(self, result: tuple[int, str, str, int]) -> None:
        self.result = result

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        del command, cwd, timeout_seconds
        return self.result


@pytest.mark.asyncio
async def test_temporary_tests_compare_base_and_patch_and_are_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    app_path = tmp_path / "app.py"
    app_path.write_text("is_safe = False\nvalue = 1\n", encoding="utf-8")
    existing_test = tmp_path / "test_app.py"
    existing_test.write_text(
        "import app\n\ndef test_value():\n    assert app.value == 1\n",
        encoding="utf-8",
    )
    environment = _environment(tmp_path)
    monkeypatch.setattr(
        scenario_testing,
        "prepare_project_environments",
        lambda **_kwargs: {tmp_path: environment},
    )

    async def request_tests_with_contract(**_kwargs: object) -> list[FixGeneratedTest]:
        return [
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="exploit",
                content=(
                    "import app\n\n"
                    "def test_exploit_blocked():\n"
                    "    assert app.is_safe\n"
                ),
            ),
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="positive",
                content="import app\n\ndef test_value():\n    assert app.value == 1\n",
            ),
        ]

    monkeypatch.setattr(
        scenario_testing,
        "_request_tests_with_contract",
        request_tests_with_contract,
    )
    plan = _plan(issue_id)
    context = await scenario_testing.prepare_fix_verification(
        sandbox_path=tmp_path,
        specs=[_spec(issue_id)],
        plans=[plan],
        timeout_seconds=15,
        executor=_HostTestExecutor(),
    )

    baseline = {
        result.scenario_id: result for result in context.baseline_results[issue_id]
    }
    assert baseline["exploit"].baseline_status == FixScenarioStatus.FAILED
    assert baseline["positive"].baseline_status == FixScenarioStatus.PASSED
    assert baseline["related:test_app.py"].baseline_status == FixScenarioStatus.PASSED

    app_path.write_text("is_safe = True\nvalue = 1\n", encoding="utf-8")
    patched = scenario_testing.run_patched_verification(
        sandbox_path=tmp_path,
        plans=[plan],
        context=context,
        timeout_seconds=15,
        executor=_HostTestExecutor(),
    )[issue_id]
    patched_by_id = {result.scenario_id: result for result in patched}
    assert patched_by_id["exploit"].patched_status == FixScenarioStatus.PASSED
    assert patched_by_id["positive"].patched_status == FixScenarioStatus.PASSED
    assert patched_by_id["related:test_app.py"].patched_status == (
        FixScenarioStatus.PASSED
    )

    test_root = tmp_path / scenario_testing.TEMP_TEST_DIRECTORY
    assert test_root.exists()
    scenario_testing.cleanup_temporary_tests(context)
    assert not test_root.exists()


@pytest.mark.asyncio
async def test_changed_temporary_test_is_not_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    (tmp_path / "app.py").write_text(
        "is_safe = False\nvalue = 1\n",
        encoding="utf-8",
    )
    environment = _environment(tmp_path)
    monkeypatch.setattr(
        scenario_testing,
        "prepare_project_environments",
        lambda **_kwargs: {tmp_path: environment},
    )

    async def request_tests_with_contract(**_kwargs: object) -> list[FixGeneratedTest]:
        return [
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="exploit",
                content="def test_exploit():\n    assert False\n",
            ),
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="positive",
                content="def test_positive():\n    assert True\n",
            ),
        ]

    monkeypatch.setattr(
        scenario_testing,
        "_request_tests_with_contract",
        request_tests_with_contract,
    )
    plan = _plan(issue_id)
    context = await scenario_testing.prepare_fix_verification(
        sandbox_path=tmp_path,
        specs=[_spec(issue_id)],
        plans=[plan],
        timeout_seconds=15,
        executor=_HostTestExecutor(),
    )
    context.artifacts[0].file_path.write_text(
        "def test_weakened():\n    assert True\n",
        encoding="utf-8",
    )

    patched = scenario_testing.run_patched_verification(
        sandbox_path=tmp_path,
        plans=[plan],
        context=context,
        timeout_seconds=15,
        executor=_HostTestExecutor(),
    )[issue_id]

    exploit = next(result for result in patched if result.scenario_id == "exploit")
    assert exploit.patched_status == FixScenarioStatus.SKIPPED
    assert "changed after generation" in exploit.output
    scenario_testing.cleanup_temporary_tests(context)


@pytest.mark.asyncio
async def test_temporary_test_cannot_modify_production_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    app_path = tmp_path / "app.py"
    original_source = "is_safe = False\nvalue = 1\n"
    app_path.write_text(original_source, encoding="utf-8")
    environment = _environment(tmp_path)
    monkeypatch.setattr(
        scenario_testing,
        "prepare_project_environments",
        lambda **_kwargs: {tmp_path: environment},
    )

    async def request_tests_with_contract(**_kwargs: object) -> list[FixGeneratedTest]:
        modifying_test = (
            "from pathlib import Path\n\n"
            "def test_exploit():\n"
            "    root = Path(__file__).parents[2]\n"
            "    (root / 'app.py').write_text('is_safe = True\\nvalue = 1\\n')\n"
            "    assert True\n"
        )
        return [
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="exploit",
                content=modifying_test,
            ),
            FixGeneratedTest(
                issue_id=issue_id,
                scenario_id="positive",
                content="def test_positive():\n    assert True\n",
            ),
        ]

    monkeypatch.setattr(
        scenario_testing,
        "_request_tests_with_contract",
        request_tests_with_contract,
    )

    context = await scenario_testing.prepare_fix_verification(
        sandbox_path=tmp_path,
        specs=[_spec(issue_id)],
        plans=[_plan(issue_id)],
        timeout_seconds=15,
        executor=_HostTestExecutor(),
    )

    exploit = next(
        result
        for result in context.baseline_results[issue_id]
        if result.scenario_id == "exploit"
    )
    assert exploit.baseline_status == FixScenarioStatus.SKIPPED
    assert "changes were restored" in exploit.output
    assert app_path.read_text(encoding="utf-8") == original_source
    scenario_testing.cleanup_temporary_tests(context)


def test_test_runner_restores_new_changes_outside_issue_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    (tmp_path / ".git").mkdir()
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    unrelated_path = tmp_path / "unrelated.py"
    unrelated_path.write_text("untouched = True\n", encoding="utf-8")
    tracked_changes = iter([set(), {"unrelated.py"}])
    restored_paths: list[str] = []

    monkeypatch.setattr(
        scenario_testing,
        "_get_tracked_changes",
        lambda _sandbox_path: next(tracked_changes),
    )

    def run_test_path(**_kwargs: object) -> tuple[FixScenarioStatus, str]:
        unrelated_path.write_text("untouched = False\n", encoding="utf-8")
        return FixScenarioStatus.PASSED, "passed"

    def restore_index_files(_sandbox_path: Path, file_paths: list[str]) -> None:
        restored_paths.extend(file_paths)
        unrelated_path.write_text("untouched = True\n", encoding="utf-8")

    monkeypatch.setattr(scenario_testing, "_run_test_path", run_test_path)
    monkeypatch.setattr(
        scenario_testing,
        "restore_index_files",
        restore_index_files,
    )

    status, output = scenario_testing._run_test_with_source_guard(
        path=tmp_path / "generated_test.py",
        environment=_environment(tmp_path),
        sandbox_path=tmp_path,
        plan=_plan(issue_id),
        timeout_seconds=1,
        executor=_HostTestExecutor(),
    )

    assert status == FixScenarioStatus.SKIPPED
    assert "unrelated.py" in output
    assert restored_paths == ["unrelated.py"]
    assert unrelated_path.read_text(encoding="utf-8") == "untouched = True\n"


@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected_status"),
    [
        (1, "AssertionError: exploit remains possible", FixScenarioStatus.FAILED),
        (2, "error while importing test module", FixScenarioStatus.SKIPPED),
        (127, "command not found", FixScenarioStatus.SKIPPED),
    ],
)
def test_runner_does_not_treat_infrastructure_errors_as_exploit_evidence(
    tmp_path: Path,
    exit_code: int,
    stderr: str,
    expected_status: FixScenarioStatus,
) -> None:
    test_path = tmp_path / "test_app.py"
    test_path.write_text("def test_app():\n    assert True\n", encoding="utf-8")
    status, _output = scenario_testing._run_test_path(
        path=test_path,
        environment=_environment(tmp_path),
        timeout_seconds=1,
        executor=_ResultExecutor((exit_code, "", stderr, 1)),
    )

    assert status == expected_status


@pytest.mark.parametrize(
    ("related_file", "expected_suffix"),
    [
        ("src/app.js", ".test.js"),
        ("src/app.jsx", ".test.jsx"),
        ("src/app.ts", ".test.ts"),
        ("src/app.tsx", ".test.tsx"),
    ],
)
def test_javascript_test_suffix_matches_the_affected_source(
    related_file: str,
    expected_suffix: str,
) -> None:
    scenario = FixVerificationScenario(
        scenario_id="positive",
        kind=FixScenarioKind.PRESERVED_BEHAVIOR,
        description="Normal behavior remains available.",
        related_files=[related_file],
    )

    suffix = scenario_testing._test_file_suffix(
        scenario,
        FixTestFramework.VITEST,
    )

    assert suffix == expected_suffix


def test_temporary_test_directory_cannot_be_a_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    scenario = _plan(issue_id).exploit_scenarios[0]
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        if path.name == scenario_testing.TEMP_TEST_DIRECTORY:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)

    with pytest.raises(FixPipelineError, match="cannot be a symlink"):
        scenario_testing._write_test_artifact(
            issue_id=issue_id,
            scenario=scenario,
            generated=FixGeneratedTest(
                issue_id=issue_id,
                scenario_id=scenario.scenario_id,
                content="def test_exploit():\n    assert False\n",
            ),
            environment=_environment(tmp_path),
        )


def test_temporary_test_file_is_removed_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    plan = _plan(issue_id)
    scenario = plan.exploit_scenarios[0]
    artifact = scenario_testing._write_test_artifact(
        issue_id=issue_id,
        scenario=scenario,
        generated=FixGeneratedTest(
            issue_id=issue_id,
            scenario_id=scenario.scenario_id,
            content="def test_exploit():\n    assert False\n",
        ),
        environment=_environment(tmp_path),
    )

    def run_test_with_source_guard(**_kwargs: object) -> None:
        raise RuntimeError("runner failed")

    monkeypatch.setattr(
        scenario_testing,
        "_run_test_with_source_guard",
        run_test_with_source_guard,
    )

    with pytest.raises(RuntimeError, match="runner failed"):
        scenario_testing._run_artifact(
            artifact,
            environment=_environment(tmp_path),
            sandbox_path=tmp_path,
            plan=plan,
            timeout_seconds=1,
            executor=_HostTestExecutor(),
        )

    assert not artifact.file_path.exists()


def _environment(project_root: Path) -> FixProjectEnvironment:
    return FixProjectEnvironment(
        project_root=project_root,
        framework=FixTestFramework.PYTEST,
        status=FixEnvironmentStatus.READY,
        runner_command=(sys.executable, "-m", "pytest"),
    )


def _plan(issue_id: UUID) -> FixIssuePlan:
    return FixIssuePlan(
        issue_id=issue_id,
        probe_id="custom.behavior",
        root_cause="Unsafe state",
        safety_property="Unsafe state is rejected",
        editable_files=["app.py"],
        context_files=["app.py"],
        affected_contracts=["app state"],
        exploit_scenarios=[
            FixVerificationScenario(
                scenario_id="exploit",
                kind=FixScenarioKind.EXPLOIT,
                description="Unsafe state is observable.",
                related_files=["app.py"],
            )
        ],
        preserved_behavior_scenarios=[
            FixVerificationScenario(
                scenario_id="positive",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                description="Value remains available.",
                related_files=["app.py"],
            )
        ],
        status=FixIssuePlanStatus.PLANNED,
    )


def _spec(issue_id: UUID) -> FixIssueSpec:
    return FixIssueSpec(
        issue_id=issue_id,
        probe_id="custom.behavior",
        file_path="app.py",
        line_start=1,
        line_end=1,
        severity="high",
        category="security",
        source="ai_review",
        title="Unsafe behavior",
        description="Unsafe behavior is observable.",
        source_files={"app.py": "is_safe = False\nvalue = 1\n"},
    )
