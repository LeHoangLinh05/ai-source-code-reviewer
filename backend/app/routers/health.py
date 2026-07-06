"""Application health check API route."""

from typing import Annotated

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_mongodb, get_redis
from app.db.postgres import get_async_session
from app.schemas.health import HealthResponse
from app.services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check API health")
async def get_health(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
    mongodb: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> HealthResponse:
    """Check required infrastructure connectivity."""

    return await HealthService(session, redis_client, mongodb).check()
