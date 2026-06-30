"""Redis-backed access token blacklist service."""

from datetime import datetime
import logging
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

BLACKLIST_KEY_PREFIX = "auth:blacklist:jti:"
USER_TOKEN_INVALID_AFTER_KEY_PREFIX = "auth:blacklist:user_invalid_after:"


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

    async def invalidate_user_tokens_issued_before(
        self,
        user_id: UUID,
        invalid_after: datetime,
        ttl_seconds: int,
    ) -> None:
        """Reject all user access tokens issued at or before the cutoff."""

        if ttl_seconds <= 0:
            return

        try:
            await self.redis_client.set(
                self._build_user_invalid_after_key(user_id),
                str(int(invalid_after.timestamp())),
                ex=ttl_seconds,
            )
        except RedisError as error:
            logger.exception("Failed to add user access token cutoff to blacklist")
            raise ServiceUnavailableError(
                "Redis token blacklist is unavailable"
            ) from error

    async def is_user_token_invalid(
        self,
        user_id: UUID,
        issued_at: datetime,
    ) -> bool:
        """Return whether a token was issued before the user's logout cutoff."""

        try:
            invalid_after = await self.redis_client.get(
                self._build_user_invalid_after_key(user_id)
            )
        except RedisError as error:
            logger.exception("Failed to check user access token cutoff")
            raise ServiceUnavailableError(
                "Redis token blacklist is unavailable"
            ) from error

        if invalid_after is None:
            return False

        return int(issued_at.timestamp()) <= int(invalid_after)

    def _build_key(self, jti: str) -> str:
        return f"{BLACKLIST_KEY_PREFIX}{jti}"

    def _build_user_invalid_after_key(self, user_id: UUID) -> str:
        return f"{USER_TOKEN_INVALID_AFTER_KEY_PREFIX}{user_id}"
