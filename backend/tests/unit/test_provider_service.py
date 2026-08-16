"""Tests for source-control provider connection workflows."""

from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import AuthorizationError, NotFoundError
from app.models.repository import Repository, RepositoryPlatform
from app.models.user import User, UserRole
from app.services.provider_service import (
    GITHUB_BOT_NOT_CONFIGURED_MESSAGE,
    GITHUB_BOT_READY_MESSAGE_TEMPLATE,
    GITHUB_PROVIDER_NOT_SUPPORTED_MESSAGE,
    ProviderService,
)

JWT_SECRET_KEY = SecretStr("x" * 32)
BOT_USERNAME = "repoguard-bot"
BOT_TOKEN = SecretStr("ghp_testtoken123")


@pytest.mark.asyncio
async def test_get_repository_provider_status_ready_when_bot_configured() -> None:
    user = build_user()
    repository = build_repository(user_id=user.id, platform=RepositoryPlatform.GITHUB)
    service = build_provider_service(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_bot_username=BOT_USERNAME,
            github_bot_token=BOT_TOKEN,
        ),
        repository=repository,
    )

    status = await service.get_repository_provider_status(repository.id, user)

    assert status.is_connected is True
    assert status.can_publish is True
    assert status.account_login == BOT_USERNAME
    assert status.provider == RepositoryPlatform.GITHUB
    assert status.message == GITHUB_BOT_READY_MESSAGE_TEMPLATE.format(
        bot_username=BOT_USERNAME
    )


@pytest.mark.asyncio
async def test_get_repository_provider_status_unready_when_bot_not_configured() -> None:
    user = build_user()
    repository = build_repository(user_id=user.id, platform=RepositoryPlatform.GITHUB)
    service = build_provider_service(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_bot_username=None,
            github_bot_token=None,
        ),
        repository=repository,
    )

    status = await service.get_repository_provider_status(repository.id, user)

    assert status.is_connected is False
    assert status.can_publish is False
    assert status.account_login is None
    assert status.provider == RepositoryPlatform.GITHUB
    assert status.message == GITHUB_BOT_NOT_CONFIGURED_MESSAGE


@pytest.mark.asyncio
async def test_get_repository_provider_status_unsupported_for_non_github() -> None:
    user = build_user()
    repository = build_repository(user_id=user.id, platform=RepositoryPlatform.OTHER)
    service = build_provider_service(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_bot_username=BOT_USERNAME,
            github_bot_token=BOT_TOKEN,
        ),
        repository=repository,
    )

    status = await service.get_repository_provider_status(repository.id, user)

    assert status.is_connected is False
    assert status.can_publish is False
    assert status.provider == RepositoryPlatform.OTHER
    assert status.message == GITHUB_PROVIDER_NOT_SUPPORTED_MESSAGE


@pytest.mark.asyncio
async def test_get_repository_provider_status_rejects_unowned_repository() -> None:
    user = build_user()
    other_user_id = uuid4()
    repository = build_repository(
        user_id=other_user_id, platform=RepositoryPlatform.GITHUB
    )
    service = build_provider_service(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_bot_username=BOT_USERNAME,
            github_bot_token=BOT_TOKEN,
        ),
        repository=repository,
    )

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await service.get_repository_provider_status(repository.id, user)


@pytest.mark.asyncio
async def test_get_repository_provider_status_rejects_missing_repository() -> None:
    user = build_user()
    service = build_provider_service(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_bot_username=BOT_USERNAME,
            github_bot_token=BOT_TOKEN,
        ),
        repository=None,
    )

    with pytest.raises(NotFoundError, match="Repository not found"):
        await service.get_repository_provider_status(uuid4(), user)


def build_user() -> User:
    return User(
        id=uuid4(),
        email="user@example.com",
        hashed_password="hashed",
        full_name="Test User",
        is_active=True,
        role=UserRole.USER,
    )


def build_repository(
    *,
    user_id: UUID,
    platform: RepositoryPlatform = RepositoryPlatform.GITHUB,
) -> Repository:
    return Repository(
        id=uuid4(),
        user_id=user_id,
        name="test-repo",
        url="https://github.com/example/test-repo.git",
        platform=platform,
        default_branch="main",
    )


class RecordingRepositoryRepository:
    def __init__(self, repository: Repository | None) -> None:
        self.repository = repository

    async def get_by_id(self, repository_id: UUID) -> Repository | None:
        if self.repository and self.repository.id == repository_id:
            return self.repository
        return None


def build_provider_service(
    settings: Settings,
    repository: Repository | None = None,
) -> ProviderService:
    repo_repo = RecordingRepositoryRepository(repository)
    return ProviderService(
        settings=settings,
        repository_repository=repo_repo,  # type: ignore[arg-type]
    )
