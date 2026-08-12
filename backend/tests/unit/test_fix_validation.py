"""Tests for typed fix validation result construction."""

from pathlib import Path

from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import FixValidationCheckStatus
from app.services.fix_pipeline import validation as fix_validation
from app.services.fix_pipeline.validation import validate_fix


class _RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[tuple[list[str], Path]] = []

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        del timeout_seconds
        self.commands.append((command, cwd))
        return 0, "ok", "", 1


def test_validate_fix_returns_not_run_when_no_commands_apply(tmp_path: Path) -> None:
    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=[],
        timeout_seconds=1,
        executor=_RecordingExecutor(),
    )

    assert result.status == FixValidationStatus.NOT_RUN
    assert result.checks == []
    assert result.summary == "No validation commands ran for this patch."


def test_validate_fix_returns_typed_python_checks(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=["src/app.py"],
        timeout_seconds=1,
        executor=_RecordingExecutor(),
    )

    assert result.status == FixValidationStatus.PASSED
    assert result.summary == "Validation passed: 1 passed, 0 failed, 0 skipped."
    assert [check.status for check in result.checks] == [
        FixValidationCheckStatus.PASSED,
    ]


def test_validate_fix_marks_node_checks_skipped_without_dependencies(
    tmp_path: Path,
) -> None:
    (tmp_path / fix_validation.PACKAGE_JSON).write_text(
        '{"scripts":{"lint":"eslint .","test":"vitest run"}}',
    )

    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=["src/app.ts"],
        timeout_seconds=1,
        executor=_RecordingExecutor(),
    )

    assert result.status == FixValidationStatus.NOT_RUN
    assert result.checks[0].status == FixValidationCheckStatus.SKIPPED
    assert "node_modules" in result.checks[0].stderr


def test_validate_fix_discovers_nested_python_and_node_projects(tmp_path: Path) -> None:
    backend_path = tmp_path / "backend"
    frontend_path = tmp_path / "frontend"
    backend_path.mkdir()
    frontend_path.mkdir()
    (backend_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )
    (frontend_path / "package.json").write_text(
        '{"scripts":{"lint":"eslint ."}}',
        encoding="utf-8",
    )
    (frontend_path / "node_modules").mkdir()
    executor = _RecordingExecutor()

    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=["backend/app/api.py", "frontend/src/page.tsx"],
        timeout_seconds=1,
        executor=executor,
    )

    assert result.status == FixValidationStatus.PASSED
    assert (["ruff", "check", "app/api.py"], backend_path) in executor.commands
    assert (["npm", "run", "lint"], frontend_path) in executor.commands
