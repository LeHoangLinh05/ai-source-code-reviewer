"""Persistence operations for connected provider installations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_installation import ProviderInstallation
from app.models.repository import RepositoryPlatform


class ProviderInstallationRepository:
    """Database access for Git provider installation records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        user_id: UUID,
        provider: RepositoryPlatform,
        installation_id: str,
        account_login: str,
        account_type: str | None,
        repository_selection: str | None,
        permissions: dict[str, object] | None,
    ) -> ProviderInstallation:
        """Create or update a provider installation for a user."""

        installation = await self.get_by_installation_id(
            user_id=user_id,
            provider=provider,
            installation_id=installation_id,
        )
        if installation is None:
            installation = ProviderInstallation(
                user_id=user_id,
                provider=provider,
                installation_id=installation_id,
            )
            self.session.add(installation)

        installation.account_login = account_login
        installation.account_type = account_type
        installation.repository_selection = repository_selection
        installation.permissions = permissions
        await self.session.commit()
        await self.session.refresh(installation)
        return installation

    async def get_by_installation_id(
        self,
        *,
        user_id: UUID,
        provider: RepositoryPlatform,
        installation_id: str,
    ) -> ProviderInstallation | None:
        """Return one installation by provider-native ID."""

        statement = select(ProviderInstallation).where(
            ProviderInstallation.user_id == user_id,
            ProviderInstallation.provider == provider,
            ProviderInstallation.installation_id == installation_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[ProviderInstallation]:
        """Return provider installations connected by a user."""

        statement = (
            select(ProviderInstallation)
            .where(ProviderInstallation.user_id == user_id)
            .order_by(ProviderInstallation.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id_for_user(
        self,
        *,
        connection_id: UUID,
        user_id: UUID,
    ) -> ProviderInstallation | None:
        """Return one provider connection owned by a user."""

        statement = select(ProviderInstallation).where(
            ProviderInstallation.id == connection_id,
            ProviderInstallation.user_id == user_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_primary_for_user_provider(
        self,
        *,
        user_id: UUID,
        provider: RepositoryPlatform,
    ) -> ProviderInstallation | None:
        """Return the newest installation for a user/provider pair."""

        statement = (
            select(ProviderInstallation)
            .where(
                ProviderInstallation.user_id == user_id,
                ProviderInstallation.provider == provider,
            )
            .order_by(ProviderInstallation.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def delete(self, installation: ProviderInstallation) -> None:
        """Delete a local provider connection."""

        await self.session.delete(installation)
        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged provider installation changes."""

        await self.session.rollback()
