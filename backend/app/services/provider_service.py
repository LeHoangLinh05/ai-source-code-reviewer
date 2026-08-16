"""Provider connection business workflows."""

from uuid import UUID

from app.core.config import Settings
from app.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.repository import Repository, RepositoryPlatform
from app.models.user import User
from app.repositories.provider_installation_repository import (
    ProviderInstallationRepository,
)
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.provider import (
    GitHubInstallationSyncRequest,
    GitHubInstallUrlResponse,
    ProviderConnectionResponse,
    RepositoryProviderStatus,
)
from app.services.git_provider.base import (
    GitProvider,
    GitProviderError,
    ProviderInstallationDetails,
)

GITHUB_PROVIDER_NOT_SUPPORTED_MESSAGE = (
    "Only GitHub publishing is available in this version."
)
GITHUB_CONNECTED_MESSAGE = "GitHub App installation is connected."
GITHUB_NOT_CONNECTED_MESSAGE = "Connect the GitHub App before publishing pull requests."
GITHUB_INSTALL_URL_NOT_CONFIGURED_MESSAGE = (
    "GitHub App installation URL is not configured. Set GITHUB_APP_INSTALL_URL "
    "to the app installation URL before connecting GitHub."
)
GITHUB_INSTALLATION_LOOKUP_FAILED_MESSAGE = (
    "Unable to verify the GitHub App installation."
)


class ProviderService:
    """Manage connected source-control provider installations."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider_installation_repository: ProviderInstallationRepository,
        repository_repository: RepositoryRepository,
        github_provider: GitProvider | None = None,
    ) -> None:
        self.settings = settings
        self.provider_installation_repository = provider_installation_repository
        self.repository_repository = repository_repository
        self.github_provider = github_provider

    async def get_github_install_url(self) -> GitHubInstallUrlResponse:
        """Return the configured GitHub App installation URL."""

        if self.settings.github_app_install_url is None:
            raise ServiceUnavailableError(GITHUB_INSTALL_URL_NOT_CONFIGURED_MESSAGE)

        return GitHubInstallUrlResponse(
            install_url=self.settings.github_app_install_url,
        )

    async def sync_github_installation(
        self,
        payload: GitHubInstallationSyncRequest,
        current_user: User,
    ) -> ProviderConnectionResponse:
        """Store GitHub App installation metadata for the current user."""

        installation_details = await self._get_github_installation_details(payload)
        try:
            installation = await self.provider_installation_repository.upsert(
                user_id=current_user.id,
                provider=RepositoryPlatform.GITHUB,
                installation_id=installation_details.installation_id,
                account_login=installation_details.account_login,
                account_type=installation_details.account_type,
                repository_selection=installation_details.repository_selection,
                permissions=installation_details.permissions,
            )
        except Exception:
            await self.provider_installation_repository.rollback()
            raise

        return ProviderConnectionResponse.model_validate(installation)

    async def list_connections(
        self,
        current_user: User,
    ) -> list[ProviderConnectionResponse]:
        """Return provider installations connected by the current user."""

        installations = await self.provider_installation_repository.list_for_user(
            current_user.id,
        )
        return [
            ProviderConnectionResponse.model_validate(installation)
            for installation in installations
        ]

    async def disconnect_provider(
        self,
        connection_id: UUID,
        current_user: User,
    ) -> None:
        """Remove a user's local provider link without uninstalling the app."""

        installation = await self.provider_installation_repository.get_by_id_for_user(
            connection_id=connection_id,
            user_id=current_user.id,
        )
        if installation is None:
            raise NotFoundError("Provider connection not found")

        try:
            await self.provider_installation_repository.delete(installation)
        except Exception:
            await self.provider_installation_repository.rollback()
            raise

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

        installation = (
            await self.provider_installation_repository.get_primary_for_user_provider(
                user_id=current_user.id,
                provider=RepositoryPlatform.GITHUB,
            )
        )
        if installation is None:
            return RepositoryProviderStatus(
                repository_id=source_repository.id,
                provider=RepositoryPlatform.GITHUB,
                is_connected=False,
                can_publish=False,
                installation_id=None,
                account_login=None,
                message=GITHUB_NOT_CONNECTED_MESSAGE,
            )

        return RepositoryProviderStatus(
            repository_id=source_repository.id,
            provider=RepositoryPlatform.GITHUB,
            is_connected=True,
            can_publish=True,
            installation_id=installation.installation_id,
            account_login=installation.account_login,
            message=GITHUB_CONNECTED_MESSAGE,
        )

    def _ensure_repository_owner(
        self,
        source_repository: Repository,
        current_user: User,
    ) -> None:
        if source_repository.user_id != current_user.id:
            raise AuthorizationError("Repository access is restricted to its owner")

    async def _get_github_installation_details(
        self,
        payload: GitHubInstallationSyncRequest,
    ) -> ProviderInstallationDetails:
        if self.github_provider is not None:
            try:
                return await self.github_provider.get_installation_details(
                    payload.installation_id,
                )
            except GitProviderError as error:
                raise ServiceUnavailableError(
                    GITHUB_INSTALLATION_LOOKUP_FAILED_MESSAGE,
                ) from error

        if payload.account_login is None:
            raise ServiceUnavailableError(GITHUB_INSTALLATION_LOOKUP_FAILED_MESSAGE)

        return ProviderInstallationDetails(
            installation_id=payload.installation_id,
            account_login=payload.account_login,
            account_type=payload.account_type,
            repository_selection=payload.repository_selection,
            permissions=payload.permissions,
        )
