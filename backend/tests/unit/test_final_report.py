"""Tests for structured final-report synthesis helpers."""

from app.ai.final_report import (
    FinalReportDraft,
    _is_placeholder_summary,
    parse_final_report_draft_text,
)
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import calculate_report_scores


def test_final_report_schema_parses_markdown_json_output() -> None:
    payload = parse_final_report_draft_text(
        "```json\n"
        "{\n"
        '  "executive_summary": "Done",\n'
        '  "security_score": 7,\n'
        '  "maintainability_score": 6,\n'
        '  "performance_score": 8,\n'
        '  "overall_score": 7\n'
        "}\n"
        "```"
    )

    assert payload.executive_summary == "Done"
    assert payload.security_score == 7
    assert payload.maintainability_score == 6
    assert payload.performance_score == 8
    assert payload.overall_score == 7


def test_final_report_schema_parses_model_wrapper_text() -> None:
    payload = parse_final_report_draft_text(
        "<|python|>\n"
        "{\n"
        '  "executive_summary": "AI review found critical auth issues.",\n'
        '  "security_score": 2,\n'
        '  "maintainability_score": 6,\n'
        '  "performance_score": 5,\n'
        '  "overall_score": 4,\n'
        '  "top_priorities": ["Fix plaintext password logging"],\n'
        '  "tech_stack": {"language": "Python"}\n'
        "}\n"
        "<|python|>\n\n"
        "Final Answer: Final report generated successfully."
    )

    assert payload.executive_summary == "AI review found critical auth issues."
    assert payload.overall_score == 4
    assert payload.top_priorities == ["Fix plaintext password logging"]


def test_final_report_schema_accepts_structured_top_priorities() -> None:
    payload = FinalReportDraft.model_validate(
        {
            "executive_summary": "Done",
            "security_score": 7,
            "maintainability_score": 6,
            "performance_score": 8,
            "overall_score": 7,
            "top_priorities": [
                {
                    "severity": "critical",
                    "category": "requirement",
                    "source": "KB",
                    "title": "Missing FastAPI implementation",
                    "file_path": "requirements.txt",
                    "line_start": 1,
                },
                {
                    "severity": "high",
                    "category": "security",
                    "title": "Missing authorization",
                },
                "src/app.py",
            ],
        }
    )

    assert payload.top_priorities == [
        "requirements.txt",
        "high | security | Missing authorization",
        "src/app.py",
    ]


def test_final_report_detects_placeholder_summary() -> None:
    assert _is_placeholder_summary("(as above)\n### Final Answer")
    assert _is_placeholder_summary("(the JSON input above)")
    assert _is_placeholder_summary("The final report has been successfully generated.")
    assert not _is_placeholder_summary("AI review found two high security issues.")


def test_final_report_scores_from_persisted_issues() -> None:
    scores = calculate_report_scores(
        [
            _normalized_issue(
                severity=IssueSeverity.HIGH,
                category=IssueCategory.SECURITY,
            ),
            _normalized_issue(
                severity=IssueSeverity.MEDIUM,
                category=IssueCategory.PERFORMANCE,
            ),
            _normalized_issue(
                severity=IssueSeverity.LOW,
                category=IssueCategory.MAINTAINABILITY,
            ),
        ]
    )

    assert scores["security_score"] == 8.2
    assert scores["performance_score"] == 9.0
    assert scores["maintainability_score"] == 9.7
    assert scores["overall_score"] == 7.2


def _normalized_issue(
    *,
    severity: IssueSeverity,
    category: IssueCategory,
) -> NormalizedIssue:
    return NormalizedIssue(
        file_path="app.py",
        line_start=1,
        line_end=1,
        severity=severity,
        category=category,
        title="Issue",
        description="Description",
        source=IssueSource.AI_REVIEW,
        confidence=0.8,
    )
