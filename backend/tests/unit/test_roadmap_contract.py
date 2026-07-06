"""Smoke tests for roadmap compliance PostgreSQL contract fields."""

from app.models.review_issue import IssueCategory, IssueSource, ReviewIssue
from app.models.review_report import ReviewReport
from app.schemas.report import ReportResponse


def test_roadmap_issue_enums_and_nullable_file_path() -> None:
    assert IssueCategory.REQUIREMENT.value == "requirement"
    assert IssueSource.ROADMAP_RULE.value == "roadmap_rule"
    assert ReviewIssue.__table__.c.file_path.nullable is True


def test_report_model_and_schema_include_compliance_scores() -> None:
    assert "compliance_score" in ReviewReport.__table__.c
    assert "bonus_score" in ReviewReport.__table__.c
    assert "compliance_score" in ReportResponse.model_fields
    assert "bonus_score" in ReportResponse.model_fields
