"""Persistence operations for tracked source repositories."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.repository import Repository, RepositoryPlatform


class RepositoryRepository:
    """Database access for repository records without business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        name: str,
        url: str,
        platform: RepositoryPlatform,
        default_branch: str,
        description: str | None,
    ) -> Repository:
        """Persist a source repository for a user."""

        source_repository = Repository(
            user_id=user_id,
            name=name,
            url=url,
            platform=platform,
            default_branch=default_branch,
            description=description,
        )
        self.session.add(source_repository)
        await self.session.commit()
        await self.session.refresh(source_repository)
        return source_repository

    async def get_by_id(self, repository_id: UUID) -> Repository | None:
        """Return a repository by primary key."""

        return await self.session.get(Repository, repository_id)

    async def list_for_user(self, user_id: UUID) -> list[Repository]:
        """Return repositories owned by a user, newest first."""

        statement = (
            select(Repository)
            .where(Repository.user_id == user_id)
            .order_by(Repository.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_all(self) -> list[Repository]:
        """Return all repositories, newest first."""

        statement = select(Repository).order_by(Repository.created_at.desc())
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def delete(self, source_repository: Repository) -> None:
        """Delete a repository and cascade dependent review data."""

        await self.session.delete(source_repository)
        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged repository changes after an error."""

        await self.session.rollback()
