"""Repository management business workflows."""

from urllib.parse import urlparse
from uuid import UUID

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.models.repository import Repository, RepositoryPlatform
from app.models.user import User, UserRole
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.repository import RepositoryCreate

ALLOWED_REPOSITORY_HOSTS = {
    "github.com": RepositoryPlatform.GITHUB,
    "gitlab.com": RepositoryPlatform.GITLAB,
}


class RepositoryService:
    """Business workflows for source repository management."""

    def __init__(self, repository_repository: RepositoryRepository) -> None:
        self.repository_repository = repository_repository

    async def create_repository(
        self,
        payload: RepositoryCreate,
        current_user: User,
    ) -> Repository:
        """Register a repository for the current user."""

        platform = self._detect_platform(payload.url)
        try:
            return await self.repository_repository.create(
                user_id=current_user.id,
                name=payload.name,
                url=payload.url,
                platform=platform,
                default_branch=payload.default_branch,
                description=payload.description,
            )
        except Exception:
            await self.repository_repository.rollback()
            raise

    async def list_repositories(self, current_user: User) -> list[Repository]:
        """List repositories visible to the current user."""

        if current_user.role == UserRole.ADMIN:
            return await self.repository_repository.list_all()

        return await self.repository_repository.list_for_user(current_user.id)

    async def get_repository(
        self,
        repository_id: UUID,
        current_user: User,
    ) -> Repository:
        """Return a repository after ownership or admin authorization."""

        source_repository = await self.repository_repository.get_by_id(repository_id)
        if source_repository is None:
            raise NotFoundError("Repository not found")

        self._ensure_can_access(source_repository, current_user)
        return source_repository

    async def delete_repository(
        self,
        repository_id: UUID,
        current_user: User,
    ) -> None:
        """Delete a repository after ownership or admin authorization."""

        source_repository = await self.get_repository(repository_id, current_user)
        try:
            await self.repository_repository.delete(source_repository)
        except Exception:
            await self.repository_repository.rollback()
            raise

    def _detect_platform(self, repository_url: str) -> RepositoryPlatform:
        parsed_url = urlparse(repository_url)
        if parsed_url.scheme not in {"http", "https"}:
            raise BadRequestError("Repository URL must use http or https")

        hostname = parsed_url.hostname.lower() if parsed_url.hostname else None
        if hostname not in ALLOWED_REPOSITORY_HOSTS:
            raise BadRequestError(
                "Repository URL must point to github.com or gitlab.com"
            )

        return ALLOWED_REPOSITORY_HOSTS[hostname]

    def _ensure_can_access(
        self,
        source_repository: Repository,
        current_user: User,
    ) -> None:
        if current_user.role == UserRole.ADMIN:
            return

        if source_repository.user_id != current_user.id:
            raise AuthorizationError("Repository access is restricted to its owner")
