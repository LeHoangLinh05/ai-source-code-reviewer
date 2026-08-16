"""Provider connection API schemas."""

from uuid import UUID

from pydantic import BaseModel

from app.models.repository import RepositoryPlatform


class RepositoryProviderStatus(BaseModel):
    """Provider readiness for publishing fixes from one tracked repository."""

    repository_id: UUID
    provider: RepositoryPlatform | None
    is_connected: bool
    can_publish: bool
    installation_id: str | None = None
    account_login: str | None = None
    message: str
