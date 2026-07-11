"""Contract tests for KB-derived issues."""

from app.models.review_issue import IssueSource, ReviewIssue
from app.models.review_report import ReviewReport
from app.schemas.report import ReportResponse


def test_kb_issue_source_and_required_location() -> None:
    assert IssueSource.KB.value == "KB"
    assert not hasattr(IssueSource, "ROADMAP_RULE")
    assert ReviewIssue.__table__.c.file_path.nullable is False
    assert ReviewIssue.__table__.c.line_start.nullable is False
    assert ReviewIssue.__table__.c.line_end.nullable is False


def test_report_contract_has_no_roadmap_scores() -> None:
    assert "compliance_score" not in ReviewReport.__table__.c
    assert "bonus_score" not in ReviewReport.__table__.c
    assert "compliance_score" not in ReportResponse.model_fields
    assert "bonus_score" not in ReportResponse.model_fields
