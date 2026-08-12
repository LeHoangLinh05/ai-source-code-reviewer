"""Persistence operations for user accounts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


class UserRepository:
    """Database access for user records without business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Return a user by primary key."""

        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return a user by normalized email address."""

        statement = select(User).where(User.email == email)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
    ) -> User:
        """Persist a new user account."""

        user = User(
            email=email,
            hashed_password=hashed_password,
            role=UserRole.USER,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def mark_refresh_tokens_revoked(
        self,
        user: User,
        *,
        revoked_at: datetime,
    ) -> None:
        """Stage the user-wide refresh-token revocation cutoff."""

        user.refresh_tokens_revoked_at = revoked_at
        await self.session.flush()

    async def update_profile(
        self,
        user: User,
        *,
        full_name: str | None,
    ) -> User:
        """Stage editable profile field changes."""

        user.full_name = full_name
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update_password(
        self,
        user: User,
        *,
        hashed_password: str,
    ) -> None:
        """Stage a password hash replacement."""

        user.hashed_password = hashed_password
        await self.session.flush()

    async def commit(self) -> None:
        """Persist all staged user changes."""

        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged user changes after an error."""

        await self.session.rollback()
