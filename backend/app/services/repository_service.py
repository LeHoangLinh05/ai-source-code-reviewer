"""Repository management business workflows."""

from urllib.parse import unquote, urlparse
from uuid import UUID

from app.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.models.repository import Repository, RepositoryPlatform
from app.models.user import User
from app.repositories.repository_repository import RepositoryRepository
from app.schemas.repository import RepositoryCreate

ALLOWED_REPOSITORY_HOSTS = {
    "github.com": RepositoryPlatform.GITHUB,
}
MIN_REPOSITORY_PATH_SEGMENTS = 2


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

        existing_repository = await self.repository_repository.get_by_name_for_user(
            current_user.id,
            payload.name,
        )
        if existing_repository is not None:
            raise ConflictError("A repository with this name already exists")

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
        """List repositories owned by the current user."""

        return await self.repository_repository.list_for_user(current_user.id)

    async def get_repository(
        self,
        repository_id: UUID,
        current_user: User,
    ) -> Repository:
        """Return a repository after ownership authorization."""

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
        """Delete a repository after ownership authorization."""

        source_repository = await self.get_repository(repository_id, current_user)
        try:
            await self.repository_repository.delete(source_repository)
        except Exception:
            await self.repository_repository.rollback()
            raise

    def _detect_platform(self, repository_url: str) -> RepositoryPlatform:
        try:
            parsed_url = urlparse(repository_url)
            hostname = parsed_url.hostname.lower() if parsed_url.hostname else None
            has_credentials = bool(parsed_url.username or parsed_url.password)
            has_custom_port = parsed_url.port is not None
        except ValueError as error:
            raise BadRequestError("Repository URL is invalid") from error

        if parsed_url.scheme != "https":
            raise BadRequestError("Repository URL must use https")

        if has_credentials:
            raise BadRequestError("Repository URL must not contain credentials")

        if has_custom_port or parsed_url.query or parsed_url.fragment:
            raise BadRequestError(
                "Repository URL must not contain a port, query, or fragment"
            )

        if hostname not in ALLOWED_REPOSITORY_HOSTS:
            raise BadRequestError("Repository URL must point to github.com")

        decoded_path = unquote(parsed_url.path)
        if decoded_path != parsed_url.path:
            raise BadRequestError("Repository URL path must not be encoded")

        path_segments = [segment for segment in decoded_path.split("/") if segment]
        if (
            len(path_segments) < MIN_REPOSITORY_PATH_SEGMENTS
            or any(segment in {".", ".."} for segment in path_segments)
            or path_segments[-1].removesuffix(".git") == ""
        ):
            raise BadRequestError("Repository URL must include an owner and repository")

        return ALLOWED_REPOSITORY_HOSTS[hostname]

    def _ensure_can_access(
        self,
        source_repository: Repository,
        current_user: User,
    ) -> None:
        if source_repository.user_id != current_user.id:
            raise AuthorizationError("Repository access is restricted to its owner")
