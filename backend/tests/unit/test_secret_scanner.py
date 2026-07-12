"""Tests for regex secret scanning."""

from pathlib import Path

from app.analyzers.secret_scanner import SECRET_MASK, mask_secret_values, scan_secrets
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


def test_mask_secret_values_preserves_assignment_structure() -> None:
    content = "\n".join(
        [
            "API_KEY=sk-fake-secret-value",
            'password = "super-secret-password"',
        ]
    )

    masked_content = mask_secret_values(content)

    assert "sk-fake-secret-value" not in masked_content
    assert "super-secret-password" not in masked_content
    assert f"API_KEY={SECRET_MASK}" in masked_content
    assert f'password = "{SECRET_MASK}"' in masked_content
