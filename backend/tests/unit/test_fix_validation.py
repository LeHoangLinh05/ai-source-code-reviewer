"""Tests for typed fix validation result construction."""

from pathlib import Path

from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import FixValidationCheckStatus
from app.services.fix_pipeline import validation as fix_validation
from app.services.fix_pipeline.validation import validate_fix


def test_validate_fix_returns_not_run_when_no_commands_apply(tmp_path: Path) -> None:
    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=[],
        timeout_seconds=1,
    )

    assert result.status == FixValidationStatus.NOT_RUN
    assert result.checks == []
    assert result.summary == "No validation commands ran for this patch."


def test_validate_fix_returns_typed_python_checks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")

    def run_static_command(
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        del cwd, timeout_seconds
        if command[0] == fix_validation.RUFF_COMMAND:
            return 0, "ruff ok", "", 12

        return 1, "", "pytest failed", 34

    monkeypatch.setattr(fix_validation.shutil, "which", lambda _name: "tool")
    monkeypatch.setattr(fix_validation, "run_static_command", run_static_command)

    result = validate_fix(
        sandbox_path=tmp_path,
        changed_files=["src/app.py"],
        timeout_seconds=1,
    )

    assert result.status == FixValidationStatus.FAILED
    assert result.summary == "Validation failed: 1 passed, 1 failed, 0 skipped."
    assert [check.status for check in result.checks] == [
        FixValidationCheckStatus.PASSED,
        FixValidationCheckStatus.FAILED,
    ]
    assert result.checks[1].stderr == "pytest failed"


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
    )

    assert result.status == FixValidationStatus.NOT_RUN
    assert result.checks[0].status == FixValidationCheckStatus.SKIPPED
    assert "node_modules" in result.checks[0].stderr
