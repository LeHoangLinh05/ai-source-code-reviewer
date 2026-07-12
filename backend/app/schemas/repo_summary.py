"""Pydantic schemas for repository project overview summaries."""

from datetime import datetime

from pydantic import BaseModel


class KeyModule(BaseModel):
    """Important module or directory in a repository."""

    path: str
    name: str
    description: str


class EntryPoint(BaseModel):
    """Application entry point discovered for a repository."""

    path: str
    description: str


class RepoSummary(BaseModel):
    """Structured project overview generated for a repository."""

    purpose: str
    project_type: str
    tech_stack: list[str]
    architecture_overview: str
    key_modules: list[KeyModule]
    entry_points: list[EntryPoint]
    notable_setup: list[str]


class RepoSummaryResponse(RepoSummary):
    """Latest generated repository summary returned by the API."""

    generated_at: datetime
    commit_sha: str
    model_used: str
