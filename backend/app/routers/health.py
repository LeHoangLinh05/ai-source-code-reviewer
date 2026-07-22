"""Application health check API route."""

from fastapi import APIRouter

from app.core.dependencies import HealthServiceDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check API health")
async def get_health(
    health_service: HealthServiceDep,
) -> HealthResponse:
    """Check required infrastructure connectivity."""

    return await health_service.check()
