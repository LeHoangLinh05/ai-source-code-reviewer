"""Report and issue response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource


class ReportResponse(BaseModel):
    """Full review report returned by the report endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    total_files_analyzed: int
    total_issues: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    info_count: int
    security_score: float | None
    maintainability_score: float | None
    performance_score: float | None
    overall_score: float | None
    tech_stack: dict[str, object] | None
    top_risky_files: list[dict[str, object]] | None
    executive_summary: str | None
    ai_model_used: str | None
    created_at: datetime


class ReportScores(BaseModel):
    """Score fields included in report summaries."""

    security_score: float | None
    maintainability_score: float | None
    performance_score: float | None
    overall_score: float | None


class ReportSummaryResponse(BaseModel):
    """Executive summary and scores for a report."""

    job_id: UUID
    executive_summary: str | None
    scores: ReportScores


class IssueResponse(BaseModel):
    """Normalized review issue returned by report endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    file_path: str
    line_start: int
    line_end: int
    severity: IssueSeverity
    category: IssueCategory
    title: str
    description: str
    suggestion: str | None
    source: IssueSource
    confidence: float | None
    raw_output: dict[str, object] | None
    created_at: datetime


class IssueFilters(BaseModel):
    """Filters applied to an issue list request."""

    severity: IssueSeverity | None = None
    category: IssueCategory | None = None
    source: IssueSource | None = None
    file_path: str | None = None


class IssueListResponse(BaseModel):
    """Paginated issue list returned by the report endpoint."""

    total: int
    page: int
    per_page: int
    sort: str
    filters: IssueFilters
    issues: list[IssueResponse]
