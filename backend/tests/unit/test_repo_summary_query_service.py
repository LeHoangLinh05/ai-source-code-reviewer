"""Tests for repository summary query workflows."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.models.user import User
from app.services.repo_summary_query_service import RepoSummaryQueryService


@pytest.mark.asyncio
async def test_get_latest_summary_returns_authorized_repository_summary() -> None:
    repository_id = uuid4()
    generated_at = datetime.now(UTC)
    repository_service = _RecordingRepositoryService()
    summary_repository = _RecordingRepoSummaryRepository(
        {
            "repository_id": str(repository_id),
            "job_id": str(uuid4()),
            "commit_sha": "abc123",
            "generated_at": generated_at.isoformat(),
            "model_used": "gpt-4o-mini",
            "purpose": "Explains what a repository does.",
            "project_type": "REST API backend",
            "tech_stack": ["Python", "FastAPI"],
            "architecture_overview": "API routes call services and repositories.",
            "key_modules": [
                {
                    "path": "backend/app/services",
                    "name": "Services",
                    "description": "Contains business workflows.",
                }
            ],
            "entry_points": [
                {
                    "path": "backend/app/main.py",
                    "description": "Creates the FastAPI application.",
                }
            ],
            "notable_setup": ["Uses Docker Compose"],
        }
    )
    service = RepoSummaryQueryService(
        repository_service=repository_service,  # type: ignore[arg-type]
        repo_summary_repository=summary_repository,  # type: ignore[arg-type]
    )

    summary = await service.get_latest_summary(repository_id, cast(User, object()))

    assert repository_service.repository_ids == [repository_id]
    assert summary_repository.repository_ids == [repository_id]
    assert summary.purpose == "Explains what a repository does."
    assert summary.tech_stack == ["Python", "FastAPI"]
    assert summary.key_modules[0].path == "backend/app/services"
    assert summary.generated_at == generated_at
    assert summary.commit_sha == "abc123"
    assert summary.model_used == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_get_latest_summary_raises_not_found_when_summary_missing() -> None:
    repository_id = uuid4()
    service = RepoSummaryQueryService(
        repository_service=_RecordingRepositoryService(),  # type: ignore[arg-type]
        repo_summary_repository=_RecordingRepoSummaryRepository(None),  # type: ignore[arg-type]
    )

    with pytest.raises(NotFoundError, match="Repository summary not found"):
        await service.get_latest_summary(repository_id, cast(User, object()))


class _RecordingRepositoryService:
    def __init__(self) -> None:
        self.repository_ids: list[UUID] = []

    async def get_repository(
        self,
        repository_id: UUID,
        _current_user: User,
    ) -> object:
        self.repository_ids.append(repository_id)
        return object()


class _RecordingRepoSummaryRepository:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document
        self.repository_ids: list[UUID] = []

    async def find_latest_by_repository_id(
        self,
        repository_id: UUID,
    ) -> dict[str, object] | None:
        self.repository_ids.append(repository_id)
        return self.document
