"""Locked dependency preparation for temporary fix verification tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

from app.schemas.fix_job import FixIssuePlan
from app.services.fix_pipeline.contracts import (
    FixEnvironmentStatus,
    FixProjectEnvironment,
    FixTestFramework,
)
from app.services.fix_pipeline.execution import FixCommandExecutor
from app.services.fix_pipeline.workspace import resolve_repo_file

MAX_COMMAND_OUTPUT_LENGTH = 4_000
PYTHON_MARKERS = (
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements.txt",
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
)
NODE_MARKERS = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "package.json")
PYTHON_RUNTIME_PATHS = (
    ".repoguard-env",
    ".repoguard-env/cache",
    ".repoguard-env/python",
    ".venv",
)
NODE_RUNTIME_PATHS = (
    ".repoguard-env",
    ".repoguard-env/cache",
    "node_modules",
)


def prepare_project_environments(
    *,
    sandbox_path: Path,
    plans: list[FixIssuePlan],
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> dict[Path, FixProjectEnvironment]:
    """Prepare one reproducible environment for each planned target project."""

    projects: dict[Path, str] = {}
    for plan in plans:
        for relative_path in plan.editable_files:
            file_path = resolve_repo_file(sandbox_path, relative_path)
            language = _language_for_path(file_path)
            if language is None:
                continue
            project_root = _find_project_root(
                file_path.parent,
                sandbox_path=sandbox_path.resolve(),
                markers=PYTHON_MARKERS if language == "python" else NODE_MARKERS,
                preferred_root_check=(
                    _has_supported_python_lock
                    if language == "python"
                    else _has_supported_node_lock
                ),
            )
            projects.setdefault(project_root, language)

    return {
        project_root: _prepare_environment(
            project_root=project_root,
            language=language,
            timeout_seconds=timeout_seconds,
            executor=executor,
        )
        for project_root, language in projects.items()
    }


def run_safe_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> tuple[int, str, str, int]:
    """Delegate an allowlisted argv to the configured isolated executor."""

    return executor.run(command, cwd=cwd, timeout_seconds=timeout_seconds)


def _prepare_environment(
    *,
    project_root: Path,
    language: str,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixProjectEnvironment:
    if language == "python":
        unsafe_path = _find_unsafe_runtime_path(project_root, PYTHON_RUNTIME_PATHS)
        if unsafe_path is not None:
            return _unsafe_environment(
                project_root,
                FixTestFramework.PYTEST,
                unsafe_path,
            )
        return _prepare_python_environment(project_root, timeout_seconds, executor)
    framework = _detect_node_framework(project_root / "package.json")
    unsafe_path = _find_unsafe_runtime_path(project_root, NODE_RUNTIME_PATHS)
    if unsafe_path is not None and framework is not None:
        return _unsafe_environment(project_root, framework, unsafe_path)
    return _prepare_node_environment(project_root, timeout_seconds, executor)


def _prepare_python_environment(
    project_root: Path,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixProjectEnvironment:
    framework = FixTestFramework.PYTEST
    uv_lock = project_root / "uv.lock"
    if uv_lock.exists():
        return _run_install(
            project_root=project_root,
            framework=framework,
            executable="uv",
            command=["uv", "sync", "--frozen", "--all-groups", "--no-install-project"],
            runner_command=(_venv_python(project_root / ".venv"), "-m", "pytest"),
            timeout_seconds=timeout_seconds,
            executor=executor,
        )

    if (project_root / "poetry.lock").exists():
        return _run_install(
            project_root=project_root,
            framework=framework,
            executable="poetry",
            command=["poetry", "install", "--no-root", "--sync"],
            runner_command=("poetry", "run", "python", "-m", "pytest"),
            timeout_seconds=timeout_seconds,
            executor=executor,
        )

    if (project_root / "Pipfile.lock").exists():
        return _run_install(
            project_root=project_root,
            framework=framework,
            executable="pipenv",
            command=["pipenv", "sync", "--dev"],
            runner_command=("pipenv", "run", "python", "-m", "pytest"),
            timeout_seconds=timeout_seconds,
            executor=executor,
        )

    requirements_path = _find_pinned_requirements(project_root)
    if requirements_path is None:
        return FixProjectEnvironment(
            project_root=project_root,
            framework=framework,
            status=FixEnvironmentStatus.UNAVAILABLE,
            reason="No supported frozen Python lockfile was found.",
        )

    environment_path = project_root / ".repoguard-env" / "python"
    python_executable = _venv_python(environment_path)
    if not (project_root / python_executable).exists():
        exit_code, _stdout, stderr, _duration_ms = run_safe_command(
            ["python", "-m", "venv", ".repoguard-env/python"],
            cwd=project_root,
            timeout_seconds=timeout_seconds,
            executor=executor,
        )
        if exit_code != 0:
            return _failed_environment(project_root, framework, stderr)

    return _run_install(
        project_root=project_root,
        framework=framework,
        executable=python_executable,
        command=[
            python_executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            requirements_path.relative_to(project_root).as_posix(),
        ],
        runner_command=(python_executable, "-m", "pytest"),
        timeout_seconds=timeout_seconds,
        executor=executor,
    )


def _prepare_node_environment(
    project_root: Path,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixProjectEnvironment:
    framework = _detect_node_framework(project_root / "package.json")
    if framework is None:
        return FixProjectEnvironment(
            project_root=project_root,
            framework=None,
            status=FixEnvironmentStatus.UNAVAILABLE,
            reason="Neither Vitest nor Jest is declared by the target project.",
        )
    if (project_root / "package-lock.json").exists():
        executable = "npm"
        command = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
        runner = _node_runner(framework, executable)
    elif (project_root / "pnpm-lock.yaml").exists():
        executable = "pnpm"
        command = ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]
        runner = _node_runner(framework, executable)
    elif (project_root / "yarn.lock").exists():
        executable = "yarn"
        command = ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"]
        runner = _node_runner(framework, executable)
    else:
        return FixProjectEnvironment(
            project_root=project_root,
            framework=framework,
            status=FixEnvironmentStatus.UNAVAILABLE,
            reason="No supported frozen JavaScript lockfile was found.",
        )

    return _run_install(
        project_root=project_root,
        framework=framework,
        executable=executable,
        command=command,
        runner_command=runner,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )


def _run_install(
    *,
    project_root: Path,
    framework: FixTestFramework,
    executable: str,
    command: list[str],
    runner_command: tuple[str, ...],
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixProjectEnvironment:
    exit_code, stdout, stderr, _duration_ms = run_safe_command(
        command,
        cwd=project_root,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    if exit_code != 0:
        return _failed_environment(project_root, framework, stderr or stdout)
    return FixProjectEnvironment(
        project_root=project_root,
        framework=framework,
        status=FixEnvironmentStatus.READY,
        runner_command=runner_command,
    )


def _failed_environment(
    project_root: Path,
    framework: FixTestFramework,
    reason: str,
) -> FixProjectEnvironment:
    return FixProjectEnvironment(
        project_root=project_root,
        framework=framework,
        status=FixEnvironmentStatus.FAILED,
        reason=_truncate(reason) or "Dependency preparation failed.",
    )


def _unsafe_environment(
    project_root: Path,
    framework: FixTestFramework,
    unsafe_path: Path,
) -> FixProjectEnvironment:
    return FixProjectEnvironment(
        project_root=project_root,
        framework=framework,
        status=FixEnvironmentStatus.UNAVAILABLE,
        reason=(
            "Dependency runtime path must stay inside the project and cannot be a "
            f"symlink: {unsafe_path.relative_to(project_root).as_posix()}"
        ),
    )


def _detect_node_framework(package_path: Path) -> FixTestFramework | None:
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    dependency_names = set(
        _dict_keys(payload.get("dependencies"))
        + _dict_keys(payload.get("devDependencies"))
    )
    scripts = payload.get("scripts")
    script_text = " ".join(str(value) for value in _dict_values(scripts)).casefold()
    if "vitest" in dependency_names or "vitest" in script_text:
        return FixTestFramework.VITEST
    if "jest" in dependency_names or "jest" in script_text:
        return FixTestFramework.JEST
    return None


def _node_runner(
    framework: FixTestFramework,
    package_manager: str,
) -> tuple[str, ...]:
    if package_manager == "npm":
        return ("npm", "exec", "--no", framework.value)
    if package_manager == "pnpm":
        return ("pnpm", "exec", framework.value)
    return ("yarn", framework.value)


def _find_pinned_requirements(project_root: Path) -> Path | None:
    for name in ("requirements-dev.txt", "requirements-test.txt", "requirements.txt"):
        candidate = project_root / name
        if candidate.is_file() and _requirements_are_pinned(candidate):
            return candidate
    return None


def _has_supported_python_lock(project_root: Path) -> bool:
    return (
        any(
            (project_root / lockfile).is_file()
            for lockfile in ("uv.lock", "poetry.lock", "Pipfile.lock")
        )
        or _find_pinned_requirements(project_root) is not None
    )


def _has_supported_node_lock(project_root: Path) -> bool:
    return any(
        (project_root / lockfile).is_file()
        for lockfile in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
    )


def _requirements_are_pinned(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    requirements = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]
    return bool(requirements) and all(
        "==" in requirement
        and not any(operator in requirement for operator in (">=", "<=", "~=", "!="))
        for requirement in requirements
    )


def _find_project_root(
    start_path: Path,
    *,
    sandbox_path: Path,
    markers: Iterable[str],
    preferred_root_check: Callable[[Path], bool] | None = None,
) -> Path:
    current = start_path.resolve()
    sandbox_root = sandbox_path.resolve()
    if not current.is_relative_to(sandbox_root):
        return sandbox_root
    nearest_marker_root: Path | None = None
    while True:
        if preferred_root_check is not None and preferred_root_check(current):
            return current
        if any((current / marker).exists() for marker in markers):
            nearest_marker_root = nearest_marker_root or current
        if current == sandbox_root:
            break
        current = current.parent
    return nearest_marker_root or sandbox_root


def _find_unsafe_runtime_path(
    project_root: Path,
    relative_paths: Iterable[str],
) -> Path | None:
    resolved_root = project_root.resolve()
    for relative_path in relative_paths:
        candidate = project_root / relative_path
        if candidate.is_symlink():
            return candidate
        if candidate.exists() and not candidate.resolve().is_relative_to(resolved_root):
            return candidate
    return None


def _language_for_path(path: Path) -> str | None:
    if path.suffix.lower() == ".py":
        return "python"
    if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        return "node"
    return None


def _venv_python(environment_path: Path) -> str:
    if environment_path.name == ".venv":
        return ".venv/bin/python"
    return ".repoguard-env/python/bin/python"


def _dict_keys(value: object) -> list[str]:
    return list(value) if isinstance(value, dict) else []


def _dict_values(value: object) -> list[object]:
    return list(value.values()) if isinstance(value, dict) else []


def _truncate(value: str | None) -> str:
    return (value or "")[-MAX_COMMAND_OUTPUT_LENGTH:]
