"""Tests for fix patch generation routing."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.services.fix_pipeline import generation
from app.services.fix_pipeline.generation import generate_fix_changes


@pytest.mark.asyncio
async def test_generate_fix_changes_sends_non_autofix_ruff_to_ai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ruff_without_fix = _build_issue(
        source=IssueSource.RUFF,
        raw_output={"code": "B008"},
    )
    ruff_with_fix = _build_issue(
        source=IssueSource.RUFF,
        raw_output={"code": "F401", "fix": {"message": "Remove unused import."}},
    )
    ai_issue = _build_issue(source=IssueSource.AI_REVIEW)
    ai_routed_issue_ids: list[str] = []

    def apply_ruff_fixes(**_kwargs: object) -> None:
        return None

    async def apply_ai_file_fixes(
        *,
        sandbox_path: Path,
        issues: list[ReviewIssue],
    ) -> None:
        del sandbox_path
        ai_routed_issue_ids.extend(str(issue.id) for issue in issues)

    monkeypatch.setattr(generation, "apply_ruff_fixes", apply_ruff_fixes)
    monkeypatch.setattr(generation, "apply_ai_file_fixes", apply_ai_file_fixes)

    await generate_fix_changes(
        sandbox_path=tmp_path,
        issues=[ruff_without_fix, ruff_with_fix, ai_issue],
        timeout_seconds=1,
    )

    assert ai_routed_issue_ids == [str(ruff_without_fix.id), str(ai_issue.id)]


def _build_issue(
    *,
    source: IssueSource,
    raw_output: dict[str, object] | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        id=uuid4(),
        job_id=uuid4(),
        file_path="app/api/auth.py",
        line_start=1,
        line_end=1,
        severity=IssueSeverity.LOW,
        category=IssueCategory.STYLE,
        title="Finding",
        description="Finding description",
        suggestion=None,
        source=source,
        confidence=0.9,
        raw_output=raw_output,
    )
