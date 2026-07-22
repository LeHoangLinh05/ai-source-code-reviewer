"""Business workflows for authenticated user profile settings."""

from app.core.exceptions import AuthenticationError, BadRequestError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import ChangePasswordRequest, UserProfileUpdateRequest


class UserService:
    """Authenticated account settings operations."""

    def __init__(self, user_repository: UserRepository) -> None:
        self.user_repository = user_repository

    async def update_profile(
        self,
        user: User,
        payload: UserProfileUpdateRequest,
    ) -> User:
        """Update editable profile fields for the authenticated user."""

        try:
            updated_user = await self.user_repository.update_profile(
                user,
                full_name=payload.full_name,
            )
            await self.user_repository.commit()
        except Exception:
            await self.user_repository.rollback()
            raise

        return updated_user

    async def change_password(
        self,
        user: User,
        payload: ChangePasswordRequest,
    ) -> None:
        """Validate the current password and store a new password hash."""

        if not verify_password(payload.current_password, user.hashed_password):
            raise AuthenticationError("Current password is incorrect")

        if verify_password(payload.new_password, user.hashed_password):
            raise BadRequestError("New password must be different")

        try:
            await self.user_repository.update_password(
                user,
                hashed_password=hash_password(payload.new_password),
            )
            await self.user_repository.commit()
        except Exception:
            await self.user_repository.rollback()
            raise
