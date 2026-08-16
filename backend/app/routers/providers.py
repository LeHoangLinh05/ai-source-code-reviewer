"""Provider connection API routes."""

from uuid import UUID

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, ProviderServiceDep
from app.schemas.provider import RepositoryProviderStatus

router = APIRouter(tags=["providers"])


@router.get(
    "/repositories/{repository_id}/provider-status",
    response_model=RepositoryProviderStatus,
    summary="Get repository provider status",
)
async def get_repository_provider_status(
    repository_id: UUID,
    current_user: CurrentUserDep,
    provider_service: ProviderServiceDep,
) -> RepositoryProviderStatus:
    """Return provider readiness for one owned repository."""

    return await provider_service.get_repository_provider_status(
        repository_id,
        current_user,
    )
