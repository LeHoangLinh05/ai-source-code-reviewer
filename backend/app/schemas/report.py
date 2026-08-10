"""Report and issue response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource


class ReportResponse(BaseModel):
    """Full review report returned by the report endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    total_files_analyzed: int
    total_issues: int
    total_findings: int = 0
    total_occurrences: int = 0
    total_raw_issues: int = 0
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    info_count: int
    security_score: float | None = Field(default=None, deprecated=True)
    maintainability_score: float | None = Field(default=None, deprecated=True)
    performance_score: float | None = Field(default=None, deprecated=True)
    overall_score: float | None = Field(default=None, deprecated=True)
    tech_stack: dict[str, object] | None
    top_risky_files: list[dict[str, object]] | None
    executive_summary: str | None
    ai_model_used: str | None
    created_at: datetime


class ReportScores(BaseModel):
    """Deprecated score fields retained as nullable API compatibility fields."""

    security_score: float | None = Field(default=None, deprecated=True)
    maintainability_score: float | None = Field(default=None, deprecated=True)
    performance_score: float | None = Field(default=None, deprecated=True)
    overall_score: float | None = Field(default=None, deprecated=True)


class ReportSummaryResponse(BaseModel):
    """Executive summary, canonical counts, and deprecated score compatibility."""

    job_id: UUID
    executive_summary: str | None
    scores: ReportScores
    total_findings: int = 0
    total_occurrences: int = 0
    total_raw_issues: int = 0


class IssueOccurrenceResponse(BaseModel):
    """One concrete source location for a grouped issue."""

    issue_id: UUID
    raw_issue_ids: list[UUID] = Field(default_factory=list)
    sources: list[IssueSource] = Field(default_factory=list)
    file_path: str
    line_start: int
    line_end: int
    title: str
    description: str
    suggestion: str | None
    confidence: float | None
    raw_output: dict[str, object] | None
    created_at: datetime


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
    group_key: str | None = None
    occurrence_count: int = 1
    raw_issue_count: int = 1
    affected_files: list[str] = Field(default_factory=list)
    primary_issue_id: UUID | None = None
    fix_issue_ids: list[UUID] = Field(default_factory=list)
    occurrences: list[IssueOccurrenceResponse] = Field(default_factory=list)


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
