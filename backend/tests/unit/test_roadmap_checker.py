"""Regression tests for deterministic roadmap compliance rules."""

from pathlib import Path

from app.ai.rules.roadmap_checker import RoadmapComplianceChecker, RuleStatus
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource


def test_rule_profile_null_does_not_run(tmp_path: Path) -> None:
    checker = RoadmapComplianceChecker()

    output = checker.run(tmp_path, None)

    assert output is None


def test_weeks_included_filters_week_1_week_2_and_gen(tmp_path: Path) -> None:
    checker = RoadmapComplianceChecker()

    output = checker.run(
        tmp_path,
        {"id": "roadmap_bootcamp_v1", "weeks_included": [1, 2]},
    )

    assert output is not None
    assert len(output.results) == 34
    assert {result.week for result in output.results} <= {1, 2, "GEN"}
    assert all(result.rule_id != "RC-W5-01" for result in output.results)
    assert all(not result.rule_id.startswith("RC-W7") for result in output.results)


def test_needs_ai_verification_pass_becomes_provisional(
    tmp_path: Path,
) -> None:
    write_text(
        tmp_path / "requirements.txt", "fastapi\npydantic\nuvicorn\npython-jose\n"
    )
    write_text(tmp_path / "routers" / "__init__.py", "")
    write_text(tmp_path / "schemas" / "__init__.py", "")
    write_text(tmp_path / "models" / "__init__.py", "")
    write_text(
        tmp_path / "app.py",
        '@router.post("/login")\ndef login():\n    return {"refresh_token": "token"}\n',
    )
    checker = RoadmapComplianceChecker()

    output = checker.run(
        tmp_path,
        {"id": "roadmap_bootcamp_v1", "weeks_included": [1]},
    )

    assert output is not None
    login_result = next(
        result for result in output.results if result.rule_id == "RC-W1-10"
    )
    assert login_result.status == RuleStatus.PROVISIONAL_PASS
    assert any(
        item.rule_id == "RC-W1-10" and item.file_path == "app.py"
        for item in output.verification_queue
    )
    assert all(
        issue.raw_output is None or issue.raw_output.get("rule_id") != "RC-W1-10"
        for issue in output.issues
    )


def test_missing_websocket_creates_one_requirement_issue(tmp_path: Path) -> None:
    checker = RoadmapComplianceChecker()

    output = checker.run(
        tmp_path,
        {"id": "roadmap_bootcamp_v1", "weeks_included": [5]},
    )

    assert output is not None
    issues = [
        issue
        for issue in output.issues
        if issue.raw_output and issue.raw_output.get("rule_id") == "RC-W5-01"
    ]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.category == IssueCategory.REQUIREMENT
    assert issue.source == IssueSource.ROADMAP_RULE
    assert issue.severity == IssueSeverity.CRITICAL
    assert issue.file_path is None


def test_rag_retrieval_rule_scans_rag_package_paths(tmp_path: Path) -> None:
    write_text(tmp_path / "requirements.txt", "chromadb\n")
    write_text(
        tmp_path / "app" / "rag" / "pipeline.py",
        "def answer(question: str) -> str:\n"
        "    retrieved_chunks = retriever.retrieve(question)\n"
        "    return llm.invoke({'context': retrieved_chunks, 'question': question})\n",
    )
    checker = RoadmapComplianceChecker()

    output = checker.run(
        tmp_path,
        {"id": "roadmap_bootcamp_v1", "weeks_included": [7]},
    )

    assert output is not None
    result = next(result for result in output.results if result.rule_id == "RC-W7-04")
    assert result.status == RuleStatus.PROVISIONAL_PASS
    assert any(
        item.rule_id == "RC-W7-04" and item.file_path == "app/rag/pipeline.py"
        for item in output.verification_queue
    )


def test_roadmap_rules_yaml_loads_expected_contract() -> None:
    checker = RoadmapComplianceChecker()

    rules = checker.load_rules()

    assert len(rules) == 79
    priorities = [rule.priority for rule in rules]
    assert priorities.count("P0") == 40
    assert priorities.count("P1") == 26
    assert priorities.count("P2") == 13
    assert sum(1 for rule in rules if rule.needs_ai_verification) == 16


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
