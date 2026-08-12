"""Tests for source-control provider connection workflows."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailableError
from app.models.repository import RepositoryPlatform
from app.models.user import User, UserRole
from app.repositories.provider_installation_repository import (
    ProviderInstallationRepository,
)
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.provider import GitHubInstallationSyncRequest
from app.services.git_provider.base import GitProvider, ProviderInstallationDetails
from app.services.provider_service import ProviderService

JWT_SECRET_KEY = SecretStr("x" * 32)
GITHUB_APP_INSTALL_URL = "https://github.com/apps/repoguard-ai/installations/new"


@pytest.mark.asyncio
async def test_get_github_install_url_returns_configured_install_url() -> None:
    service = build_provider_service(
        Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_app_install_url=GITHUB_APP_INSTALL_URL,
        ),
    )

    response = await service.get_github_install_url()

    assert response.install_url == GITHUB_APP_INSTALL_URL


@pytest.mark.asyncio
async def test_get_github_install_url_rejects_missing_configuration() -> None:
    service = build_provider_service(
        Settings(jwt_secret_key=JWT_SECRET_KEY, github_app_install_url=None),
    )

    with pytest.raises(ServiceUnavailableError, match="GITHUB_APP_INSTALL_URL"):
        await service.get_github_install_url()


@pytest.mark.asyncio
async def test_sync_github_installation_uses_github_provider_details() -> None:
    github_provider = RecordingGitHubProvider()
    installation_repository = RecordingProviderInstallationRepository()
    service = ProviderService(
        settings=Settings(
            jwt_secret_key=JWT_SECRET_KEY,
            github_app_install_url=GITHUB_APP_INSTALL_URL,
        ),
        provider_installation_repository=cast(
            ProviderInstallationRepository,
            installation_repository,
        ),
        repository_repository=cast(RepositoryRepository, SimpleNamespace()),
        github_provider=cast(GitProvider, github_provider),
    )
    current_user = User(
        id=uuid4(),
        email="user@example.com",
        hashed_password="hashed",
        full_name=None,
        is_active=True,
        role=UserRole.USER,
    )

    response = await service.sync_github_installation(
        GitHubInstallationSyncRequest(installation_id="12345"),
        current_user,
    )

    assert github_provider.requested_installation_ids == ["12345"]
    assert installation_repository.upserts == [
        {
            "user_id": current_user.id,
            "provider": "github",
            "installation_id": "12345",
            "account_login": "LeHoangLinh05",
            "account_type": "User",
            "repository_selection": "all",
            "permissions": {"metadata": "read"},
        }
    ]
    assert response.account_login == "LeHoangLinh05"


def build_provider_service(settings: Settings) -> ProviderService:
    return ProviderService(
        settings=settings,
        provider_installation_repository=cast(
            ProviderInstallationRepository,
            SimpleNamespace(),
        ),
        repository_repository=cast(RepositoryRepository, SimpleNamespace()),
    )


class RecordingGitHubProvider:
    def __init__(self) -> None:
        self.requested_installation_ids: list[str] = []

    async def get_installation_details(
        self,
        installation_id: str,
    ) -> ProviderInstallationDetails:
        self.requested_installation_ids.append(installation_id)
        return ProviderInstallationDetails(
            installation_id=installation_id,
            account_login="LeHoangLinh05",
            account_type="User",
            repository_selection="all",
            permissions={"metadata": "read"},
        )


class RecordingProviderInstallationRepository:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []

    async def upsert(
        self,
        *,
        user_id: UUID,
        provider: RepositoryPlatform,
        installation_id: str,
        account_login: str,
        account_type: str | None,
        repository_selection: str | None,
        permissions: dict[str, object] | None,
    ) -> SimpleNamespace:
        self.upserts.append(
            {
                "user_id": user_id,
                "provider": provider.value if hasattr(provider, "value") else provider,
                "installation_id": installation_id,
                "account_login": account_login,
                "account_type": account_type,
                "repository_selection": repository_selection,
                "permissions": permissions,
            }
        )
        return SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            provider=provider,
            installation_id=installation_id,
            account_login=account_login,
            account_type=account_type,
            repository_selection=repository_selection,
            permissions=permissions,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def rollback(self) -> None:
        return None
