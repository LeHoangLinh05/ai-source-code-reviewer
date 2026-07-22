"""User profile and settings API routes."""

from fastapi import APIRouter

from app.core.dependencies import CurrentUserDep, UserServiceDep
from app.models.user import User
from app.schemas.user import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    UserProfileResponse,
    UserProfileUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["users"])
ACCOUNT_CREDENTIAL_UPDATED_MESSAGE = "Password updated"


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get current user profile",
)
async def get_current_profile(
    current_user: CurrentUserDep,
) -> User:
    """Return editable profile data for the authenticated user."""

    return current_user


@router.patch(
    "/me",
    response_model=UserProfileResponse,
    summary="Update current user profile",
)
async def update_current_profile(
    payload: UserProfileUpdateRequest,
    current_user: CurrentUserDep,
    user_service: UserServiceDep,
) -> UserProfileResponse:
    """Update editable profile fields for the authenticated user."""

    updated_user = await user_service.update_profile(current_user, payload)
    return UserProfileResponse.model_validate(updated_user)


@router.post(
    "/me/password",
    response_model=ChangePasswordResponse,
    summary="Change current user password",
)
async def change_current_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUserDep,
    user_service: UserServiceDep,
) -> ChangePasswordResponse:
    """Change password after validating the current password."""

    await user_service.change_password(current_user, payload)
    return ChangePasswordResponse(message=ACCOUNT_CREDENTIAL_UPDATED_MESSAGE)
