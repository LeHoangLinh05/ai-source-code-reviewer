"""FastAPI application entrypoint for RepoGuard AI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.redis import close_redis_client
from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.repositories import router as repositories_router
from app.routers.reports import router as reports_router
from app.routers.review_jobs import router as review_jobs_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage shared infrastructure clients during app startup/shutdown."""

    yield
    await close_redis_client()


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url=f"{settings.api_prefix}/docs",
    openapi_url=f"{settings.api_prefix}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_origin_regex=settings.cors_allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
    """Convert domain errors into stable JSON API responses."""

    headers = None
    if error.status_code == 401:
        headers = {"WWW-Authenticate": "Bearer"}

    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail},
        headers=headers,
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    """Return a stable JSON response for unexpected errors during development."""

    logger.exception("Unhandled backend error")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(repositories_router, prefix=settings.api_prefix)
app.include_router(review_jobs_router, prefix=settings.api_prefix)
app.include_router(reports_router, prefix=settings.api_prefix)
