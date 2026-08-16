"""FastAPI dependency providers shared across routers."""

from typing import Annotated

from fastapi import Cookie, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AuthenticationError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.security import TokenType, decode_token
from app.db.mongodb import get_mongodb_database
from app.db.postgres import get_async_session
from app.db.redis import get_redis_client
from app.models.user import User
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.health_repository import InfrastructureHealthRepository
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    RepoSummaryResultRepository,
    ToolCallLogRepository,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.repositories.user_repository import UserRepository
from app.services.ai_trace.service import AITraceService
from app.services.auth_service import AuthService
from app.services.fix_jobs.publish_queue import FixPublishQueueService
from app.services.fix_jobs.publish_service import FixPublishService
from app.services.fix_jobs.queue import FixJobQueueService
from app.services.fix_jobs.service import FixJobService
from app.services.health_service import HealthService
from app.services.provider_service import ProviderService
from app.services.repo_summary.query_service import RepoSummaryQueryService
from app.services.reporting.service import ReportService
from app.services.repository_service import RepositoryService
from app.services.review_jobs.queue import JobQueueService
from app.services.review_jobs.service import ReviewJobService
from app.services.token_blacklist import TokenBlacklistService
from app.services.user_service import UserService

bearer_scheme = HTTPBearer(auto_error=False)
REVIEW_JOB_CREATE_RATE_LIMIT = 10
RATE_LIMIT_WINDOW_SECONDS = 60
FIRST_RATE_LIMIT_REQUEST_COUNT = 1
RATE_LIMIT_REDIS_KEY_PREFIX = "rate"
REVIEW_JOB_CREATE_RATE_LIMIT_KEY_SUFFIX = "review_jobs:create"
RETRY_AFTER_HEADER = "Retry-After"


async def get_redis() -> Redis:
    """Provide Redis to services that need cache or blacklist state."""

    return get_redis_client()


async def get_mongodb() -> AsyncIOMotorDatabase:
    """Provide the shared MongoDB database to repositories and services."""

    return get_mongodb_database()


SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]
MongoDatabaseDep = Annotated[AsyncIOMotorDatabase, Depends(get_mongodb)]
AsyncSessionDep = Annotated[AsyncSession, Depends(get_async_session)]
BearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]
AccessTokenCookieDep = Annotated[
    str | None,
    Cookie(alias=get_settings().access_cookie_name),
]
RefreshTokenCookieDep = Annotated[
    str | None,
    Cookie(alias=get_settings().refresh_cookie_name),
]


async def get_file_analysis_result_repository(
    database: MongoDatabaseDep,
) -> FileAnalysisResultRepository:
    """Build the MongoDB repository for file analysis results."""

    return FileAnalysisResultRepository(database)


async def get_raw_static_analysis_output_repository(
    database: MongoDatabaseDep,
) -> RawStaticAnalysisOutputRepository:
    """Build the MongoDB repository for raw static analyzer outputs."""

    return RawStaticAnalysisOutputRepository(database)


async def get_tool_call_log_repository(
    database: MongoDatabaseDep,
) -> ToolCallLogRepository:
    """Build the MongoDB repository for AI tool call logs."""

    return ToolCallLogRepository(database)


async def get_chunk_metadata_repository(
    database: MongoDatabaseDep,
) -> ChunkMetadataRepository:
    """Build the MongoDB repository for code chunk metadata."""

    return ChunkMetadataRepository(database)


async def get_repo_summary_result_repository(
    database: MongoDatabaseDep,
) -> RepoSummaryResultRepository:
    """Build the MongoDB repository for generated repository summaries."""

    return RepoSummaryResultRepository(database)


RepoSummaryResultRepositoryDep = Annotated[
    RepoSummaryResultRepository,
    Depends(get_repo_summary_result_repository),
]
ChunkMetadataRepositoryDep = Annotated[
    ChunkMetadataRepository,
    Depends(get_chunk_metadata_repository),
]


