"""Tests for AI issue validation safety gates."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.tools.generate_issue import IssueValidationError, validate_issue_payload


def test_rejects_confidence_below_threshold() -> None:
    with pytest.raises(IssueValidationError, match="confidence"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="medium",
            category="bug",
            title="Possible bug",
            description="The issue is not certain enough.",
            suggestion=None,
            confidence=0.69,
            references=[],
        )


def test_rejects_security_issue_without_references() -> None:
    with pytest.raises(IssueValidationError, match="references"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="high",
            category="security",
            title="Missing validation",
            description="Security finding without RAG grounding.",
            suggestion=None,
            confidence=0.9,
            references=[],
        )


def test_rejects_requirement_category() -> None:
    with pytest.raises(IssueValidationError, match="requirement"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="high",
            category="requirement",
            title="Roadmap issue",
            description="Requirement is reserved for roadmap rules.",
            suggestion=None,
            confidence=1.0,
            references=[],
        )


def test_rejects_line_range_that_does_not_exist(tmp_path: Path) -> None:
    source_path = tmp_path / "app.py"
    source_path.write_text("print('hello')\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with (
        ai_tool_runtime(runtime),
        pytest.raises(
            IssueValidationError,
            match="does not exist",
        ),
    ):
        validate_issue_payload(
            file_path="app.py",
            line_start=2,
            line_end=2,
            severity="medium",
            category="bug",
            title="Invalid line",
            description="The line does not exist in the file.",
            suggestion=None,
            confidence=0.8,
            references=[],
        )
