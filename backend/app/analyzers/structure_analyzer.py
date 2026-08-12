"""Project structure, language, framework, and file tree analysis."""

import ast
import json
import re
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

PYTHON_FRAMEWORKS = {
    "django": "django",
    "fastapi": "fastapi",
    "flask": "flask",
}
REQUIREMENT_NAME_PATTERN = re.compile(r"^\s*([a-zA-Z0-9_.-]+)")


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
    frameworks = _detect_frameworks(filtered_files)
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


def _detect_frameworks(filtered_files: list[Path]) -> list[str]:
    frameworks: set[str] = set()
    for file_path in filtered_files:
        normalized_name = file_path.name.casefold()
        if normalized_name == "package.json":
            _detect_node_frameworks(file_path, frameworks)
        elif normalized_name == "pyproject.toml":
            _detect_python_frameworks_from_pyproject(file_path, frameworks)
        elif normalized_name.startswith("requirements") and normalized_name.endswith(
            ".txt"
        ):
            _detect_python_frameworks_from_requirements(file_path, frameworks)

    _detect_python_frameworks_from_imports(filtered_files, frameworks)
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

    frameworks.add("python")
    _add_python_frameworks(
        _dependency_names_from_requirement_lines(content),
        frameworks,
    )


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

    frameworks.add("python")
    _add_python_frameworks(_python_dependency_names(payload), frameworks)


def _dependency_names_from_requirement_lines(content: str) -> set[str]:
    dependencies: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://", "git+")):
            continue
        match = REQUIREMENT_NAME_PATTERN.match(line)
        if match is not None:
            dependencies.add(_normalize_dependency_name(match.group(1)))
    return dependencies


def _python_dependency_names(payload: dict[str, object]) -> set[str]:
    dependencies: set[str] = set()
    project = payload.get("project")
    if isinstance(project, dict):
        dependencies.update(_dependency_names_from_values(project.get("dependencies")))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for values in optional.values():
                dependencies.update(_dependency_names_from_values(values))

    tool = payload.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            poetry_dependencies = poetry.get("dependencies")
            if isinstance(poetry_dependencies, dict):
                dependencies.update(
                    _normalize_dependency_name(str(name))
                    for name in poetry_dependencies
                    if str(name).casefold() != "python"
                )

    dependency_groups = payload.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for values in dependency_groups.values():
            dependencies.update(_dependency_names_from_values(values))
    return dependencies


def _dependency_names_from_values(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    dependencies: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        match = REQUIREMENT_NAME_PATTERN.match(item)
        if match is not None:
            dependencies.add(_normalize_dependency_name(match.group(1)))
    return dependencies


def _normalize_dependency_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.casefold())


def _add_python_frameworks(
    dependencies: set[str],
    frameworks: set[str],
) -> None:
    for dependency_name, framework_name in PYTHON_FRAMEWORKS.items():
        if dependency_name in dependencies:
            frameworks.add(framework_name)


def _detect_python_frameworks_from_imports(
    filtered_files: list[Path],
    frameworks: set[str],
) -> None:
    for file_path in filtered_files:
        if file_path.suffix.casefold() != ".py":
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        frameworks.add("python")
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.Import):
                module_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names = [node.module]
            for module_name in module_names:
                root_module = module_name.split(".", 1)[0].casefold()
                framework_name = PYTHON_FRAMEWORKS.get(root_module)
                if framework_name is not None:
                    frameworks.add(framework_name)


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
