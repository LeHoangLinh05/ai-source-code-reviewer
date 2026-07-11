"""Tests for review pipeline configuration helpers."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.ai.roadmap.knowledge import ROADMAP_PROFILE_ID
from app.services.review_pipeline_service import (
    ReviewJobCanceled,
    ReviewPipelineError,
    ReviewPipelineService,
    build_plain_file_chunk_metadata,
    get_rule_profile,
)


def test_get_rule_profile_is_disabled_when_options_missing() -> None:
    assert get_rule_profile(None) is None


def test_get_rule_profile_is_disabled_when_key_missing() -> None:
    assert get_rule_profile({"run_static_analysis": True}) is None


def test_get_rule_profile_preserves_explicit_profile() -> None:
    rule_profile = {"id": ROADMAP_PROFILE_ID, "weeks_included": [1, 2]}

    assert get_rule_profile({"rule_profile": rule_profile}) == rule_profile


def test_get_rule_profile_rejects_invalid_profile_shape() -> None:
    with pytest.raises(ReviewPipelineError, match="rule_profile option"):
        get_rule_profile({"rule_profile": "roadmap_bootcamp_v1"})


def test_plain_text_files_are_chunked_for_ai_coverage(tmp_path: Path) -> None:
    source_path = tmp_path / "frontend" / "app.tsx"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "export function App() {\n  return null\n}\n", encoding="utf-8"
    )

    metadata = build_plain_file_chunk_metadata(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=source_path,
        issues=[],
    )

    assert metadata.file_path == "frontend/app.tsx"
    assert metadata.chunk_index == 0
    assert metadata.total_chunks == 1
    assert metadata.chunk_text == "export function App() {\n  return null\n}\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_pipeline_always_cleans_code_chunks_for_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
) -> None:
    job_id = uuid4()
    code_store = _RecordingCodeStore()
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.settings = SimpleNamespace(sandbox_root=str(tmp_path))
    service.review_job_repository = _ExistingJobRepository()
    service.code_embedding_store = code_store

    async def run_pipeline_steps(_review_job: object, _sandbox_path: Path) -> None:
        if outcome == "failed":
            raise RuntimeError("pipeline failed")
        if outcome == "cancelled":
            raise ReviewJobCanceled

    async def handle_failure(_job_id: object, _error: Exception) -> None:
        return None

    monkeypatch.setattr(service, "_run_pipeline_steps", run_pipeline_steps)
    monkeypatch.setattr(service, "_handle_failure", handle_failure)

    await service.run(job_id)

    assert code_store.deleted_job_ids == [str(job_id)]


class _ExistingJobRepository:
    async def get_by_id(self, job_id: object) -> object:
        return SimpleNamespace(id=job_id)

    async def mark_started(self, review_job: object, *, sandbox_path: str) -> None:
        _ = review_job, sandbox_path


class _RecordingCodeStore:
    def __init__(self) -> None:
        self.deleted_job_ids: list[str] = []

    def delete_job(self, job_id: str) -> None:
        self.deleted_job_ids.append(job_id)
