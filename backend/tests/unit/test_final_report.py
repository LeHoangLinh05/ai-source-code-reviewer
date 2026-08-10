"""Tests for final-report synthesis helpers."""

import pytest

from app.ai.reporting import draft as report_draft
from app.ai.reporting.final_report import (
    FinalReportDraft,
    _is_placeholder_summary,
    parse_final_report_draft_text,
)


def test_final_report_schema_ignores_legacy_scores_in_markdown_json() -> None:
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
    assert "overall_score" not in payload.model_dump()


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


@pytest.mark.asyncio
async def test_final_report_uses_one_raw_json_call_for_cline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def invoke_raw_json(report_input: str) -> FinalReportDraft:
        calls.append(report_input)
        return FinalReportDraft(
            executive_summary="Done",
        )

    monkeypatch.setattr(
        report_draft,
        "_invoke_raw_json_final_report",
        invoke_raw_json,
    )

    result, source = await report_draft.build_final_report_draft(
        report_input="report input",
        report_context="{}",
    )

    assert result.executive_summary == "Done"
    assert source == "raw_json_output"
    assert calls == ["report input"]


def test_final_report_detects_placeholder_summary() -> None:
    assert _is_placeholder_summary("(as above)\n### Final Answer")
    assert _is_placeholder_summary("(the JSON input above)")
    assert _is_placeholder_summary("The final report has been successfully generated.")
    assert not _is_placeholder_summary("AI review found two high security issues.")
