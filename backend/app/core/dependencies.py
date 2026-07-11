"""FastAPI dependency providers shared across routers."""

from typing import Annotated

from fastapi import Cookie, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.security import TokenType, decode_token
from app.db.mongodb import get_mongodb_database
from app.db.postgres import get_async_session
from app.db.redis import get_redis_client
from app.models.user import User, UserRole
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    ToolCallLogRepository,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.ai_trace_service import AITraceService
from app.services.job_service import ReviewJobService
from app.services.job_queue_service import JobQueueService
from app.services.report_service import ReportService
from app.services.repository_service import RepositoryService
from app.services.token_blacklist import TokenBlacklistService

bearer_scheme = HTTPBearer(auto_error=False)
REVIEW_JOB_CREATE_RATE_LIMIT = 10
RATE_LIMIT_WINDOW_SECONDS = 60


async def get_redis() -> Redis:
    """Provide Redis to services that need cache or blacklist state."""

    return get_redis_client()


async def get_mongodb() -> AsyncIOMotorDatabase:
    """Provide the shared MongoDB database to repositories and services."""

    return get_mongodb_database()


async def get_file_analysis_result_repository(
    database: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> FileAnalysisResultRepository:
    """Build the MongoDB repository for file analysis results."""

    return FileAnalysisResultRepository(database)


async def get_raw_static_analysis_output_repository(
    database: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> RawStaticAnalysisOutputRepository:
    """Build the MongoDB repository for raw static analyzer outputs."""

    return RawStaticAnalysisOutputRepository(database)


async def get_tool_call_log_repository(
    database: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> ToolCallLogRepository:
    """Build the MongoDB repository for AI tool call logs."""

    return ToolCallLogRepository(database)


async def get_chunk_metadata_repository(
    database: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> ChunkMetadataRepository:
    """Build the MongoDB repository for code chunk metadata."""

    return ChunkMetadataRepository(database)


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    """Build auth service with request-scoped DB and shared Redis clients."""

    user_repository = UserRepository(session)
    refresh_token_repository = RefreshTokenRepository(session)
    token_blacklist_service = TokenBlacklistService(redis_client)
    return AuthService(
        user_repository,
        refresh_token_repository,
        token_blacklist_service,
    )


async def get_repository_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> RepositoryService:
    """Build repository service with request-scoped DB access."""

    repository_repository = RepositoryRepository(session)
    return RepositoryService(repository_repository)


async def get_review_job_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ReviewJobService:
    """Build review job service with request-scoped DB access."""

    review_job_repository = ReviewJobRepository(session)
    repository_repository = RepositoryRepository(session)
    job_queue_service = JobQueueService()
    return ReviewJobService(
        review_job_repository,
        repository_repository,
        job_queue_service,
    )


async def get_ai_trace_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    database: Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)],
) -> AITraceService:
    """Build the lightweight AI trace service."""

    return AITraceService(
        postgres_session=session,
        mongodb_database=database,
    )


async def get_report_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    chunk_metadata_repository: Annotated[
        ChunkMetadataRepository,
        Depends(get_chunk_metadata_repository),
    ],
) -> ReportService:
    """Build report service with request-scoped DB access."""

    report_repository = ReportRepository(session)
    return ReportService(report_repository, chunk_metadata_repository)


async def get_current_access_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    access_token_cookie: Annotated[
        str | None,
        Cookie(alias=get_settings().access_cookie_name),
    ] = None,
) -> str:
    """Extract the current access token from bearer auth or HttpOnly cookie."""

    if credentials is not None:
        return credentials.credentials

    if access_token_cookie is not None:
        return access_token_cookie

    raise AuthenticationError("Access token is required")


async def get_current_user(
    token: Annotated[str, Depends(get_current_access_token)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    """Decode JWT, reject blacklisted tokens, and load the active user."""

    decoded_token = decode_token(token, TokenType.ACCESS)
    is_blacklisted = await auth_service.token_blacklist_service.is_blacklisted(
        decoded_token["token_id"]
    )
    is_user_token_invalid = (
        await auth_service.token_blacklist_service.is_user_token_invalid(
            decoded_token["subject"],
            decoded_token["issued_at"],
        )
    )
    if is_blacklisted or is_user_token_invalid:
        raise AuthenticationError("Access token has been revoked")

    return await auth_service.get_active_user(decoded_token["subject"])


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require an authenticated admin user for protected admin endpoints."""

    if current_user.role != UserRole.ADMIN:
        raise AuthorizationError("Admin role is required")

    return current_user


async def rate_limit_review_job_create(
    current_user: Annotated[User, Depends(get_current_user)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> None:
    """Limit expensive review-job creation requests per authenticated user."""

    key = f"rate:{current_user.id}:review_jobs:create"
    try:
        request_count = await redis_client.incr(key)
        if request_count == 1:
            await redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        if request_count <= REVIEW_JOB_CREATE_RATE_LIMIT:
            return

        ttl_seconds = await redis_client.ttl(key)
    except RedisError as error:
        raise ServiceUnavailableError("Rate limit store is unavailable") from error

    retry_after_seconds = ttl_seconds if ttl_seconds > 0 else RATE_LIMIT_WINDOW_SECONDS
    raise RateLimitError(
        "Too many review job creation requests",
        headers={"Retry-After": str(retry_after_seconds)},
    )
