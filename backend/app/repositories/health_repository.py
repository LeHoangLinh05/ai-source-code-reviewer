"""Connectivity checks for required application infrastructure."""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


class InfrastructureHealthRepository:
    """Check infrastructure connectivity without exposing clients to services."""

    def __init__(
        self,
        *,
        postgres_session: AsyncSession,
        redis_client: Redis,
        mongodb_database: AsyncIOMotorDatabase,
    ) -> None:
        self.postgres_session = postgres_session
        self.redis_client = redis_client
        self.mongodb_database = mongodb_database

    async def is_postgres_available(self) -> bool:
        """Return whether PostgreSQL accepts a minimal query."""

        try:
            await self.postgres_session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True

    async def is_redis_available(self) -> bool:
        """Return whether Redis responds to a ping."""

        try:
            await self.redis_client.ping()
        except RedisError:
            return False
        return True

    async def is_mongodb_available(self) -> bool:
        """Return whether MongoDB responds to a ping command."""

        try:
            await self.mongodb_database.command("ping")
        except PyMongoError:
            return False
        return True
