"""Pydantic schemas for repository project overview summaries."""

from datetime import datetime

from pydantic import BaseModel


class RepoSummary(BaseModel):
    """Structured project overview generated for a repository."""

    purpose: str
    project_type: str
    tech_stack: list[str]
    architecture_overview: str


class RepoSummaryResponse(RepoSummary):
    """Latest generated repository summary returned by the API."""

    generated_at: datetime
    commit_sha: str
    model_used: str
