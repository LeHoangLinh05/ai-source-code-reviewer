"""Provider connection business workflows."""

from uuid import UUID

from app.core.config import Settings
from app.core.exceptions import (
    AuthorizationError,
    NotFoundError,
)
from app.models.repository import Repository, RepositoryPlatform
from app.models.user import User
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.provider import RepositoryProviderStatus

GITHUB_PROVIDER_NOT_SUPPORTED_MESSAGE = (
    "Only GitHub publishing is available in this version."
)
GITHUB_BOT_NOT_CONFIGURED_MESSAGE = (
    "GitHub bot is not configured. Contact the administrator to configure "
    "GITHUB_BOT_USERNAME and GITHUB_BOT_TOKEN."
)
GITHUB_BOT_READY_MESSAGE_TEMPLATE = "GitHub bot @{bot_username} is ready to publish."


class ProviderService:
    """Manage source-control provider status for repositories."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository_repository: RepositoryRepository,
    ) -> None:
        self.settings = settings
        self.repository_repository = repository_repository

    async def get_repository_provider_status(
        self,
        repository_id: UUID,
        current_user: User,
    ) -> RepositoryProviderStatus:
        """Return provider readiness for one owned repository."""

        source_repository = await self.repository_repository.get_by_id(repository_id)
        if source_repository is None:
            raise NotFoundError("Repository not found")

        self._ensure_repository_owner(source_repository, current_user)
        if source_repository.platform != RepositoryPlatform.GITHUB:
            return RepositoryProviderStatus(
                repository_id=source_repository.id,
                provider=source_repository.platform,
                is_connected=False,
                can_publish=False,
                installation_id=None,
                account_login=None,
                message=GITHUB_PROVIDER_NOT_SUPPORTED_MESSAGE,
            )

        bot_username = self.settings.github_bot_username
        bot_token = (
            self.settings.github_bot_token.get_secret_value().strip()
            if self.settings.github_bot_token
            else None
        )

        if not bot_username or not bot_token:
            return RepositoryProviderStatus(
                repository_id=source_repository.id,
                provider=RepositoryPlatform.GITHUB,
                is_connected=False,
                can_publish=False,
                installation_id=None,
                account_login=None,
                message=GITHUB_BOT_NOT_CONFIGURED_MESSAGE,
            )

        return RepositoryProviderStatus(
            repository_id=source_repository.id,
            provider=RepositoryPlatform.GITHUB,
            is_connected=True,
            can_publish=True,
            installation_id=None,
            account_login=bot_username,
            message=GITHUB_BOT_READY_MESSAGE_TEMPLATE.format(
                bot_username=bot_username,
            ),
        )

    def _ensure_repository_owner(
        self,
        source_repository: Repository,
        current_user: User,
    ) -> None:
        if source_repository.user_id != current_user.id:
            raise AuthorizationError("Repository access is restricted to its owner")
