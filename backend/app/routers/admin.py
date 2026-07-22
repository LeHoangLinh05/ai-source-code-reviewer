"""Administrative API routes for jobs, logs, and system health."""

from fastapi import APIRouter

from app.core.dependencies import CurrentAdminDep
from app.models.user import User
from app.schemas.auth import UserResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current admin user",
)
async def get_admin_me(current_admin: CurrentAdminDep) -> User:
    """Return the authenticated admin profile after RBAC authorization."""

    return current_admin
