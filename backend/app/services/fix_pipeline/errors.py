"""Fix pipeline exception helpers."""


class FixPipelineError(RuntimeError):
    """Raised when a fix job cannot generate or validate a patch."""


def build_fix_error_message(error: Exception) -> str:
    """Return a stable operator-readable fix pipeline error."""

    if isinstance(error, FixPipelineError):
        return str(error)

    return f"Fix pipeline failed: {error}"
