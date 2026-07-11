"""Schemas for lightweight AI review execution visibility."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AIToolCallTrace(BaseModel):
    """One logged AI tool call."""

    sequence: int = Field(ge=1)
    tool_name: str
    called_at: datetime
    duration_ms: int = Field(ge=0)
    input: dict[str, object]
    output: dict[str, object]
    status: str


class AITraceCoverage(BaseModel):
    """Coverage counters that explain what the agent and pipeline touched."""

    total_reviewable_files: int = Field(ge=0)
    total_reviewable_lines: int = Field(ge=0)
    review_mode: str
    chunked_files: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    target_files: int = Field(ge=0)
    target_chunks: int = Field(ge=0)
    ai_read_files: int = Field(ge=0)
    ai_read_chunks: int = Field(ge=0)
    ai_read_target_chunks: int = Field(ge=0)
    ai_read_file_percent: float = Field(ge=0.0, le=100.0)
    ai_read_chunk_percent: float = Field(ge=0.0, le=100.0)
    static_analyzer_runs: int = Field(ge=0)
    static_analyzer_issues: int = Field(ge=0)
    generated_ai_issues: int = Field(ge=0)
    generated_report_by_ai: bool


class AITraceStage(BaseModel):
    """One visible stage in the review execution timeline."""

    key: str
    label: str
    status: str
    detail: str
    progress_percent: float = Field(ge=0.0, le=100.0)
    current: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class AITraceResponse(BaseModel):
    """Current AI review trace for one job."""

    job_id: UUID
    has_ai_started: bool
    tool_call_count: int = Field(ge=0)
    latest_tool_name: str | None = None
    latest_tool_status: str | None = None
    issue_counts_by_source: dict[str, int]
    ai_issue_count: int = Field(ge=0)
    static_issue_count: int = Field(ge=0)
    report_model: str | None = None
    report_created_at: datetime | None = None
    coverage: AITraceCoverage
    stages: list[AITraceStage]
    recent_tool_calls: list[AIToolCallTrace]
