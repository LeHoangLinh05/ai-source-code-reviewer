"""Tests for safe review pipeline error messages."""

from app.services.review_pipeline.errors import (
    UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE,
    build_error_message,
    sanitize_error_message,
)


def test_build_error_message_sanitizes_upstream_server_error() -> None:
    error = RuntimeError("<html><body>502 Server Error</body></html>")
    error.status_code = 502  # type: ignore[attr-defined]

    assert build_error_message(error) == UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE


def test_sanitize_error_message_hides_stored_html_document() -> None:
    stored_message = (
        "Review pipeline failed: <html><body>502 Server Error</body></html>"
    )

    assert sanitize_error_message(stored_message) == (
        UPSTREAM_SERVICE_UNAVAILABLE_MESSAGE
    )


def test_build_error_message_preserves_regular_failure() -> None:
    assert build_error_message(RuntimeError("embedding dependency failed")) == (
        "Review pipeline failed: embedding dependency failed"
    )
