"""Expected review pipeline failures and safe user-facing messages."""

import subprocess
from http import HTTPStatus

HTTP_SERVER_ERROR_STATUS_UPPER_BOUND = 600
UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE = (
    "Review pipeline failed: An upstream service is temporarily unavailable. "
    "Please retry shortly."
)


class ReviewPipelineError(Exception):
    """Expected pipeline failure with a user-facing error message."""


class ReviewJobCanceled(Exception):
    """Raised when a review job was removed while the worker was running."""


def build_error_message(error: Exception) -> str:
    """Return a stable user-facing pipeline error message."""

    if isinstance(error, ReviewPipelineError):
        return sanitize_error_message(str(error))

    if isinstance(error, subprocess.TimeoutExpired):
        return f"Command timed out after {error.timeout}s"

    message = f"Review pipeline failed: {error}"
    if _is_server_error(error):
        return UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE

    return sanitize_error_message(message)


def sanitize_error_message(message: str) -> str:
    """Remove upstream HTML documents from a user-facing error message."""

    normalized_message = message.casefold()
    if "<html" in normalized_message and "</html>" in normalized_message:
        return UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE

    return message


def _is_server_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    return (
        isinstance(status_code, int)
        and HTTPStatus.INTERNAL_SERVER_ERROR
        <= status_code
        < HTTP_SERVER_ERROR_STATUS_UPPER_BOUND
    )
