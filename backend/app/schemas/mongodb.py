"""MongoDB document schemas for analysis, agent trace, and roadmap data."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
    sequence: int = Field(ge=1)
    tool_name: str
    called_at: datetime
    duration_ms: int = Field(ge=0)
    input: dict[str, object]
    output: dict[str, object]


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


class RoadmapRuleResult(BaseModel):
    """Deterministic roadmap rule result stored for scoring and debugging."""

    rule_id: str
    status: str
    severity: str | None = None
    week: int | str
    skill_group: str


class RoadmapVerificationQueueItem(BaseModel):
    """Rule verification candidate that the AI agent must inspect later."""

    rule_id: str
    file_path: str
    ai_hint: str


class RoadmapComplianceResultDocument(BaseModel):
    """Roadmap compliance output stored when a rule profile is enabled."""

    job_id: UUID
    rule_profile: dict[str, object]
    checked_at: datetime
    results: list[RoadmapRuleResult]
    verification_queue: list[RoadmapVerificationQueueItem] = Field(default_factory=list)
    compliance_score: float | None = Field(default=None, ge=0.0, le=100.0)
    bonus_score: float | None = Field(default=None, ge=0.0, le=100.0)
