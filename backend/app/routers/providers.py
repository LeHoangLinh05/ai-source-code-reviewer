"""Provider connection API routes."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.core.dependencies import CurrentUserDep, ProviderServiceDep
from app.schemas.provider import (
    GitHubInstallationSyncRequest,
    GitHubInstallUrlResponse,
    ProviderConnectionResponse,
    RepositoryProviderStatus,
)

router = APIRouter(tags=["providers"])


@router.get(
    "/providers/github/install-url",
    response_model=GitHubInstallUrlResponse,
    summary="Get GitHub App installation URL",
)
async def get_github_install_url(
    provider_service: ProviderServiceDep,
) -> GitHubInstallUrlResponse:
    """Return the configured GitHub App installation URL."""

    return await provider_service.get_github_install_url()


@router.post(
    "/providers/github/installations/sync",
    response_model=ProviderConnectionResponse,
    summary="Sync GitHub App installation",
)
async def sync_github_installation(
    payload: GitHubInstallationSyncRequest,
    current_user: CurrentUserDep,
    provider_service: ProviderServiceDep,
) -> ProviderConnectionResponse:
    """Store GitHub App installation metadata for the authenticated user."""

    return await provider_service.sync_github_installation(payload, current_user)


@router.get(
    "/providers/connections",
    response_model=list[ProviderConnectionResponse],
    summary="List provider connections",
)
async def list_provider_connections(
    current_user: CurrentUserDep,
    provider_service: ProviderServiceDep,
) -> list[ProviderConnectionResponse]:
    """Return provider installations connected by the current user."""

    return await provider_service.list_connections(current_user)


@router.delete(
    "/providers/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect a provider installation",
)
async def disconnect_provider(
    connection_id: UUID,
    current_user: CurrentUserDep,
    provider_service: ProviderServiceDep,
) -> Response:
    """Remove a local provider connection without uninstalling the provider app."""

    await provider_service.disconnect_provider(connection_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
