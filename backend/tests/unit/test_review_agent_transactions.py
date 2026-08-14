"""Transaction-boundary tests for long-running AI review work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.probe.review import _load_job_options
from app.ai.review.agent import _build_report_context
from app.repositories.report_repository import ReportRepository


@pytest.mark.asyncio
async def test_load_job_options_closes_read_transaction_before_ai_work() -> None:
    options = {"review_mode": "full_audit"}
    report_repository = MagicMock(spec=ReportRepository)
    report_repository.get_job_by_id = AsyncMock(
        return_value=SimpleNamespace(options=options)
    )
    postgres_session = AsyncMock(spec=AsyncSession)

    loaded_options = await _load_job_options(
        report_repository,
        postgres_session,
        uuid4(),
    )

    assert loaded_options == options
    assert loaded_options is not options
    postgres_session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_build_report_context_closes_read_transaction_before_ai_work() -> None:
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = []
    postgres_session = AsyncMock(spec=AsyncSession)
    postgres_session.execute.return_value = query_result

    report_context = await _build_report_context(
        job_id=uuid4(),
        postgres_session=postgres_session,
    )

    assert '"total_issues": 0' in report_context
    postgres_session.commit.assert_awaited_once_with()
