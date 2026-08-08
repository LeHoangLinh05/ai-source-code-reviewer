"""Tests for review job option defaults and validation."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.review_job import ReviewJobCreate, ReviewJobOptions


def test_review_job_options_default_to_full_audit_without_roadmap() -> None:
    payload = ReviewJobCreate(repository_id=uuid4(), branch="main")

    assert payload.options.model_dump(mode="json") == {
        "review_mode": "full_audit",
        "rule_profile": None,
    }


def test_review_job_options_allow_explicit_smart_mode() -> None:
    payload = ReviewJobCreate(
        repository_id=uuid4(),
        options=ReviewJobOptions(review_mode="smart"),
    )

    assert payload.options.review_mode == "smart"


def test_review_job_options_reject_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        ReviewJobCreate.model_validate(
            {
                "repository_id": uuid4(),
                "options": {"review_mode": "partial"},
            },
        )


def test_review_job_accepts_and_normalizes_pinned_commit_sha() -> None:
    payload = ReviewJobCreate(
        repository_id=uuid4(),
        commit_sha="ABCDEF0123456789ABCDEF0123456789ABCDEF01",
    )

    assert payload.commit_sha == "abcdef0123456789abcdef0123456789abcdef01"


def test_review_job_rejects_invalid_pinned_commit_sha() -> None:
    with pytest.raises(ValidationError):
        ReviewJobCreate(
            repository_id=uuid4(),
            commit_sha="not-a-full-commit-sha",
        )
