"""Query workflows for generated repository project summaries."""

from uuid import UUID

from app.core.exceptions import NotFoundError
from app.models.user import User
from app.repositories.mongodb_repository import RepoSummaryResultRepository
from app.schemas.repo_summary import RepoSummaryResponse
from app.services.repository_service import RepositoryService


class RepoSummaryQueryService:
    """Read the latest generated project overview for an authorized repository."""

    def __init__(
        self,
        repository_service: RepositoryService,
        repo_summary_repository: RepoSummaryResultRepository,
    ) -> None:
        self.repository_service = repository_service
        self.repo_summary_repository = repo_summary_repository

    async def get_latest_summary(
        self,
        repository_id: UUID,
        current_user: User,
    ) -> RepoSummaryResponse:
        """Return the newest summary after repository access is verified."""

        await self.repository_service.get_repository(repository_id, current_user)
        document = await self.repo_summary_repository.find_latest_by_repository_id(
            repository_id
        )
        if document is None:
            raise NotFoundError("Repository summary not found")

        return RepoSummaryResponse.model_validate(document)
