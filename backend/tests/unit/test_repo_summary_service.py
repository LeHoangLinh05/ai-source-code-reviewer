"""Tests for repository summary prompt construction."""

from pathlib import Path

import pytest

from app.analyzers.secret_scanner import SECRET_MASK
from app.schemas.repo_summary import RepoSummary
import app.services.repo_summary_service as repo_summary_service
from app.services.repo_summary_service import (
    RepoSummaryGenerationError,
    RepoSummaryService,
    extract_file_tree_paths_from_prompt,
)


def test_repo_summary_prompt_uses_project_context_and_masks_secrets(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text(
        "# Demo API\n\nA small FastAPI service for review jobs.\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"next":"15.0.0","react":"19.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "\n".join(
            [
                "services:",
                "  api:",
                "    environment:",
                "      - API_KEY=sk-fake-secret-value",
            ]
        ),
        encoding="utf-8",
    )
    source_dir = tmp_path / "src" / "api"
    source_dir.mkdir(parents=True)
    (source_dir / "routes.py").write_text("from fastapi import APIRouter\n")

    prompt = RepoSummaryService().build_prompt(tmp_path)

    assert "You are generating a repository project overview" in prompt
    assert "File tree (depth <= 3, max 200 lines):" in prompt
    assert "- src/" in prompt
    assert "  - api/" in prompt
    assert "    - routes.py" in prompt
    assert "Project context file: README.md" in prompt
    assert "Project context file: package.json" in prompt
    assert "Project context file: requirements.txt" in prompt
    assert "Project context file: docker-compose.yml" in prompt
    assert "sk-fake-secret-value" not in prompt
    assert f"API_KEY={SECRET_MASK}" in prompt


@pytest.mark.asyncio
async def test_generate_summary_calls_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "\n".join(
        [
            "Intro",
            "",
            "File tree (depth <= 3, max 200 lines):",
            "- README.md",
            "- src/",
            "  - api/",
            "    - routes.py",
            "",
            "Project context file: README.md",
        ]
    )
    before_validate = build_summary()
    fake_llm = FakeStructuredLLM([before_validate])
    monkeypatch.setattr(
        repo_summary_service,
        "run_with_configured_llm",
        _run_with_fake_llm(fake_llm),
    )

    summary = await RepoSummaryService().generate_summary(prompt)

    assert fake_llm.schema is RepoSummary
    assert fake_llm.prompts == [prompt]
    assert summary == before_validate
    assert summary.purpose == (
        "A small API service for managing review jobs and exposing repository "
        "analysis workflows."
    )


@pytest.mark.asyncio
async def test_generate_summary_retries_once_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "\n".join(
        [
            "File tree (depth <= 3, max 200 lines):",
            "- README.md",
            "",
        ]
    )
    fake_llm = FakeStructuredLLM([RuntimeError("timeout"), RuntimeError("parse error")])
    monkeypatch.setattr(
        repo_summary_service,
        "run_with_configured_llm",
        _run_with_fake_llm(fake_llm),
    )

    with pytest.raises(RepoSummaryGenerationError):
        await RepoSummaryService().generate_summary(prompt)

    assert fake_llm.prompts == [prompt, prompt]


def test_extract_file_tree_paths_from_prompt_reconstructs_nested_paths() -> None:
    prompt = "\n".join(
        [
            "File tree (depth <= 3, max 200 lines):",
            "- README.md",
            "- backend/",
            "  - app/",
            "    - main.py",
            "",
            "Project context file: README.md",
        ]
    )

    assert extract_file_tree_paths_from_prompt(prompt) == {
        "README.md",
        "backend",
        "backend/app",
        "backend/app/main.py",
    }


class FakeStructuredLLM:
    """Tiny LangChain-like fake for structured-output tests."""

    def __init__(self, responses: list[RepoSummary | Exception]) -> None:
        self.responses = responses
        self.prompts: list[str] = []
        self.schema: type[RepoSummary] | None = None

    def with_structured_output(self, schema: type[RepoSummary]) -> "FakeStructuredLLM":
        self.schema = schema
        return self

    async def ainvoke(self, prompt: str) -> RepoSummary:
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response

        return response


def _run_with_fake_llm(fake_llm: FakeStructuredLLM):
    async def run(call, *, allow_fallback: bool = False):  # type: ignore[no-untyped-def]
        _ = allow_fallback
        return await call(fake_llm)

    return run


def build_summary() -> RepoSummary:
    return RepoSummary(
        purpose=(
            "A small API service for managing review jobs and exposing repository "
            "analysis workflows."
        ),
        project_type="REST API backend",
        tech_stack=["Python", "FastAPI"],
        architecture_overview="Routes live under src/api.",
    )
