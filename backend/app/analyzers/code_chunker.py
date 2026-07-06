"""AST-based code chunking for downstream AI review workflows."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

MAX_TOKENS_PER_CHUNK = 1500
FALLBACK_CHUNK_LINES = 60
FALLBACK_OVERLAP_LINES = 10

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SECURITY_IMPORTS = {"jwt", "bcrypt", "hashlib", "cryptography", "secrets"}
_DATABASE_IMPORTS = {"sqlalchemy", "pymongo"}
_SECURITY_DIRS = {"auth", "security", "crypto", "middleware"}
_DATABASE_DIRS = {"db", "models", "repositories"}
_API_DIRS = {"routers", "api", "endpoints"}
_CONFIG_FILENAMES = {"config.py", "settings.py", ".env"}


@dataclass(slots=True)
class CodeChunkMetadata:
    """Structured metadata attached to one source code chunk."""

    file_path: str
    language: str
    chunk_type: str
    chunk_index: int
    total_chunks: int
    function_name: str | None
    class_name: str | None
    line_start: int
    line_end: int
    imports: list[str]
    module: str
    risk_area: str
    has_static_issues: bool
    token_count: int


@dataclass(slots=True)
class CodeChunk:
    """One chunk of code plus the metadata needed by repository retrieval."""

    content: str
    metadata: CodeChunkMetadata


@dataclass(slots=True)
class StaticIssueRange:
    """Minimal static issue range used to mark chunks with known findings."""

    file_path: str | None
    line_start: int | None
    line_end: int | None = None


@dataclass(slots=True)
class _ChunkCandidate:
    chunk_type: str
    line_start: int
    line_end: int
    function_name: str | None = None
    class_name: str | None = None


def chunk_python_file(
    file_path: Path,
    *,
    project_root: Path | None = None,
    static_issues: Iterable[StaticIssueRange | object] | None = None,
) -> list[CodeChunk]:
    """Split one Python file into AST class/function chunks with metadata."""

    source = file_path.read_text(encoding="utf-8", errors="ignore")
    display_path = _display_path(file_path, project_root)
    return chunk_python_source(
        source,
        file_path=display_path,
        static_issues=static_issues,
    )


def chunk_python_source(
    source: str,
    *,
    file_path: str,
    static_issues: Iterable[StaticIssueRange | object] | None = None,
) -> list[CodeChunk]:
    """Split Python source into semantic chunks, falling back to line windows."""

    lines = source.splitlines()
    if not lines:
        return []

    imports = _extract_imports_from_source(source)
    candidates = _build_ast_candidates(source, len(lines))
    if not candidates:
        candidates = [
            _ChunkCandidate(
                chunk_type="module",
                line_start=line_start,
                line_end=line_end,
            )
            for line_start, line_end in _line_windows(len(lines))
        ]

    expanded_candidates = _enforce_token_limit(candidates, lines)
    chunks = [
        _build_chunk(
            candidate,
            lines,
            file_path=file_path,
            imports=imports,
            static_issues=static_issues,
        )
        for candidate in expanded_candidates
    ]
    total_chunks = len(chunks)
    for chunk_index, chunk in enumerate(chunks):
        chunk.metadata.chunk_index = chunk_index
        chunk.metadata.total_chunks = total_chunks

    return chunks


def detect_risk_area(file_path: str, imports: Iterable[str]) -> str:
    """Classify a file into the risk areas defined by the AI flow spec."""

    path = Path(file_path.replace("\\", "/"))
    normalized_parts = {part.lower() for part in path.parts}
    filename = path.name.lower()
    import_roots = {
        imported_name.split(".", maxsplit=1)[0].lower() for imported_name in imports
    }

    if filename in _CONFIG_FILENAMES:
        return "config"

    if normalized_parts & _SECURITY_DIRS or import_roots & _SECURITY_IMPORTS:
        return "security"

    if normalized_parts & _DATABASE_DIRS or import_roots & _DATABASE_IMPORTS:
        return "database"

    if normalized_parts & _API_DIRS:
        return "api"

    return "general"


def count_tokens(text: str) -> int:
    """Return a lightweight token estimate suitable for chunk size limits."""

    return len(_TOKEN_PATTERN.findall(text))


def _build_ast_candidates(source: str, line_count: int) -> list[_ChunkCandidate]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    candidates: list[_ChunkCandidate] = []
    covered_ranges: list[tuple[int, int]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_start, class_end = _node_range(node)
            candidates.append(
                _ChunkCandidate(
                    chunk_type="class",
                    line_start=class_start,
                    line_end=class_end,
                    class_name=node.name,
                )
            )
            covered_ranges.append((class_start, class_end))
            candidates.extend(_function_candidates(node.body, class_name=node.name))
            continue

        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function_start, function_end = _node_range(node)
            candidates.append(
                _ChunkCandidate(
                    chunk_type="function",
                    line_start=function_start,
                    line_end=function_end,
                    function_name=node.name,
                )
            )
            covered_ranges.append((function_start, function_end))

    candidates.extend(_module_candidates(line_count, covered_ranges))
    return sorted(candidates, key=lambda candidate: (candidate.line_start, candidate.line_end))


def _function_candidates(
    nodes: list[ast.stmt],
    *,
    class_name: str | None,
) -> list[_ChunkCandidate]:
    candidates: list[_ChunkCandidate] = []
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            line_start, line_end = _node_range(node)
            candidates.append(
                _ChunkCandidate(
                    chunk_type="function",
                    line_start=line_start,
                    line_end=line_end,
                    function_name=node.name,
                    class_name=class_name,
                )
            )
    return candidates


def _module_candidates(
    line_count: int,
    covered_ranges: list[tuple[int, int]],
) -> list[_ChunkCandidate]:
    module_candidates: list[_ChunkCandidate] = []
    current_start: int | None = None

    for line_number in range(1, line_count + 1):
        is_covered = any(start <= line_number <= end for start, end in covered_ranges)
        if is_covered:
            if current_start is not None:
                module_candidates.append(
                    _ChunkCandidate("module", current_start, line_number - 1)
                )
                current_start = None
            continue

        if current_start is None:
            current_start = line_number

    if current_start is not None:
        module_candidates.append(_ChunkCandidate("module", current_start, line_count))

    return [
        candidate
        for candidate in module_candidates
        if candidate.line_start <= candidate.line_end
    ]


def _enforce_token_limit(
    candidates: list[_ChunkCandidate],
    lines: list[str],
) -> list[_ChunkCandidate]:
    expanded_candidates: list[_ChunkCandidate] = []
    for candidate in candidates:
        content = _slice_lines(lines, candidate.line_start, candidate.line_end)
        if count_tokens(content) <= MAX_TOKENS_PER_CHUNK:
            expanded_candidates.append(candidate)
            continue

        for line_start, line_end in _line_windows(
            candidate.line_end - candidate.line_start + 1,
            offset=candidate.line_start - 1,
        ):
            expanded_candidates.append(
                _ChunkCandidate(
                    chunk_type=candidate.chunk_type,
                    line_start=line_start,
                    line_end=line_end,
                    function_name=candidate.function_name,
                    class_name=candidate.class_name,
                )
            )

    return expanded_candidates


def _build_chunk(
    candidate: _ChunkCandidate,
    lines: list[str],
    *,
    file_path: str,
    imports: list[str],
    static_issues: Iterable[StaticIssueRange | object] | None,
) -> CodeChunk:
    content = _slice_lines(lines, candidate.line_start, candidate.line_end)
    metadata = CodeChunkMetadata(
        file_path=file_path,
        language="python",
        chunk_type=candidate.chunk_type,
        chunk_index=0,
        total_chunks=1,
        function_name=candidate.function_name,
        class_name=candidate.class_name,
        line_start=candidate.line_start,
        line_end=candidate.line_end,
        imports=imports,
        module=_module_name(file_path),
        risk_area=detect_risk_area(file_path, imports),
        has_static_issues=_has_static_issue(candidate, file_path, static_issues),
        token_count=count_tokens(content),
    )
    return CodeChunk(content=content, metadata=metadata)


def _line_windows(
    line_count: int,
    *,
    offset: int = 0,
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    start = 1
    while start <= line_count:
        end = min(line_count, start + FALLBACK_CHUNK_LINES - 1)
        windows.append((start + offset, end + offset))
        if end == line_count:
            break
        start = end - FALLBACK_OVERLAP_LINES + 1

    return windows


def _extract_imports_from_source(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
            continue

        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    return sorted(set(imports))


def _node_range(node: ast.AST) -> tuple[int, int]:
    line_start = getattr(node, "lineno", 1)
    line_end = getattr(node, "end_lineno", line_start)
    return line_start, line_end


def _slice_lines(lines: list[str], line_start: int, line_end: int) -> str:
    return "\n".join(lines[line_start - 1 : line_end])


def _module_name(file_path: str) -> str:
    parent = Path(file_path.replace("\\", "/")).parent
    if str(parent) in {"", "."}:
        return "root"

    return parent.name


def _display_path(file_path: Path, project_root: Path | None) -> str:
    if project_root is None:
        return file_path.as_posix()

    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


def _has_static_issue(
    candidate: _ChunkCandidate,
    file_path: str,
    static_issues: Iterable[StaticIssueRange | object] | None,
) -> bool:
    if static_issues is None:
        return False

    for issue in static_issues:
        issue_file_path = getattr(issue, "file_path", None)
        if issue_file_path not in {None, file_path}:
            continue

        line_start = getattr(issue, "line_start", None)
        if line_start is None:
            continue

        line_end = getattr(issue, "line_end", None) or line_start
        if line_start <= candidate.line_end and line_end >= candidate.line_start:
            return True

    return False
