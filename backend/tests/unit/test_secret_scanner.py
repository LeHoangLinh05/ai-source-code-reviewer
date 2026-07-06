"""Tests for regex secret scanning."""

from pathlib import Path

from app.analyzers.secret_scanner import scan_secrets
from app.models.review_issue import IssueSeverity, IssueSource


def test_scan_secrets_detects_and_redacts_secret_values(tmp_path: Path) -> None:
    source_file = tmp_path / "settings.py"
    source_file.write_text(
        "\n".join(
            [
                "AWS_KEY = 'AKIA1234567890ABCDEF'",
                "password = 'super-secret-password'",
            ]
        ),
        encoding="utf-8",
    )

    issues = scan_secrets(tmp_path, [source_file])

    assert len(issues) == 2
    assert {issue.source for issue in issues} == {IssueSource.SECRET_SCANNER}
    assert {issue.severity for issue in issues} == {IssueSeverity.CRITICAL}
    assert all(issue.raw_output is not None for issue in issues)
    assert all(
        issue.raw_output["redacted"] is True for issue in issues if issue.raw_output
    )
    assert "super-secret-password" not in str([issue.model_dump() for issue in issues])
