"""Persistence operations for user accounts."""

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
        role: UserRole = UserRole.USER,
    ) -> User:
        """Persist a new user account."""

        user = User(
            email=email,
            hashed_password=hashed_password,
            role=role,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user
