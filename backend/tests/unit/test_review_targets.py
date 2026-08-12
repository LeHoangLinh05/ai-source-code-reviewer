"""Tests for repository paths that may receive review findings."""

from pathlib import Path

from app.core.review_targets import is_readme_path, is_review_target_path


def test_readme_documents_are_context_only_review_paths() -> None:
    assert is_readme_path("README.md")
    assert is_readme_path("docs/README.en.md")
    assert is_readme_path(Path("guide/ReadMe.rst"))
    assert not is_review_target_path("README.markdown")


def test_source_files_with_readme_in_the_name_remain_reviewable() -> None:
    assert not is_readme_path("app/readme_helper.py")
    assert is_review_target_path("app/readme_helper.py")