async def get_auth_service(
    session: AsyncSessionDep,
    redis_client: RedisDep,
    settings: SettingsDep,
) -> AuthService:
    """Build auth service with request-scoped DB and shared Redis clients."""

    user_repository = UserRepository(session)
    refresh_token_repository = RefreshTokenRepository(session)
    token_blacklist_service = TokenBlacklistService(redis_client)
    return AuthService(
        user_repository=user_repository,
        refresh_token_repository=refresh_token_repository,
        token_blacklist_service=token_blacklist_service,
        settings=settings,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_repository_service(
    session: AsyncSessionDep,
) -> RepositoryService:
    """Build repository service with request-scoped DB access."""

    repository_repository = RepositoryRepository(session)
    return RepositoryService(repository_repository)


RepositoryServiceDep = Annotated[RepositoryService, Depends(get_repository_service)]


async def get_provider_service(
    session: AsyncSessionDep,
    settings: SettingsDep,
) -> ProviderService:
    """Build provider connection service with request-scoped DB access."""

    return ProviderService(
        settings=settings,
        repository_repository=RepositoryRepository(session),
    )


ProviderServiceDep = Annotated[ProviderService, Depends(get_provider_service)]


async def get_user_service(
    session: AsyncSessionDep,
) -> UserService:
    """Build user settings service with request-scoped DB access."""

    user_repository = UserRepository(session)
    return UserService(user_repository)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


async def get_health_service(
    session: AsyncSessionDep,
    redis_client: RedisDep,
    database: MongoDatabaseDep,
) -> HealthService:
    """Build the service that checks infrastructure health."""

    return HealthService(
        InfrastructureHealthRepository(
            postgres_session=session,
            redis_client=redis_client,
            mongodb_database=database,
        )
    )


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


async def get_repo_summary_query_service(
    repository_service: RepositoryServiceDep,
    repo_summary_repository: RepoSummaryResultRepositoryDep,
) -> RepoSummaryQueryService:
    """Build the query service for repository project overview summaries."""

    return RepoSummaryQueryService(repository_service, repo_summary_repository)


RepoSummaryQueryServiceDep = Annotated[
    RepoSummaryQueryService,
    Depends(get_repo_summary_query_service),
]


async def get_review_job_service(
    session: AsyncSessionDep,
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


ReviewJobServiceDep = Annotated[ReviewJobService, Depends(get_review_job_service)]


async def get_fix_job_service(
    session: AsyncSessionDep,
) -> FixJobService:
    """Build fix job service with request-scoped DB access."""

    return FixJobService(
        fix_job_repository=FixJobRepository(session),
        review_job_repository=ReviewJobRepository(session),
        report_repository=ReportRepository(session),
        queue_service=FixJobQueueService(),
        audit_log_repository=FixAuditLogRepository(session),
    )


FixJobServiceDep = Annotated[FixJobService, Depends(get_fix_job_service)]


async def get_fix_publish_service(
    session: AsyncSessionDep,
) -> FixPublishService:
    """Build fix publish service with request-scoped DB access."""

    return FixPublishService(
        fix_job_repository=FixJobRepository(session),
        audit_log_repository=FixAuditLogRepository(session),
        queue_service=FixPublishQueueService(),
    )


FixPublishServiceDep = Annotated[
    FixPublishService,
    Depends(get_fix_publish_service),
]


async def get_ai_trace_service(
    session: AsyncSessionDep,
    database: MongoDatabaseDep,
) -> AITraceService:
    """Build the lightweight AI trace service."""

    return AITraceService(
        review_job_repository=ReviewJobRepository(session),
        report_repository=ReportRepository(session),
        file_analysis_repository=FileAnalysisResultRepository(database),
        raw_static_repository=RawStaticAnalysisOutputRepository(database),
        tool_call_repository=ToolCallLogRepository(database),
        chunk_metadata_repository=ChunkMetadataRepository(database),
    )


AITraceServiceDep = Annotated[AITraceService, Depends(get_ai_trace_service)]


async def get_report_service(
    session: AsyncSessionDep,
    chunk_metadata_repository: ChunkMetadataRepositoryDep,
) -> ReportService:
    """Build report service with request-scoped DB access."""

    report_repository = ReportRepository(session)
    return ReportService(report_repository, chunk_metadata_repository)


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


async def get_current_access_token(
    credentials: BearerCredentialsDep,
    access_token_cookie: AccessTokenCookieDep = None,
) -> str:
    """Extract the current access token from bearer auth or HttpOnly cookie."""

    if credentials is not None:
        return credentials.credentials

    if access_token_cookie is not None:
        return access_token_cookie

    raise AuthenticationError("Access token is required")


AccessTokenDep = Annotated[str, Depends(get_current_access_token)]


async def get_current_user(
    token: AccessTokenDep,
    auth_service: AuthServiceDep,
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


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def build_review_job_create_rate_limit_key(user: User) -> str:
    """Build the Redis key for review-job creation throttling."""

    return (
        f"{RATE_LIMIT_REDIS_KEY_PREFIX}:{user.id}:"
        f"{REVIEW_JOB_CREATE_RATE_LIMIT_KEY_SUFFIX}"
    )


async def rate_limit_review_job_create(
    current_user: CurrentUserDep,
    redis_client: RedisDep,
) -> None:
    """Limit expensive review-job creation requests per authenticated user."""

    key = build_review_job_create_rate_limit_key(current_user)
    try:
        request_count = await redis_client.incr(key)
        if request_count == FIRST_RATE_LIMIT_REQUEST_COUNT:
            await redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        if request_count <= REVIEW_JOB_CREATE_RATE_LIMIT:
            return

        ttl_seconds = await redis_client.ttl(key)
    except RedisError as error:
        raise ServiceUnavailableError("Rate limit store is unavailable") from error

    retry_after_seconds = ttl_seconds if ttl_seconds > 0 else RATE_LIMIT_WINDOW_SECONDS
    raise RateLimitError(
        "Too many review job creation requests",
        headers={RETRY_AFTER_HEADER: str(retry_after_seconds)},
    )


ReviewJobCreateRateLimitDep = Annotated[
    None,
    Depends(rate_limit_review_job_create),
]
