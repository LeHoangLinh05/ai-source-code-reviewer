"""Expected review pipeline failures and safe user-facing messages."""

import subprocess


class ReviewPipelineError(Exception):
    """Expected pipeline failure with a user-facing error message."""


class ReviewJobCanceled(Exception):
    """Raised when a review job was removed while the worker was running."""


def build_error_message(error: Exception) -> str:
    """Return a stable user-facing pipeline error message."""

    if isinstance(error, ReviewPipelineError):
        return str(error)

    if isinstance(error, subprocess.TimeoutExpired):
        return f"Command timed out after {error.timeout}s"

    return f"Review pipeline failed: {error}"
