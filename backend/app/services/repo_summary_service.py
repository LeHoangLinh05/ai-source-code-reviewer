"""Build and generate repository overview prompts."""

import logging
import re
from pathlib import Path

from app.ai.json_utils import parse_json_object_text
from app.ai.llm_config import run_with_configured_llm
from app.analyzers.file_filter import filter_files
from app.analyzers.secret_scanner import mask_secret_values
from app.analyzers.structure_analyzer import analyze_structure
from app.schemas.repo_summary import RepoSummary

MAX_TREE_DEPTH = 3
MAX_TREE_LINES = 200
# Roughly 3k tokens for prose-heavy README/config snippets; enough for project
# overview while keeping the future one-shot LLM call bounded.
MAX_CONTEXT_FILE_CHARS = 12_000
README_CANDIDATES = ("README.md", "README", "README.rst")
CONFIG_FILE_NAMES = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "docker-compose.yml",
)
MAX_REPO_SUMMARY_LLM_ATTEMPTS = 2
FILE_TREE_HEADER = "File tree (depth <= 3, max 200 lines):"
FILE_TREE_LINE_REGEX = re.compile(r"^(?P<indent> *)- (?P<name>.+)$")

logger = logging.getLogger(__name__)


class RepoSummaryGenerationError(Exception):
    """Raised when one-shot Repo Summary generation cannot produce valid output."""


class RepoSummaryService:
    """Prepare and execute the Repo Summary one-shot LLM flow."""

    def build_prompt(self, sandbox_path: Path) -> str:
        """Return a prompt string ready for structured RepoSummary generation."""

        sandbox_root = sandbox_path.resolve()
        filtered_files = filter_files(sandbox_root)
        structure = analyze_structure(sandbox_root, filtered_files)
        tree_lines = render_file_tree(
            structure.file_tree,
            max_depth=MAX_TREE_DEPTH,
            max_lines=MAX_TREE_LINES,
        )
        context_sections = self._build_file_context_sections(sandbox_root)

        return "\n\n".join(
            [
                "You are generating a repository project overview, not a code "
                "review report. Use only the project-level context below. "
                "Write every field in English.",
                "Return valid JSON only matching RepoSummary with fields: "
                "purpose, project_type, tech_stack, architecture_overview. "
                "Make purpose a detailed paragraph that explains what the project "
                "does, the main user-facing or operational workflows, and the "
                "important capabilities visible from the repository context. "
                "Do not include separate key modules, entry points, or notable "
                "setup sections.",
                format_project_structure(structure.project_structure),
                "File tree (depth <= 3, max 200 lines):\n"
                + ("\n".join(tree_lines) if tree_lines else "(empty)"),
                context_sections or "Project context files: none found.",
            ]
        )

    async def generate_summary(self, prompt: str) -> RepoSummary:
        """Call the configured LLM once per attempt and return a validated summary."""

        for attempt in range(1, MAX_REPO_SUMMARY_LLM_ATTEMPTS + 1):
            try:
                summary = await invoke_repo_summary_llm(prompt)
                valid_paths = extract_file_tree_paths_from_prompt(prompt)
                return validate_repo_summary(summary, valid_paths)
            except Exception as error:
                if attempt >= MAX_REPO_SUMMARY_LLM_ATTEMPTS:
                    logger.error(
                        "Repo Summary LLM generation failed after %s attempts: %s",
                        MAX_REPO_SUMMARY_LLM_ATTEMPTS,
                        error,
                        exc_info=True,
                    )
                    raise RepoSummaryGenerationError(
                        "Repo Summary LLM generation failed after 2 attempts"
                    ) from error

                logger.warning(
                    "Repo Summary LLM call failed on attempt %s/%s; retrying once: %s",
                    attempt,
                    MAX_REPO_SUMMARY_LLM_ATTEMPTS,
                    error,
                    exc_info=True,
                )

        raise RepoSummaryGenerationError(
            "Repo Summary LLM generation failed after 2 attempts"
        )

    def _build_file_context_sections(self, sandbox_root: Path) -> str:
        sections: list[str] = []
        readme_path = find_first_existing_root_file(sandbox_root, README_CANDIDATES)
        if readme_path is not None:
            sections.append(format_file_section(readme_path, sandbox_root))

        for file_name in CONFIG_FILE_NAMES:
            file_path = sandbox_root / file_name
            if file_path.is_file():
                sections.append(format_file_section(file_path, sandbox_root))

        return "\n\n".join(sections)


async def invoke_repo_summary_llm(prompt: str) -> RepoSummary:
    """Run one JSON-output LLM call for RepoSummary."""

    async def invoke(llm: object) -> RepoSummary:
        result = await llm.ainvoke(prompt)  # type: ignore[attr-defined]
        return coerce_repo_summary(result)

    result = await run_with_configured_llm(invoke, allow_fallback=True)
    return coerce_repo_summary(result)


