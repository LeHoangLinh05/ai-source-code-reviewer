"""Source file filtering for cloned review sandboxes."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import build_git_subprocess_env, get_git_executable

IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
    ".venv",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".mp3",
    ".mp4",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".so",
    ".wasm",
    ".webp",
    ".zip",
}

DEFAULT_MAX_SOURCE_FILE_SIZE_BYTES = 1_048_576
MAGIC_BYTES_READ_SIZE = 4096


@dataclass(slots=True, frozen=True)
class FileManifest:
    """Source files selected for analysis plus scanner metadata."""

    files: list[Path]
    source: str
    scanned_count: int


def filter_files(
    sandbox_path: Path,
    *,
    max_source_file_size_bytes: int = DEFAULT_MAX_SOURCE_FILE_SIZE_BYTES,
) -> list[Path]:
    """Return source files that are safe and useful for analyzers."""

    return build_file_manifest(
        sandbox_path,
        max_source_file_size_bytes=max_source_file_size_bytes,
    ).files


def build_file_manifest(
    sandbox_path: Path,
    *,
    max_source_file_size_bytes: int = DEFAULT_MAX_SOURCE_FILE_SIZE_BYTES,
) -> FileManifest:
    """Return a Git-aware source-file manifest for cloned review sandboxes."""

    root_path = sandbox_path.resolve()
    candidates = _git_tracked_files(root_path)
    source = "git" if candidates is not None else "walk"
    if candidates is None:
        candidates = _walk_files(root_path)

    filtered_files: list[Path] = []
    for file_path in candidates:
        resolved_path = file_path.resolve()
        if not _is_relative_to(resolved_path, root_path):
            continue

        if not resolved_path.is_file():
            continue

        if _is_in_ignored_directory(resolved_path, root_path):
            continue

        if resolved_path.is_symlink():
            continue

        if resolved_path.stat().st_size > max_source_file_size_bytes:
            continue

        if is_binary_file(resolved_path):
            continue

        filtered_files.append(resolved_path)

    return FileManifest(
        files=sorted(filtered_files, key=lambda path: path.as_posix()),
        source=source,
        scanned_count=len(candidates),
    )


def is_binary_file(file_path: Path) -> bool:
    """Detect binary files by extension and a small null-byte sample."""

    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return True

    try:
        with file_path.open("rb") as source_file:
            sample = source_file.read(MAGIC_BYTES_READ_SIZE)
    except OSError:
        return True

    return b"\0" in sample


def to_relative_posix_path(file_path: Path, sandbox_path: Path) -> str:
    """Return a stable POSIX-style path relative to the sandbox root."""

    return file_path.resolve().relative_to(sandbox_path.resolve()).as_posix()


def normalize_analyzer_file_path(
    file_path: str,
    sandbox_path: Path | None,
) -> str:
    """Normalize absolute or sandbox-relative analyzer output paths."""

    if sandbox_path is None or not file_path:
        return file_path

    path = Path(file_path)
    resolved_path = (
        path.resolve() if path.is_absolute() else (sandbox_path / path).resolve()
    )
    try:
        return resolved_path.relative_to(sandbox_path.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_in_ignored_directory(file_path: Path, root_path: Path) -> bool:
    relative_parts = file_path.resolve().relative_to(root_path).parts
    return any(part in IGNORED_DIRECTORY_NAMES for part in relative_parts[:-1])


def _git_tracked_files(root_path: Path) -> list[Path] | None:
    git_dir = root_path / ".git"
    if not git_dir.exists():
        return None

    try:
        git_executable = get_git_executable()
    except RuntimeError:
        return None

    completed_process = subprocess.run(
        [git_executable, "ls-files", "-z", "--cached"],
        cwd=root_path,
        capture_output=True,
        check=False,
        text=False,
        timeout=15,
        env=build_git_subprocess_env(),
    )
    if completed_process.returncode != 0:
        return None

    raw_paths = completed_process.stdout.split(b"\0")
    return [
        root_path / raw_path.decode("utf-8", errors="ignore")
        for raw_path in raw_paths
        if raw_path
    ]


def _walk_files(root_path: Path) -> list[Path]:
    files: list[Path] = []
    for directory, directory_names, filenames in os.walk(root_path):
        directory_path = Path(directory)
        directory_names[:] = sorted(
            name for name in directory_names if name not in IGNORED_DIRECTORY_NAMES
        )
        for filename in sorted(filenames):
            files.append(directory_path / filename)
    return files


def _is_relative_to(path: Path, root_path: Path) -> bool:
    try:
        path.relative_to(root_path)
    except ValueError:
        return False
    return True
