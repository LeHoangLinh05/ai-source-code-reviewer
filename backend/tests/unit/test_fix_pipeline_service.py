"""Tests for fix pipeline orchestration helpers."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.models.fix_job import FixJob, FixValidationStatus
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import (
    FixValidationCheck,
    FixValidationCheckStatus,
    FixValidationResult,
)
from app.services.fix_pipeline import service as fix_pipeline_service
from app.services.fix_pipeline.service import FixPipelineService


@pytest.mark.asyncio
async def test_validate_and_repair_patch_revalidates_after_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failed_result = _validation_result(FixValidationStatus.FAILED)
    passed_result = _validation_result(FixValidationStatus.PASSED)
    validation_results = [failed_result, passed_result]
    validation_changed_files: list[list[str]] = []
    repair_calls: list[list[str]] = []

    def validate_fix(
        *,
        sandbox_path: Path,
        changed_files: list[str],
        timeout_seconds: int,
    ) -> FixValidationResult:
        del sandbox_path, timeout_seconds
        validation_changed_files.append(changed_files)
        return validation_results.pop(0)

    async def repair_validation_failures(
        *,
        sandbox_path: Path,
        changed_files: list[str],
        validation_result: FixValidationResult,
    ) -> bool:
        del sandbox_path, validation_result
        repair_calls.append(changed_files)
        return True

    async def publish_fix_job_progress(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(fix_pipeline_service, "validate_fix", validate_fix)
    monkeypatch.setattr(
        fix_pipeline_service,
        "repair_validation_failures",
        repair_validation_failures,
    )
    monkeypatch.setattr(
        fix_pipeline_service,
        "get_changed_files",
        lambda _sandbox_path: ["src/app.py", "src/auth.py"],
    )
    monkeypatch.setattr(
        fix_pipeline_service,
        "publish_fix_job_progress",
        publish_fix_job_progress,
    )
    service = FixPipelineService(
        settings=cast(
            Settings,
            SimpleNamespace(analysis_subprocess_timeout_seconds=30),
        ),
        fix_job_repository=cast(FixJobRepository, object()),
        report_repository=cast(ReportRepository, object()),
    )

    result, changed_files = await service._validate_and_repair_patch(
        fix_job=cast(FixJob, SimpleNamespace(id=uuid4())),
        sandbox_path=tmp_path,
        changed_files=["src/app.py"],
    )

    assert result.status == FixValidationStatus.PASSED
    assert changed_files == ["src/app.py", "src/auth.py"]
    assert validation_changed_files == [
        ["src/app.py"],
        ["src/app.py", "src/auth.py"],
    ]
    assert repair_calls == [["src/app.py"]]


def _validation_result(status: FixValidationStatus) -> FixValidationResult:
    check_status = (
        FixValidationCheckStatus.FAILED
        if status == FixValidationStatus.FAILED
        else FixValidationCheckStatus.PASSED
    )
    return FixValidationResult(
        status=status,
        summary="Validation summary",
        checks=[
            FixValidationCheck(
                name="ruff check",
                command="ruff check src/app.py",
                status=check_status,
                exit_code=0 if status == FixValidationStatus.PASSED else 1,
                stdout="",
                stderr="validation output",
                duration_ms=1,
            )
        ],
    )
