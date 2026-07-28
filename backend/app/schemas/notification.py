"""Schemas for realtime review-job progress events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

JobProgressEventType = Literal[
    "status_change",
    "progress_update",
    "log",
    "completed",
    "failed",
]


class JobProgressEvent(BaseModel):
    """One normalized progress event delivered through Redis and SSE."""

    job_id: UUID
    event: JobProgressEventType
    status: str
    progress: int = Field(ge=0, le=100)
    message: str
    timestamp: datetime
    data: dict[str, object] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Return whether the stream should close after this event."""

        return self.event in {"completed", "failed"}
