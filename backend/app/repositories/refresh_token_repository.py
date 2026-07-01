"""Persistence operations for hashed refresh tokens."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    """Database access for refresh token rotation and revocation records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Return a refresh token record by its secure hash."""

        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        """Stage a new hashed refresh token record."""

        refresh_token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(refresh_token)
        await self.session.flush()
        return refresh_token

    async def revoke(
        self,
        refresh_token: RefreshToken,
        *,
        revoked_at: datetime,
        replaced_by_token_id: UUID | None = None,
    ) -> None:
        """Stage refresh token revocation metadata."""

        refresh_token.revoked_at = revoked_at
        refresh_token.replaced_by_token_id = replaced_by_token_id
        await self.session.flush()

    async def revoke_active_for_user(
        self,
        *,
        user_id: UUID,
        revoked_at: datetime,
    ) -> None:
        """Stage revocation for every active refresh token owned by a user."""

        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        await self.session.execute(statement)
        await self.session.flush()

    async def commit(self) -> None:
        """Persist all staged refresh-token changes."""

        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged refresh-token changes after an error."""

        await self.session.rollback()
