"""Patch validation helpers for fix jobs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.analyzers.static_analysis.base import run_static_command
from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import (
    FixValidationCheck,
    FixValidationCheckStatus,
    FixValidationResult,
    build_fix_validation_summary,
)

PYTHON_SUFFIXES = {".py"}
NODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
RUFF_COMMAND = "ruff"
PYTEST_COMMAND = "pytest"
NPM_COMMAND = "npm"
PACKAGE_JSON = "package.json"
NODE_MODULES_DIR = "node_modules"
MAX_VALIDATION_OUTPUT_LENGTH = 4_000
COMMAND_NOT_AVAILABLE_EXIT_CODE = 127
COMMAND_TIMEOUT_EXIT_CODE = 124


def validate_fix(
    *,
    sandbox_path: Path,
    changed_files: list[str],
    timeout_seconds: int,
) -> FixValidationResult:
    """Run available validation commands for a generated patch."""

    checks: list[FixValidationCheck] = []
    python_files = [
        file_path
        for file_path in changed_files
        if Path(file_path).suffix in PYTHON_SUFFIXES
    ]
    node_files = [
        file_path
        for file_path in changed_files
        if Path(file_path).suffix in NODE_SUFFIXES
    ]

    if python_files:
        checks.append(
            _run_optional_command(
                name="ruff check",
                command=[RUFF_COMMAND, "check", *python_files],
                sandbox_path=sandbox_path,
                timeout_seconds=timeout_seconds,
            )
        )
        if _has_pytest_project(sandbox_path):
            checks.append(
                _run_optional_command(
                    name="pytest",
                    command=[PYTEST_COMMAND],
                    sandbox_path=sandbox_path,
                    timeout_seconds=timeout_seconds,
                )
            )

    if node_files:
        checks.extend(
            _run_node_validations(
                sandbox_path=sandbox_path,
                timeout_seconds=timeout_seconds,
            )
        )

    status = _summarize_validation(checks)
    return FixValidationResult(
        status=status,
        summary=build_fix_validation_summary(status, checks),
        checks=checks,
    )


def _run_node_validations(
    *,
    sandbox_path: Path,
    timeout_seconds: int,
) -> list[FixValidationCheck]:
    package_path = sandbox_path / PACKAGE_JSON
    if not package_path.exists():
        return []

    if not (sandbox_path / NODE_MODULES_DIR).exists():
        return [
            FixValidationCheck(
                name="npm scripts",
                command="npm run lint/test",
                status=FixValidationCheckStatus.SKIPPED,
                exit_code=None,
                stdout="",
                stderr="node_modules is not present in the fix sandbox",
                duration_ms=0,
            )
        ]

    scripts = _load_package_scripts(package_path)
    checks: list[FixValidationCheck] = []
    if "lint" in scripts:
        checks.append(
            _run_optional_command(
                name="npm run lint",
                command=[NPM_COMMAND, "run", "lint"],
                sandbox_path=sandbox_path,
                timeout_seconds=timeout_seconds,
            )
        )
    if "test" in scripts:
        checks.append(
            _run_optional_command(
                name="npm test",
                command=[NPM_COMMAND, "test"],
                sandbox_path=sandbox_path,
                timeout_seconds=timeout_seconds,
            )
        )

    return checks


def _run_optional_command(
    *,
    name: str,
    command: list[str],
    sandbox_path: Path,
    timeout_seconds: int,
) -> FixValidationCheck:
    executable = command[0]
    if shutil.which(executable) is None:
        return FixValidationCheck(
            name=name,
            command=" ".join(command),
            status=FixValidationCheckStatus.SKIPPED,
            exit_code=None,
            stdout="",
            stderr=f"{executable} is unavailable on PATH",
            duration_ms=0,
        )

    exit_code, stdout, stderr, duration_ms = run_static_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
    )
    status = (
        FixValidationCheckStatus.PASSED
        if exit_code == 0
        else FixValidationCheckStatus.FAILED
    )
    if exit_code == COMMAND_NOT_AVAILABLE_EXIT_CODE:
        status = FixValidationCheckStatus.SKIPPED
    if exit_code == COMMAND_TIMEOUT_EXIT_CODE:
        status = FixValidationCheckStatus.FAILED

    return FixValidationCheck(
        name=name,
        command=" ".join(command),
        status=status,
        exit_code=exit_code,
        stdout=_truncate_output(stdout),
        stderr=_truncate_output(stderr),
        duration_ms=duration_ms,
    )


def _summarize_validation(
    checks: list[FixValidationCheck],
) -> FixValidationStatus:
    statuses = {check.status for check in checks}
    if FixValidationCheckStatus.FAILED in statuses:
        return FixValidationStatus.FAILED
    if FixValidationCheckStatus.PASSED in statuses:
        return FixValidationStatus.PASSED

    return FixValidationStatus.NOT_RUN


def _has_pytest_project(sandbox_path: Path) -> bool:
    return any(
        path.exists()
        for path in [
            sandbox_path / "tests",
            sandbox_path / "pytest.ini",
            sandbox_path / "pyproject.toml",
            sandbox_path / "setup.cfg",
        ]
    )


def _load_package_scripts(package_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _truncate_output(output: str) -> str:
    if len(output) <= MAX_VALIDATION_OUTPUT_LENGTH:
        return output

    return output[-MAX_VALIDATION_OUTPUT_LENGTH:]
