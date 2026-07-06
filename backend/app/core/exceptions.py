"""Custom exception types and error handling helpers."""

from fastapi import status


class AppError(Exception):
    """Base application error that can be mapped to an HTTP response."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Internal server error"

    def __init__(
        self,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail or self.detail
        self.headers = headers
        super().__init__(self.detail)


class AuthenticationError(AppError):
    """Raised when credentials or tokens are invalid."""

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Authentication failed"


class AuthorizationError(AppError):
    """Raised when an authenticated user lacks permission."""

    status_code = status.HTTP_403_FORBIDDEN
    detail = "Permission denied"


class BadRequestError(AppError):
    """Raised when a request payload or query parameter is invalid."""

    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Invalid request"


class ConflictError(AppError):
    """Raised when a requested resource already exists."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Resource already exists"


class InactiveUserError(AuthenticationError):
    """Raised when a valid token belongs to a disabled user."""

    detail = "User account is inactive"


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Resource not found"


class ServiceUnavailableError(AppError):
    """Raised when a required infrastructure service is unavailable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Required service is unavailable"


class RateLimitError(AppError):
    """Raised when a user exceeds an endpoint-specific request budget."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    detail = "Rate limit exceeded"
