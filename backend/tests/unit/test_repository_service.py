"""Tests for source repository management workflows."""

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError, ConflictError
from app.models.user import User
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.repository import RepositoryCreate
from app.services.repository_service import RepositoryService


@pytest.mark.asyncio
async def test_create_repository_rejects_duplicate_name_for_user() -> None:
    repository = RecordingRepositoryRepository(existing_repository=SimpleNamespace())
    service = RepositoryService(cast(RepositoryRepository, repository))
    current_user = cast(User, SimpleNamespace(id=uuid4()))

    with pytest.raises(ConflictError, match="already exists"):
        await service.create_repository(build_payload(name="Backend API"), current_user)

    assert repository.created_payload is None


@pytest.mark.asyncio
async def test_create_repository_allows_name_used_by_no_matching_record() -> None:
    repository = RecordingRepositoryRepository(existing_repository=None)
    service = RepositoryService(cast(RepositoryRepository, repository))
    current_user = cast(User, SimpleNamespace(id=uuid4()))

    created_repository = await service.create_repository(
        build_payload(name="Backend API"),
        current_user,
    )

    assert created_repository is repository.created_repository
    assert repository.name_lookup == (current_user.id, "Backend API")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/backend-api",
        "https://github.com",
        "https://github.com/example",
        "https://user:token@github.com/example/backend-api",
        "https://github.com:8443/example/backend-api",
        "https://github.com/example/backend-api?tab=readme",
        "https://github.com/example/%2Frepository",
        "https://github.com/example/.git",
        "https://example.com/example/backend-api",
        "https://[invalid/example/backend-api",
    ],
)
def test_repository_url_rejects_unsafe_or_incomplete_urls(url: str) -> None:
    service = RepositoryService(cast(RepositoryRepository, SimpleNamespace()))

    with pytest.raises(BadRequestError):
        service._detect_platform(url)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://github.com/example/backend-api", "github"),
        ("https://gitlab.com/example/team/backend-api.git", "gitlab"),
    ],
)
def test_repository_url_accepts_supported_https_urls(url: str, platform: str) -> None:
    service = RepositoryService(cast(RepositoryRepository, SimpleNamespace()))

    assert service._detect_platform(url).value == platform


def build_payload(*, name: str) -> RepositoryCreate:
    return RepositoryCreate(
        name=name,
        url="https://github.com/example/backend-api",
        default_branch="main",
    )


class RecordingRepositoryRepository:
    def __init__(self, *, existing_repository: object | None) -> None:
        self.existing_repository = existing_repository
        self.created_repository = SimpleNamespace(id=uuid4())
        self.created_payload: dict[str, object] | None = None
        self.name_lookup: tuple[object, str] | None = None

    async def get_by_name_for_user(self, user_id: object, name: str) -> object | None:
        self.name_lookup = (user_id, name)
        return self.existing_repository

    async def create(self, **payload: object) -> object:
        self.created_payload = payload
        return self.created_repository

    async def rollback(self) -> None:
        raise AssertionError("Rollback should not be needed in the success path")
