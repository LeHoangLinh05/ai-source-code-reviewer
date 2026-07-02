"""Infrastructure health checks for the public health endpoint."""

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.health import HealthResponse, HealthServiceStatus

OK_STATUS = "ok"
ERROR_STATUS = "error"


class HealthService:
    """Check connectivity to required runtime services."""

    def __init__(
        self,
        session: AsyncSession,
        redis_client: Redis,
    ) -> None:
        self.session = session
        self.redis_client = redis_client

    async def check(self) -> HealthResponse:
        """Return aggregate application health."""

        services = {
            "postgres": await self._check_postgres(),
            "redis": await self._check_redis(),
        }
        status = (
            OK_STATUS
            if all(service.status == OK_STATUS for service in services.values())
            else ERROR_STATUS
        )

        return HealthResponse(status=status, services=services)

    async def _check_postgres(self) -> HealthServiceStatus:
        try:
            await self.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return HealthServiceStatus(status=ERROR_STATUS)

        return HealthServiceStatus(status=OK_STATUS)

    async def _check_redis(self) -> HealthServiceStatus:
        try:
            await self.redis_client.ping()
        except RedisError:
            return HealthServiceStatus(status=ERROR_STATUS)

        return HealthServiceStatus(status=OK_STATUS)
