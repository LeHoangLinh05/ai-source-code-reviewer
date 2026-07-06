"""PostgreSQL engine and async session setup for SQLAlchemy."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.postgres_url,
    echo=settings.debug,
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """Yield one database session per request."""

    async with AsyncSessionLocal() as session:
        yield session


async def ping_postgres() -> None:
    """Verify PostgreSQL connectivity with a lightweight query."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def close_postgres_engine() -> None:
    """Dispose the PostgreSQL connection pool during application shutdown."""

    await engine.dispose()