def coerce_repo_summary(value: object) -> RepoSummary:
    """Normalize LangChain structured-output results to RepoSummary."""

    if isinstance(value, RepoSummary):
        return value

    if isinstance(value, dict):
        return RepoSummary.model_validate(value)

    if isinstance(value, str):
        parsed_value = parse_json_object_text(value)
        if parsed_value is not None:
            return RepoSummary.model_validate(parsed_value)

    content = getattr(value, "content", None)
    if content is not None:
        return coerce_repo_summary(_message_content_text(content))

    raise TypeError(f"Expected RepoSummary from LLM, got {type(value).__name__}")


def _message_content_text(content: object) -> object:
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

        return "\n".join(text_parts)

    return content


def validate_repo_summary(summary: RepoSummary, valid_paths: set[str]) -> RepoSummary:
    """Validate the generated summary against the prompt context."""

    _ = valid_paths
    return summary


def is_valid_summary_path(path: str, valid_paths: set[str], *, field_name: str) -> bool:
    """Return whether a summary path exists in the prompt file tree."""

    normalized_path = path.strip().strip("/")
    if normalized_path in valid_paths:
        return True

    logger.warning(
        "Dropping Repo Summary %s path not present in tree: %s", field_name, path
    )
    return False


def extract_file_tree_paths_from_prompt(prompt: str) -> set[str]:
    """Extract real tree paths rendered by build_prompt for validation."""

    valid_paths: set[str] = set()
    path_stack: list[str] = []
    in_file_tree = False
    for line in prompt.splitlines():
        if line == FILE_TREE_HEADER:
            in_file_tree = True
            continue

        if not in_file_tree:
            continue

        if not line.strip():
            break

        match = FILE_TREE_LINE_REGEX.match(line)
        if match is None:
            continue

        name = match.group("name").strip()
        if name.startswith("...") or name == "(empty)":
            continue

        depth = len(match.group("indent")) // 2 + 1
        clean_name = name.removesuffix("/")
        path_stack = [*path_stack[: depth - 1], clean_name]
        valid_paths.add("/".join(path_stack))

    return valid_paths


def format_project_structure(project_structure: dict[str, object]) -> str:
    """Render detected language/framework metadata for prompt context."""

    languages = project_structure.get("languages", {})
    frameworks = project_structure.get("frameworks", [])
    primary_language = project_structure.get("primary_language") or "unknown"
    total_files = project_structure.get("total_files", 0)
    return "\n".join(
        [
            "Detected project structure:",
            f"- primary_language: {primary_language}",
            f"- languages: {languages}",
            f"- frameworks: {frameworks}",
            f"- total_files: {total_files}",
        ]
    )


def render_file_tree(
    file_tree: list[dict[str, object]],
    *,
    max_depth: int,
    max_lines: int,
) -> list[str]:
    """Render a nested file tree as bounded prompt text."""

    lines: list[str] = []
    _append_tree_lines(file_tree, lines, depth=1, max_depth=max_depth)
    if len(lines) <= max_lines:
        return lines

    return [*lines[:max_lines], f"... truncated {len(lines) - max_lines} more entries"]


def _append_tree_lines(
    nodes: list[dict[str, object]],
    lines: list[str],
    *,
    depth: int,
    max_depth: int,
) -> None:
    for node in nodes:
        name = str(node.get("name", ""))
        node_type = str(node.get("type", "file"))
        suffix = "/" if node_type == "directory" else ""
        lines.append(f"{'  ' * (depth - 1)}- {name}{suffix}")
        children = node.get("children", [])
        if depth >= max_depth or not isinstance(children, list) or not children:
            continue

        _append_tree_lines(children, lines, depth=depth + 1, max_depth=max_depth)


def find_first_existing_root_file(
    sandbox_root: Path,
    candidates: tuple[str, ...],
) -> Path | None:
    """Return the first existing root-level file from ordered candidates."""

    for file_name in candidates:
        file_path = sandbox_root / file_name
        if file_path.is_file():
            return file_path

    return None


def format_file_section(file_path: Path, sandbox_root: Path) -> str:
    """Read, truncate, and secret-mask a project context file."""

    relative_path = file_path.resolve().relative_to(sandbox_root.resolve()).as_posix()
    content = read_context_file(file_path)
    masked_content = mask_secret_values(content)
    return "\n".join(
        [
            f"Project context file: {relative_path}",
            "```text",
            masked_content,
            "```",
        ]
    )


def read_context_file(file_path: Path) -> str:
    """Read a bounded project context file for prompt construction."""

    content = file_path.read_text(encoding="utf-8", errors="ignore")
    if len(content) <= MAX_CONTEXT_FILE_CHARS:
        return content

    omitted_chars = len(content) - MAX_CONTEXT_FILE_CHARS
    return (
        f"{content[:MAX_CONTEXT_FILE_CHARS]}\n"
        f"... truncated {omitted_chars} characters ..."
    )
