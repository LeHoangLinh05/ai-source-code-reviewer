"""Tests for review pipeline configuration helpers."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.ai.rules.roadmap_checker import RULE_PROFILE_ID
from app.services.review_pipeline_service import (
    ReviewPipelineError,
    build_plain_file_chunk_metadata,
    get_rule_profile,
)


def test_get_rule_profile_defaults_to_full_roadmap_when_options_missing() -> None:
    assert get_rule_profile(None) == {"id": RULE_PROFILE_ID}


def test_get_rule_profile_defaults_to_full_roadmap_when_key_missing() -> None:
    assert get_rule_profile({"run_static_analysis": True}) == {"id": RULE_PROFILE_ID}


def test_get_rule_profile_preserves_explicit_profile() -> None:
    rule_profile = {"id": RULE_PROFILE_ID, "weeks_included": [1, 2]}

    assert get_rule_profile({"rule_profile": rule_profile}) == rule_profile


def test_get_rule_profile_rejects_invalid_profile_shape() -> None:
    with pytest.raises(ReviewPipelineError, match="rule_profile option"):
        get_rule_profile({"rule_profile": "roadmap_bootcamp_v1"})


def test_plain_text_files_are_chunked_for_ai_coverage(tmp_path: Path) -> None:
    source_path = tmp_path / "frontend" / "app.tsx"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "export function App() {\n  return null\n}\n", encoding="utf-8"
    )

    metadata = build_plain_file_chunk_metadata(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=source_path,
        issues=[],
    )

    assert metadata.file_path == "frontend/app.tsx"
    assert metadata.chunk_index == 0
    assert metadata.total_chunks == 1
    assert metadata.chunk_text == "export function App() {\n  return null\n}\n"
