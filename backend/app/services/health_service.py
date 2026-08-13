"""Infrastructure health checks for the public health endpoint."""

import asyncio

from app.repositories.health_repository import InfrastructureHealthRepository
from app.schemas.health import HealthResponse, HealthServiceStatus

OK_STATUS = "ok"
ERROR_STATUS = "error"


class HealthService:
    """Check connectivity to required runtime services."""

    def __init__(
        self,
        repository: InfrastructureHealthRepository,
    ) -> None:
        self.repository = repository

    async def check(self) -> HealthResponse:
        """Return aggregate application health."""

        postgres_available, redis_available, mongodb_available = await asyncio.gather(
            self.repository.is_postgres_available(),
            self.repository.is_redis_available(),
            self.repository.is_mongodb_available(),
        )
        services = {
            "postgres": _build_service_status(postgres_available),
            "redis": _build_service_status(redis_available),
            "mongodb": _build_service_status(mongodb_available),
        }
        status = (
            OK_STATUS
            if all(service.status == OK_STATUS for service in services.values())
            else ERROR_STATUS
        )

        return HealthResponse(status=status, services=services)


def _build_service_status(is_available: bool) -> HealthServiceStatus:
    status = OK_STATUS if is_available else ERROR_STATUS
    return HealthServiceStatus(status=status)
