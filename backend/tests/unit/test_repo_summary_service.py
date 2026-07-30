"""Tests for repository summary prompt construction."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analyzers.secret_scanner import SECRET_MASK
from app.schemas.repo_summary import RepoSummary
from app.services.repo_summary import service as repo_summary_service
from app.services.repo_summary.service import (
    RepoSummaryGenerationError,
    RepoSummaryService,
    coerce_repo_summary,
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
    assert "tech_stack field must be a JSON array of strings" in prompt
    assert "sk-fake-secret-value" not in prompt
    assert f"API_KEY={SECRET_MASK}" in prompt


@pytest.mark.asyncio
async def test_generate_summary_calls_llm_and_parses_json_output(
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
    fake_llm = FakeSummaryLLM([before_validate.model_dump_json()])
    monkeypatch.setattr(
        repo_summary_service,
        "run_with_configured_llm",
        _run_with_fake_llm(fake_llm),
    )

    summary = await RepoSummaryService().generate_summary(prompt)

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
    fake_llm = FakeSummaryLLM([RuntimeError("timeout"), RuntimeError("parse error")])
    monkeypatch.setattr(
        repo_summary_service,
        "run_with_configured_llm",
        _run_with_fake_llm(fake_llm),
    )

    with pytest.raises(RepoSummaryGenerationError):
        await RepoSummaryService().generate_summary(prompt)

    assert fake_llm.prompts == [prompt, prompt]


@pytest.mark.asyncio
async def test_generate_summary_accepts_provider_tech_stack_string_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "File tree (depth <= 3, max 200 lines):\n- README.md\n"
    fake_llm = FakeSummaryLLM(
        [
            "{"
            '"purpose":"A source review platform.",'
            '"project_type":"Web application",'
            '"tech_stack":"Python (FastAPI, Celery), PostgreSQL, Redis",'
            '"architecture_overview":"A web UI calls an API and workers."'
            "}"
        ]
    )
    monkeypatch.setattr(
        repo_summary_service,
        "run_with_configured_llm",
        _run_with_fake_llm(fake_llm),
    )

    summary = await RepoSummaryService().generate_summary(prompt)

    assert fake_llm.prompts == [prompt]
    assert summary.tech_stack == [
        "Python (FastAPI, Celery)",
        "PostgreSQL",
        "Redis",
    ]


def test_coerce_repo_summary_accepts_fenced_json_message_content() -> None:
    summary = coerce_repo_summary(
        _FakeMessage(
            "```json\n"
            "{\n"
            '  "purpose": "A small API service for review jobs.",\n'
            '  "project_type": "REST API backend",\n'
            '  "tech_stack": ["Python", "FastAPI"],\n'
            '  "architecture_overview": "Routes call services."\n'
            "}\n"
            "```"
        )
    )

    assert summary.project_type == "REST API backend"
    assert summary.tech_stack == ["Python", "FastAPI"]


def test_coerce_repo_summary_normalizes_provider_tech_stack_string() -> None:
    summary = coerce_repo_summary(
        _FakeMessage(
            "```json\n"
            "{\n"
            '  "purpose": "A source review platform.",\n'
            '  "project_type": "Web application",\n'
            '  "tech_stack": "Python (FastAPI, Celery); PostgreSQL, Redis\\n'
            'React/Next.js, Docker, Tailwind CSS",\n'
            '  "architecture_overview": "A web UI calls an API and workers."\n'
            "}\n"
            "```"
        )
    )

    assert summary.tech_stack == [
        "Python (FastAPI, Celery)",
        "PostgreSQL",
        "Redis",
        "React/Next.js",
        "Docker",
        "Tailwind CSS",
    ]


def test_coerce_repo_summary_rejects_non_string_non_list_tech_stack() -> None:
    with pytest.raises(ValidationError):
        coerce_repo_summary(
            {
                "purpose": "A source review platform.",
                "project_type": "Web application",
                "tech_stack": {"backend": "Python"},
                "architecture_overview": "A web UI calls an API and workers.",
            }
        )


def test_coerce_repo_summary_preserves_quoted_commas_and_drops_empty_items() -> None:
    summary = coerce_repo_summary(
        {
            "purpose": "A source review platform.",
            "project_type": "Web application",
            "tech_stack": '"Python, FastAPI", , Redis;  ; Docker',
            "architecture_overview": "A web UI calls an API and workers.",
        }
    )

    assert summary.tech_stack == ["Python, FastAPI", "Redis", "Docker"]


def test_repo_summary_domain_schema_remains_strict() -> None:
    with pytest.raises(ValidationError):
        RepoSummary.model_validate(
            {
                "purpose": "A source review platform.",
                "project_type": "Web application",
                "tech_stack": "Python, FastAPI",
                "architecture_overview": "A web UI calls an API and workers.",
            }
        )


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


class FakeSummaryLLM:
    """Tiny LangChain-like fake for repository summary tests."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response

        return response


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def _run_with_fake_llm(fake_llm: FakeSummaryLLM):
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
