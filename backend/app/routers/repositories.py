"""Repository management API routes."""

from uuid import UUID

from fastapi import APIRouter, status

from app.core.dependencies import (
    CurrentUserDep,
    RepositoryServiceDep,
    RepoSummaryQueryServiceDep,
)
from app.schemas.repo_summary import RepoSummaryResponse
from app.schemas.repository import (
    DeleteResponse,
    RepositoryCreate,
    RepositoryResponse,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])
REPOSITORY_DELETED_MESSAGE = "Repository deleted"


@router.post(
    "",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a source repository",
)
async def create_repository(
    payload: RepositoryCreate,
    current_user: CurrentUserDep,
    repository_service: RepositoryServiceDep,
) -> RepositoryResponse:
    """Create a repository owned by the current user."""

    source_repository = await repository_service.create_repository(
        payload,
        current_user,
    )
    return RepositoryResponse.model_validate(source_repository)


@router.get(
    "",
    response_model=list[RepositoryResponse],
    summary="List visible repositories",
)
async def list_repositories(
    current_user: CurrentUserDep,
    repository_service: RepositoryServiceDep,
) -> list[RepositoryResponse]:
    """List repositories owned by the user, or all repositories for admins."""

    repositories = await repository_service.list_repositories(current_user)
    return [
        RepositoryResponse.model_validate(source_repository)
        for source_repository in repositories
    ]


@router.get(
    "/{repository_id}/summary",
    response_model=RepoSummaryResponse,
    summary="Get latest repository project overview",
)
async def get_repository_summary(
    repository_id: UUID,
    current_user: CurrentUserDep,
    summary_service: RepoSummaryQueryServiceDep,
) -> RepoSummaryResponse:
    """Return the newest generated project overview for an authorized repository."""

    return await summary_service.get_latest_summary(repository_id, current_user)


@router.get(
    "/{repository_id}",
    response_model=RepositoryResponse,
    summary="Get repository details",
)
async def get_repository(
    repository_id: UUID,
    current_user: CurrentUserDep,
    repository_service: RepositoryServiceDep,
) -> RepositoryResponse:
    """Return one repository after owner/admin authorization."""

    source_repository = await repository_service.get_repository(
        repository_id,
        current_user,
    )
    return RepositoryResponse.model_validate(source_repository)


@router.delete(
    "/{repository_id}",
    response_model=DeleteResponse,
    summary="Delete a repository",
)
async def delete_repository(
    repository_id: UUID,
    current_user: CurrentUserDep,
    repository_service: RepositoryServiceDep,
) -> DeleteResponse:
    """Delete one repository after owner/admin authorization."""

    await repository_service.delete_repository(repository_id, current_user)
    return DeleteResponse(message=REPOSITORY_DELETED_MESSAGE)
