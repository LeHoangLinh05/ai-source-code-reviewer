"""Redis-backed access token blacklist service."""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

BLACKLIST_KEY_PREFIX = "auth:blacklist:jti:"


class TokenBlacklistService:
    """Store revoked access-token identifiers until their JWT expiry time."""

    def __init__(self, redis_client: Redis) -> None:
        self.redis_client = redis_client

    async def add_to_blacklist(self, jti: str, ttl_seconds: int) -> None:
        """Blacklist a JWT ID with the token's remaining lifetime as Redis TTL."""

        if ttl_seconds <= 0:
            return

        try:
            await self.redis_client.set(
                self._build_key(jti),
                "1",
                ex=ttl_seconds,
            )
        except RedisError as error:
            logger.exception("Failed to add access token ID to blacklist")
            raise ServiceUnavailableError(
                "Redis token blacklist is unavailable"
            ) from error

    async def is_blacklisted(self, jti: str) -> bool:
        """Return whether a JWT ID has been revoked before natural expiry."""

        try:
            return bool(await self.redis_client.exists(self._build_key(jti)))
        except RedisError as error:
            logger.exception("Failed to check access token ID blacklist")
            raise ServiceUnavailableError(
                "Redis token blacklist is unavailable"
            ) from error

    def _build_key(self, jti: str) -> str:
        return f"{BLACKLIST_KEY_PREFIX}{jti}"
