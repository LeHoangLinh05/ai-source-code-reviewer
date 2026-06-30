"""Redis client setup for cache, queue, pub/sub, and token blacklist."""

from redis.asyncio import ConnectionPool, Redis

from app.core.config import get_settings

redis_pool: ConnectionPool | None = None
redis_client: Redis | None = None


def get_redis_pool() -> ConnectionPool:
    """Return the shared Redis connection pool for async clients."""

    global redis_pool

    if redis_pool is None:
        settings = get_settings()
        redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=settings.redis_health_check_interval_seconds,
            max_connections=settings.redis_max_connections,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )

    return redis_pool


def get_redis_client() -> Redis:
    """Return the shared Redis client used by auth token services."""

    global redis_client

    if redis_client is None:
        redis_client = Redis(connection_pool=get_redis_pool())

    return redis_client


async def close_redis_client() -> None:
    """Close the shared Redis connection during application shutdown."""

    global redis_client, redis_pool

    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None

    if redis_pool is not None:
        await redis_pool.aclose()
        redis_pool = None
