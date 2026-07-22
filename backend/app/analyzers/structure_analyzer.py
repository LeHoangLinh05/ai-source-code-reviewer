"""Project structure, language, framework, and file tree analysis."""

import json
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.analyzers.file_filter import to_relative_posix_path

LANGUAGE_BY_EXTENSION = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


@dataclass(frozen=True, slots=True)
class StructureAnalysisResult:
    """Project structure details stored in MongoDB and report metadata."""

    project_structure: dict[str, object]
    file_tree: list[dict[str, object]]


def analyze_structure(
    sandbox_path: Path,
    filtered_files: list[Path],
) -> StructureAnalysisResult:
    """Detect languages/frameworks and build a nested file tree."""

    relative_paths = [
        to_relative_posix_path(file_path, sandbox_path) for file_path in filtered_files
    ]
    language_counts = _count_languages(filtered_files)
    frameworks = _detect_frameworks(sandbox_path)
    file_tree = _build_nested_file_tree(relative_paths)

    return StructureAnalysisResult(
        project_structure={
            "languages": dict(language_counts),
            "primary_language": _get_primary_language(language_counts),
            "frameworks": frameworks,
            "total_files": len(filtered_files),
        },
        file_tree=file_tree,
    )


def _count_languages(filtered_files: list[Path]) -> Counter[str]:
    language_counts: Counter[str] = Counter()
    for file_path in filtered_files:
        language = LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower())
        if language is not None:
            language_counts[language] += 1

    return language_counts


def _get_primary_language(language_counts: Counter[str]) -> str | None:
    if not language_counts:
        return None

    return language_counts.most_common(1)[0][0]


def _detect_frameworks(sandbox_path: Path) -> list[str]:
    frameworks: set[str] = set()
    _detect_node_frameworks(sandbox_path / "package.json", frameworks)
    _detect_python_frameworks_from_requirements(
        sandbox_path / "requirements.txt",
        frameworks,
    )
    _detect_python_frameworks_from_pyproject(
        sandbox_path / "pyproject.toml", frameworks
    )
    return sorted(frameworks)


def _detect_node_frameworks(package_json_path: Path, frameworks: set[str]) -> None:
    if not package_json_path.exists():
        return

    try:
        payload = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        frameworks.add("node")
        return

    dependencies: dict[str, object] = {}
    for section in ("dependencies", "devDependencies"):
        section_dependencies = payload.get(section)
        if isinstance(section_dependencies, dict):
            dependencies.update(section_dependencies)

    frameworks.add("node")
    if "next" in dependencies:
        frameworks.add("nextjs")
    if "react" in dependencies:
        frameworks.add("react")
    if "express" in dependencies:
        frameworks.add("express")


def _detect_python_frameworks_from_requirements(
    requirements_path: Path,
    frameworks: set[str],
) -> None:
    if not requirements_path.exists():
        return

    try:
        content = requirements_path.read_text(encoding="utf-8").lower()
    except OSError:
        return

    _detect_python_framework_names(content, frameworks)


def _detect_python_frameworks_from_pyproject(
    pyproject_path: Path,
    frameworks: set[str],
) -> None:
    if not pyproject_path.exists():
        return

    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return

    content = str(payload).lower()
    _detect_python_framework_names(content, frameworks)


def _detect_python_framework_names(content: str, frameworks: set[str]) -> None:
    frameworks.add("python")
    if "fastapi" in content:
        frameworks.add("fastapi")
    if "django" in content:
        frameworks.add("django")
    if "flask" in content:
        frameworks.add("flask")


def _build_nested_file_tree(relative_paths: list[str]) -> list[dict[str, object]]:
    root: dict[str, dict[str, object]] = {}
    for relative_path in sorted(relative_paths):
        current_level = root
        parts = relative_path.split("/")
        for index, part in enumerate(parts):
            is_file = index == len(parts) - 1
            if part not in current_level:
                current_level[part] = {
                    "name": part,
                    "path": "/".join(parts[: index + 1]),
                    "type": "file" if is_file else "directory",
                    "children": {},
                }
            current_level = current_level[part]["children"]  # type: ignore[assignment]

    return _tree_dict_to_list(root)


def _tree_dict_to_list(nodes: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for node in nodes.values():
        children = node.pop("children")
        if isinstance(children, dict) and children:
            node["children"] = _tree_dict_to_list(children)
        elif node["type"] == "directory":
            node["children"] = []
        entries.append(node)

    return entries
