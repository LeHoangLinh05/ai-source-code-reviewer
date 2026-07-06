"""Tests for static analyzer normalization adapters."""

from pathlib import Path
import json

from app.analyzers.static_analysis.bandit_analyzer import bandit_to_normalized
from app.analyzers.static_analysis.eslint_analyzer import eslint_to_normalized
from app.analyzers.static_analysis.ruff_analyzer import ruff_to_normalized
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource


def test_ruff_to_normalized_maps_json_findings() -> None:
    sandbox_path = Path("/tmp/sandbox/job")
    file_path = sandbox_path / "src" / "app.py"
    issues = ruff_to_normalized(
        json.dumps(
            [
                {
                    "filename": str(file_path),
                    "code": "F401",
                    "message": "unused import",
                    "location": {"row": 2, "column": 1},
                    "end_location": {"row": 2, "column": 10},
                }
            ]
        ),
        sandbox_path,
    )

    assert issues[0].file_path == "src/app.py"
    assert issues[0].source == IssueSource.RUFF
    assert issues[0].category == IssueCategory.STYLE
    assert issues[0].line_start == 2
    assert issues[0].raw_output is not None
    assert issues[0].raw_output["code"] == "F401"


def test_bandit_to_normalized_maps_high_security_findings() -> None:
    sandbox_path = Path("/tmp/sandbox/job")
    file_path = sandbox_path / "src" / "app.py"
    issues = bandit_to_normalized(
        json.dumps(
            {
                "results": [
                    {
                        "filename": str(file_path),
                        "line_number": 5,
                        "issue_severity": "HIGH",
                        "issue_confidence": "HIGH",
                        "test_id": "B105",
                        "test_name": "hardcoded_password_string",
                        "issue_text": "Possible hardcoded password",
                    }
                ]
            }
        ),
        sandbox_path,
    )

    assert issues[0].file_path == "src/app.py"
    assert issues[0].source == IssueSource.BANDIT
    assert issues[0].severity == IssueSeverity.HIGH
    assert issues[0].category == IssueCategory.SECURITY
    assert issues[0].confidence == 0.9


def test_eslint_to_normalized_maps_absolute_paths(tmp_path: Path) -> None:
    file_path = tmp_path / "src" / "index.ts"
    stdout = json.dumps(
        [
            {
                "filePath": str(file_path),
                "messages": [
                    {
                        "line": 3,
                        "severity": 2,
                        "ruleId": "no-undef",
                        "message": "x is not defined",
                    }
                ],
            }
        ]
    )

    issues = eslint_to_normalized(stdout, tmp_path)

    assert issues[0].file_path == "src/index.ts"
    assert issues[0].source == IssueSource.ESLINT
    assert issues[0].severity == IssueSeverity.MEDIUM
