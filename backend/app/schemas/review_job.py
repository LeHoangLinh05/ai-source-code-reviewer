"""Review job request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.review_job import ReviewJobStatus


class ReviewJobCreate(BaseModel):
    """Payload for starting a repository review job."""

    repository_id: UUID
    branch: str | None = Field(default=None, max_length=100)
    options: dict[str, object] = Field(default_factory=dict)

    @field_validator("branch")
    @classmethod
    def strip_optional_branch(cls, value: str | None) -> str | None:
        """Normalize optional branch names."""

        if value is None:
            return None

        stripped_value = value.strip()
        return stripped_value or None


class ReviewJobCreateResponse(BaseModel):
    """Response returned after creating a review job."""

    job_id: UUID
    status: ReviewJobStatus
    created_at: datetime
    stream_url: str


class ReviewJobResponse(BaseModel):
    """Review job fields returned by API endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_id: UUID
    repository_name: str | None = None
    user_id: UUID
    status: ReviewJobStatus
    branch: str | None
    commit_sha: str | None
    error_message: str | None
    options: dict[str, object] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    stream_url: str


class ReviewJobCancelResponse(BaseModel):
    """Response returned after canceling a review job."""

    message: str


class ReviewJobStatusUpdate(BaseModel):
    """Dev-only payload for simulating review job progress."""

    status: ReviewJobStatus
    message: str | None = Field(default=None, max_length=255)
    progress: int = Field(default=0, ge=0, le=100)
