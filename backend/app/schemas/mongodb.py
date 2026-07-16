"""MongoDB document schemas for analysis, agent trace, and roadmap data."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.repo_summary import RepoSummary


class FileTreeEntry(BaseModel):
    """One file entry discovered during structure analysis."""

    path: str
    language: str | None = None
    size_bytes: int = Field(ge=0)
    line_count: int = Field(ge=0)
    should_review: bool
    ignore_reason: str | None = None


class FileAnalysisResultDocument(BaseModel):
    """Project structure analysis stored for a review job."""

    job_id: UUID
    analyzed_at: datetime
    project_structure: dict[str, object]
    file_tree: list[FileTreeEntry]


class ParsedStaticIssue(BaseModel):
    """Normalized issue parsed from raw static analyzer output."""

    file_path: str
    line_start: int | None = Field(default=None, ge=1)
    col: int | None = Field(default=None, ge=1)
    rule_id: str | None = None
    message: str
    severity: str
    category: str


class RawStaticAnalysisOutputDocument(BaseModel):
    """Raw analyzer output kept before final issue normalization."""

    job_id: UUID
    tool: str
    language: str
    ran_at: datetime
    exit_code: int
    stdout: str
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    parsed_issues: list[ParsedStaticIssue] = Field(default_factory=list)


class ToolCallLogDocument(BaseModel):
    """Trace of one AI agent tool call for the debug timeline."""

    job_id: UUID
    session_id: UUID
    agent_type: Literal["review", "report"] = "review"
    sequence: int = Field(ge=1)
    tool_name: str
    called_at: datetime
    duration_ms: int = Field(ge=0)
    input: dict[str, object]
    output: dict[str, object]
    event_type: Literal["tool", "llm", "embedding", "pipeline"] = "tool"
    provider: str | None = None
    model: str | None = None
    phase: str | None = None
    token_usage: dict[str, int] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    status: str | None = None


class ChunkMetadataDocument(BaseModel):
    """Metadata for one AST-derived code chunk used by repository retrieval."""

    job_id: UUID
    file_path: str
    language: str
    chunk_type: str
    chunk_index: int = Field(ge=0)
    total_chunks: int = Field(ge=1)
    function_name: str | None = None
    class_name: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    imports: list[str] = Field(default_factory=list)
    module: str
    risk_area: str
    has_static_issues: bool = False
    token_count: int = Field(ge=0)
    chunk_text: str | None = None


class RepoSummaryResultDocument(RepoSummary):
    """Project overview generated for one repository review job."""

    repository_id: UUID
    job_id: UUID
    commit_sha: str
    generated_at: datetime
    model_used: str
