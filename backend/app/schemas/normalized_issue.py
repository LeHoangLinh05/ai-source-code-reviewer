"""Normalized issue schema shared by analyzers and report persistence."""

from typing import Any

from pydantic import BaseModel, Field

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource


class NormalizedIssue(BaseModel):
    """Tool-independent finding shape matching the review_issues table."""

    file_path: str | None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    severity: IssueSeverity
    category: IssueCategory
    title: str = Field(max_length=255)
    description: str
    suggestion: str | None = None
    source: IssueSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_output: dict[str, Any] | None = None
