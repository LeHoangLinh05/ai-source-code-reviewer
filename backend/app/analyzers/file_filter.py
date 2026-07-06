"""Source file filtering for cloned review sandboxes."""

from pathlib import Path

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


def filter_files(
    sandbox_path: Path,
    *,
    max_source_file_size_bytes: int = DEFAULT_MAX_SOURCE_FILE_SIZE_BYTES,
) -> list[Path]:
    """Return source files that are safe and useful for analyzers."""

    root_path = sandbox_path.resolve()
    filtered_files: list[Path] = []
    for file_path in sorted(root_path.rglob("*"), key=lambda path: path.as_posix()):
        if not file_path.is_file():
            continue

        if _is_in_ignored_directory(file_path, root_path):
            continue

        if file_path.stat().st_size > max_source_file_size_bytes:
            continue

        if is_binary_file(file_path):
            continue

        filtered_files.append(file_path)

    return filtered_files


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


def _is_in_ignored_directory(file_path: Path, root_path: Path) -> bool:
    relative_parts = file_path.resolve().relative_to(root_path).parts
    return any(part in IGNORED_DIRECTORY_NAMES for part in relative_parts[:-1])
