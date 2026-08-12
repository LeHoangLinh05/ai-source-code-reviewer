"""Patch validation helpers for fix jobs."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.fix_job import FixValidationStatus
from app.schemas.fix_job import (
    FixValidationCheck,
    FixValidationCheckKind,
    FixValidationCheckStatus,
    FixValidationResult,
    build_fix_validation_summary,
)
from app.services.fix_pipeline.dependencies import run_safe_command
from app.services.fix_pipeline.execution import FixCommandExecutor

PYTHON_SUFFIXES = {".py"}
NODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
RUFF_COMMAND = "ruff"
NPM_COMMAND = "npm"
PACKAGE_JSON = "package.json"
NODE_MODULES_DIR = "node_modules"
YARN_PNP_FILES = (".pnp.cjs", ".pnp.js")
MAX_VALIDATION_OUTPUT_LENGTH = 4_000
COMMAND_NOT_AVAILABLE_EXIT_CODE = 127
COMMAND_TIMEOUT_EXIT_CODE = 124


def validate_fix(
    *,
    sandbox_path: Path,
    changed_files: list[str],
    timeout_seconds: int,
    executor: FixCommandExecutor,
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

    python_projects = _group_changed_files_by_project(
        sandbox_path=sandbox_path,
        changed_files=python_files,
        markers=("pyproject.toml", "pytest.ini", "setup.cfg", "tests"),
    )
    for project_path, project_files in python_projects.items():
        checks.append(
            _run_optional_command(
                name="ruff check",
                command=[RUFF_COMMAND, "check", *project_files],
                kind=FixValidationCheckKind.LINT,
                sandbox_path=project_path,
                timeout_seconds=timeout_seconds,
                executor=executor,
            )
        )

    node_projects = _group_changed_files_by_project(
        sandbox_path=sandbox_path,
        changed_files=node_files,
        markers=(PACKAGE_JSON,),
    )
    for project_path in node_projects:
        checks.extend(
            _run_node_validations(
                sandbox_path=project_path,
                timeout_seconds=timeout_seconds,
                executor=executor,
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
    executor: FixCommandExecutor,
) -> list[FixValidationCheck]:
    package_path = sandbox_path / PACKAGE_JSON
    if not package_path.exists():
        return []

    if not (sandbox_path / NODE_MODULES_DIR).exists() and not any(
        (sandbox_path / file_name).exists() for file_name in YARN_PNP_FILES
    ):
        return [
            FixValidationCheck(
                name="npm scripts",
                command="npm run lint/test",
                kind=FixValidationCheckKind.COMMAND,
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
        command = _node_lint_command(sandbox_path)
        checks.append(
            _run_optional_command(
                name="frontend lint",
                command=command,
                kind=FixValidationCheckKind.LINT,
                sandbox_path=sandbox_path,
                timeout_seconds=timeout_seconds,
                executor=executor,
            )
        )
    return checks


def _run_optional_command(
    *,
    name: str,
    command: list[str],
    kind: FixValidationCheckKind = FixValidationCheckKind.COMMAND,
    sandbox_path: Path,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixValidationCheck:
    exit_code, stdout, stderr, duration_ms = run_safe_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    status = (
        FixValidationCheckStatus.PASSED
        if exit_code == 0
        else FixValidationCheckStatus.FAILED
    )
    if exit_code == COMMAND_TIMEOUT_EXIT_CODE:
        status = FixValidationCheckStatus.FAILED

    return FixValidationCheck(
        name=name,
        command=" ".join(command),
        kind=kind,
        status=status,
        exit_code=exit_code,
        stdout=_truncate_output(stdout),
        stderr=_truncate_output(stderr),
        duration_ms=duration_ms,
    )


def _node_lint_command(project_path: Path) -> list[str]:
    if (project_path / "pnpm-lock.yaml").exists():
        return ["pnpm", "run", "lint"]
    if (project_path / "yarn.lock").exists():
        return ["yarn", "run", "lint"]
    return [NPM_COMMAND, "run", "lint"]


def _summarize_validation(
    checks: list[FixValidationCheck],
) -> FixValidationStatus:
    statuses = {check.status for check in checks}
    if FixValidationCheckStatus.FAILED in statuses:
        return FixValidationStatus.FAILED
    if FixValidationCheckStatus.PASSED in statuses:
        return FixValidationStatus.PASSED

    return FixValidationStatus.NOT_RUN


def _group_changed_files_by_project(
    *,
    sandbox_path: Path,
    changed_files: list[str],
    markers: tuple[str, ...],
) -> dict[Path, list[str]]:
    projects: dict[Path, list[str]] = {}
    resolved_root = sandbox_path.resolve()
    for changed_file in changed_files:
        file_path = (sandbox_path / changed_file).resolve()
        if resolved_root not in file_path.parents:
            continue
        project_path = _find_project_root(
            file_path.parent,
            sandbox_path=resolved_root,
            markers=markers,
        )
        relative_path = file_path.relative_to(project_path).as_posix()
        projects.setdefault(project_path, []).append(relative_path)
    return projects


def _find_project_root(
    start_path: Path,
    *,
    sandbox_path: Path,
    markers: tuple[str, ...],
) -> Path:
    current_path = start_path
    while current_path != sandbox_path:
        if any((current_path / marker).exists() for marker in markers):
            return current_path
        current_path = current_path.parent
    return sandbox_path


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
