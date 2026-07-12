"""Repository management API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.dependencies import (
    get_current_user,
    get_repo_summary_query_service,
    get_repository_service,
)
from app.models.user import User
from app.schemas.repository import (
    DeleteResponse,
    RepositoryCreate,
    RepositoryResponse,
)
from app.schemas.repo_summary import RepoSummaryResponse
from app.services.repo_summary_query_service import RepoSummaryQueryService
from app.services.repository_service import RepositoryService

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.post(
    "",
    response_model=RepositoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a source repository",
)
async def create_repository(
    payload: RepositoryCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    repository_service: Annotated[RepositoryService, Depends(get_repository_service)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    repository_service: Annotated[RepositoryService, Depends(get_repository_service)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    summary_service: Annotated[
        RepoSummaryQueryService,
        Depends(get_repo_summary_query_service),
    ],
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
    current_user: Annotated[User, Depends(get_current_user)],
    repository_service: Annotated[RepositoryService, Depends(get_repository_service)],
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
    current_user: Annotated[User, Depends(get_current_user)],
    repository_service: Annotated[RepositoryService, Depends(get_repository_service)],
) -> DeleteResponse:
    """Delete one repository after owner/admin authorization."""

    await repository_service.delete_repository(repository_id, current_user)
    return DeleteResponse(message="Repository deleted")
