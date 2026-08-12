"""Tests for locked, secret-free fix verification environments."""

from pathlib import Path
from uuid import UUID

import pytest

from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixScenarioKind,
    FixVerificationScenario,
)
from app.services.fix_pipeline import dependencies
from app.services.fix_pipeline.contracts import (
    FixEnvironmentStatus,
    FixTestFramework,
)


class _RecordingExecutor:
    def __init__(
        self,
        result: tuple[int, str, str, int] = (0, "installed", "", 1),
    ) -> None:
        self.result = result
        self.commands: list[list[str]] = []

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        del cwd, timeout_seconds
        self.commands.append(command)
        return self.result


def test_python_environment_requires_a_frozen_lockfile(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\ndependencies=['pytest>=8']\n",
        encoding="utf-8",
    )

    environments = dependencies.prepare_project_environments(
        sandbox_path=tmp_path,
        plans=[_build_plan("app.py")],
        timeout_seconds=1,
        executor=_RecordingExecutor(),
    )

    environment = environments[tmp_path]
    assert environment.status == FixEnvironmentStatus.UNAVAILABLE
    assert "lockfile" in environment.reason


@pytest.mark.parametrize(
    ("lockfile", "expected_command", "expected_runner"),
    [
        (
            "package-lock.json",
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            ("npm", "exec", "--no", "vitest"),
        ),
        (
            "pnpm-lock.yaml",
            ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
            ("pnpm", "exec", "vitest"),
        ),
        (
            "yarn.lock",
            ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"],
            ("yarn", "vitest"),
        ),
    ],
)
def test_node_lock_install_disables_scripts(
    tmp_path: Path,
    lockfile: str,
    expected_command: list[str],
    expected_runner: tuple[str, ...],
) -> None:
    (tmp_path / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"devDependencies":{"vitest":"1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / lockfile).write_text("{}", encoding="utf-8")
    executor = _RecordingExecutor()

    environments = dependencies.prepare_project_environments(
        sandbox_path=tmp_path,
        plans=[_build_plan("app.ts")],
        timeout_seconds=1,
        executor=executor,
    )

    environment = environments[tmp_path]
    assert environment.status == FixEnvironmentStatus.READY
    assert environment.framework == FixTestFramework.VITEST
    assert executor.commands == [expected_command]
    assert environment.runner_command == expected_runner


@pytest.mark.parametrize(
    ("lockfile", "expected_command"),
    [
        (
            "uv.lock",
            [
                "uv",
                "sync",
                "--frozen",
                "--all-groups",
                "--no-install-project",
            ],
        ),
        (
            "poetry.lock",
            ["poetry", "install", "--no-root", "--sync"],
        ),
        (
            "Pipfile.lock",
            ["pipenv", "sync", "--dev"],
        ),
    ],
)
def test_python_lock_install_uses_frozen_package_manager_command(
    tmp_path: Path,
    lockfile: str,
    expected_command: list[str],
) -> None:
    (tmp_path / lockfile).write_text("{}", encoding="utf-8")
    executor = _RecordingExecutor()

    environment = dependencies._prepare_python_environment(tmp_path, 1, executor)

    assert environment.status == FixEnvironmentStatus.READY
    assert environment.framework == FixTestFramework.PYTEST
    assert executor.commands == [expected_command]


def test_safe_command_decodes_utf8_output(tmp_path: Path) -> None:
    executor = _RecordingExecutor((0, "\u2713\n", "", 1))
    exit_code, stdout, stderr, _duration_ms = dependencies.run_safe_command(
        ["python", "-c", "print('\\u2713')"],
        cwd=tmp_path,
        timeout_seconds=5,
        executor=executor,
    )

    assert exit_code == 0
    assert stdout.strip() == "\u2713"
    assert stderr == ""


def test_python_project_root_can_be_defined_by_pinned_requirements(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "service"
    source_directory = project_root / "src"
    source_directory.mkdir(parents=True)
    (project_root / "requirements.txt").write_text(
        "pytest==8.3.5\n",
        encoding="utf-8",
    )

    detected_root = dependencies._find_project_root(
        source_directory,
        sandbox_path=tmp_path.resolve(),
        markers=dependencies.PYTHON_MARKERS,
    )

    assert detected_root == project_root


def test_project_root_prefers_parent_lock_over_nested_manifest(
    tmp_path: Path,
) -> None:
    nested_project = tmp_path / "packages" / "service"
    source_directory = nested_project / "src"
    source_directory.mkdir(parents=True)
    (nested_project / "pyproject.toml").write_text(
        "[project]\nname='service'\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    detected_root = dependencies._find_project_root(
        source_directory,
        sandbox_path=tmp_path,
        markers=dependencies.PYTHON_MARKERS,
        preferred_root_check=dependencies._has_supported_python_lock,
    )

    assert detected_root == tmp_path


def test_python_environment_rejects_symlinked_runtime_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        if path.name == ".venv":
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)

    environment = dependencies._prepare_environment(
        project_root=tmp_path,
        language="python",
        timeout_seconds=1,
        executor=_RecordingExecutor(),
    )

    assert environment.status == FixEnvironmentStatus.UNAVAILABLE
    assert ".venv" in environment.reason


def _build_plan(file_path: str) -> FixIssuePlan:
    return FixIssuePlan(
        issue_id=UUID("00000000-0000-0000-0000-000000000001"),
        root_cause="Unsafe behavior",
        safety_property="The behavior is safe",
        editable_files=[file_path],
        context_files=[file_path],
        affected_contracts=["application behavior"],
        exploit_scenarios=[
            FixVerificationScenario(
                scenario_id="exploit",
                kind=FixScenarioKind.EXPLOIT,
                description="Unsafe behavior is reproduced.",
                related_files=[file_path],
            )
        ],
        preserved_behavior_scenarios=[
            FixVerificationScenario(
                scenario_id="positive",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                description="Normal behavior remains available.",
                related_files=[file_path],
            )
        ],
        status=FixIssuePlanStatus.PLANNED,
    )
