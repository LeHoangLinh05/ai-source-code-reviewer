"""Cross-user ownership checks for every user-visible review resource."""

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request
from redis.asyncio import Redis

from app.core.exceptions import AuthorizationError
from app.models.review_job import ReviewJobStatus
from app.models.user import User
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    RepoSummaryResultRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.routers.fix_jobs import stream_fix_job_progress_route
from app.routers.notifications import stream_review_job_progress
from app.routers.review_jobs import get_review_job_ai_trace
from app.services.ai_trace.service import AITraceService
from app.services.fix_job_service import FixJobService
from app.services.job_queue_service import JobQueueService
from app.services.job_service import ReviewJobService
from app.services.repo_summary.query_service import RepoSummaryQueryService
from app.services.reporting.service import ReportService
from app.services.repository_service import RepositoryService


def build_users() -> tuple[SimpleNamespace, SimpleNamespace]:
    return SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_repository_is_owner_only() -> None:
    owner, other_user = build_users()
    repository = SimpleNamespace(id=uuid4(), user_id=owner.id)

    class FakeRepositoryRepository:
        async def get_by_id(self, _repository_id: object) -> object:
            return repository

    service = RepositoryService(
        cast(RepositoryRepository, FakeRepositoryRepository()),
    )

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await service.get_repository(repository.id, cast(User, other_user))


@pytest.mark.asyncio
async def test_job_is_owner_only() -> None:
    owner, other_user = build_users()
    job = SimpleNamespace(
        id=uuid4(),
        user_id=owner.id,
        status=ReviewJobStatus.PENDING,
    )

    class FakeReviewJobRepository:
        async def get_by_id(self, _job_id: object) -> object:
            return job

    service = ReviewJobService(
        cast(ReviewJobRepository, FakeReviewJobRepository()),
        cast(RepositoryRepository, SimpleNamespace()),
        cast(JobQueueService, SimpleNamespace()),
    )

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await service._get_authorized_job(job.id, cast(User, other_user))


@pytest.mark.asyncio
async def test_report_is_owner_only() -> None:
    owner, other_user = build_users()
    job = SimpleNamespace(id=uuid4(), user_id=owner.id)

    class FakeReportRepository:
        async def get_job_by_id(self, _job_id: object) -> object:
            return job

    service = ReportService(
        cast(ReportRepository, FakeReportRepository()),
        cast(ChunkMetadataRepository, SimpleNamespace()),
    )

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await service._ensure_job_access(job.id, cast(User, other_user))


@pytest.mark.asyncio
async def test_repository_summary_is_owner_only() -> None:
    _owner, other_user = build_users()

    class RepositoryServiceStub:
        async def get_repository(self, _repository_id: object, _user: object) -> None:
            raise AuthorizationError("Repository access is restricted to its owner")

    service = RepoSummaryQueryService(
        cast(RepositoryService, RepositoryServiceStub()),
        cast(RepoSummaryResultRepository, SimpleNamespace()),
    )

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await service.get_latest_summary(uuid4(), cast(User, other_user))


@pytest.mark.asyncio
async def test_ai_trace_route_checks_job_ownership_before_loading_trace() -> None:
    _owner, other_user = build_users()

    class JobServiceStub:
        async def get_job(self, _job_id: object, _user: object) -> None:
            raise AuthorizationError("Review job access is restricted to its owner")

    class AITraceServiceStub:
        called = False

        async def get_trace(self, _job_id: object) -> None:
            self.called = True

    trace_service = AITraceServiceStub()
    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await get_review_job_ai_trace(
            uuid4(),
            cast(User, other_user),
            cast(ReviewJobService, JobServiceStub()),
            cast(AITraceService, trace_service),
        )
    assert not trace_service.called


@pytest.mark.asyncio
async def test_sse_route_checks_job_ownership_before_subscribing() -> None:
    _owner, other_user = build_users()

    class JobServiceStub:
        async def get_progress_snapshot(self, _job_id: object, _user: object) -> None:
            raise AuthorizationError("Review job access is restricted to its owner")

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await stream_review_job_progress(
            uuid4(),
            cast(Request, SimpleNamespace()),
            cast(User, other_user),
            cast(ReviewJobService, JobServiceStub()),
            cast(Redis, SimpleNamespace()),
        )


@pytest.mark.asyncio
async def test_fix_sse_route_checks_owner_before_subscribing() -> None:
    _owner, other_user = build_users()

    class FixJobServiceStub:
        async def get_progress_snapshot(self, _fix_id: object, _user: object) -> None:
            raise AuthorizationError("Fix job access is restricted to its owner")

    with pytest.raises(AuthorizationError, match="restricted to its owner"):
        await stream_fix_job_progress_route(
            uuid4(),
            cast(Request, SimpleNamespace()),
            cast(User, other_user),
            cast(FixJobService, FixJobServiceStub()),
            cast(Redis, SimpleNamespace()),
        )
