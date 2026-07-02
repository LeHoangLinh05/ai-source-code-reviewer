"""Health check response schemas."""

from pydantic import BaseModel


class HealthServiceStatus(BaseModel):
    """Status for one infrastructure dependency."""

    status: str


class HealthResponse(BaseModel):
    """Application health response."""

    status: str
    services: dict[str, HealthServiceStatus]
