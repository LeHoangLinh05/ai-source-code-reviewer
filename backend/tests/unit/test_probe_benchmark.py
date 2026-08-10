"""Tests for the full-audit security benchmark manifest and metrics."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from scripts.benchmark_probe_review import (
    ExpectedIssue,
    Finding,
    _benchmark_configuration,
    _finding_matches_issue,
    _judge_candidates,
    _judge_contract_metrics,
    _load_benchmark_inputs,
)

ROOT_DIR = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    ROOT_DIR
    / "backend"
    / "benchmarks"
    / "probe_review"
    / "mvp_inventory_vulnerable.json"
)
MVP_BLOG_MANIFEST_PATH = (
    ROOT_DIR / "backend" / "benchmarks" / "probe_review" / "mvp_blog_vulnerable.json"
)


def test_mvp_inventory_manifest_requires_full_audit_without_roadmap() -> None:
    manifest = _load_benchmark_inputs(
        manifest_path=MANIFEST_PATH,
        ground_truth_path=None,
        false_positives_path=None,
        probe_map_path=None,
    )

    assert manifest.require_full_recall
    assert manifest.required_options == {
        "review_mode": "full_audit",
        "rule_profile": None,
    }
    assert len(manifest.expected_issues) == 7
    assert manifest.expected_issues[0].severity == "critical"


def test_mvp_blog_manifest_covers_six_intentional_issues() -> None:
    manifest = _load_benchmark_inputs(
        manifest_path=MVP_BLOG_MANIFEST_PATH,
        ground_truth_path=None,
        false_positives_path=None,
        probe_map_path=None,
    )

    assert manifest.require_full_recall
    assert manifest.commit_sha == "51ca3a28fe6565ef8219bad3eeda59253d4ab549"
    assert len(manifest.expected_issues) == 6
    assert {
        probe_id
        for issue in manifest.expected_issues
        for probe_id in issue.expected_probes
    } >= {
        "security.sensitive_data_logging",
        "security.unrestricted_file_upload",
    }


def test_benchmark_configuration_rejects_roadmap_profile() -> None:
    manifest = _load_benchmark_inputs(
        manifest_path=MANIFEST_PATH,
        ground_truth_path=None,
        false_positives_path=None,
        probe_map_path=None,
    )
    job = SimpleNamespace(
        commit_sha=manifest.commit_sha,
        options={
            "review_mode": "full_audit",
            "rule_profile": {"id": "roadmap_bootcamp_v1"},
        },
    )

    configuration = _benchmark_configuration(job, manifest)
    option_mismatches = cast(
        dict[str, object],
        configuration["option_mismatches"],
    )

    assert configuration["matches"] is False
    assert "rule_profile" in option_mismatches


def test_judge_contract_metrics_detect_missing_verdicts() -> None:
    metrics = _judge_contract_metrics(
        [
            {
                "input": {"probe_ids": ["security.one", "security.two"]},
                "output": {
                    "status": "ok",
                    "candidates": [
                        {
                            "probe_id": "security.one",
                            "verdict": "no_issue",
                        }
                    ],
                },
            }
        ]
    )

    assert metrics["is_complete"] is False
    assert metrics["missing_verdict_count"] == 1


def test_benchmark_flattens_new_multi_issue_judge_results() -> None:
    candidates = _judge_candidates(
        {
            "output": {
                "results": [
                    {
                        "probe_id": "coverage.files",
                        "verdict": "issue",
                        "issues": [
                            {"claim_type": "path_traversal"},
                            {"claim_type": "unrestricted_file_upload"},
                        ],
                    },
                    {
                        "probe_id": "security.safe",
                        "verdict": "no_issue",
                        "issues": [],
                    },
                ]
            }
        }
    )

    assert [candidate["claim_type"] for candidate in candidates] == [
        "path_traversal",
        "unrestricted_file_upload",
    ]
    assert {candidate["probe_id"] for candidate in candidates} == {"coverage.files"}


def test_judge_contract_metrics_read_new_results_shape() -> None:
    metrics = _judge_contract_metrics(
        [
            {
                "input": {"probe_ids": ["security.one", "security.two"]},
                "output": {
                    "status": "ok",
                    "results": [
                        {
                            "probe_id": "security.one",
                            "verdict": "no_issue",
                            "issues": [],
                        },
                        {
                            "probe_id": "security.two",
                            "verdict": "issue",
                            "issues": [{"claim_type": "example"}],
                        },
                    ],
                },
            }
        ]
    )

    assert metrics["is_complete"] is True
    assert metrics["missing_verdict_count"] == 0


def test_finding_matches_cross_file_supporting_evidence_for_expected_probe() -> None:
    issue = ExpectedIssue(
        issue_id="MVP-SEC-002",
        file_path="backend/app/schemas/item.py",
        line_start=8,
        line_end=13,
        category="security",
        severity="high",
        expected_probes=("security.mass_assignment",),
    )
    finding = Finding(
        finding_id="finding-1",
        file_path="backend/app/repositories/item_repository.py",
        line_start=25,
        line_end=29,
        category="security",
        severity="high",
        title="Sensitive fields can be assigned dynamically",
        source="ai_review",
        probe_id="security.mass_assignment",
        supporting_evidence=(
            {
                "file_path": "backend/app/schemas/item.py",
                "line_start": 8,
                "line_end": 13,
            },
        ),
    )

    assert _finding_matches_issue(finding, issue)


def test_finding_rejects_evidence_from_wrong_probe() -> None:
    issue = ExpectedIssue(
        issue_id="MVP-SEC-002",
        file_path="backend/app/schemas/item.py",
        line_start=8,
        line_end=13,
        category="security",
        severity="high",
        expected_probes=("security.mass_assignment",),
    )
    finding = Finding(
        finding_id="finding-1",
        file_path=issue.file_path,
        line_start=issue.line_start,
        line_end=issue.line_end,
        category="security",
        severity="high",
        title="Unrelated finding",
        source="ai_review",
        probe_id="security.sensitive_response_exposure",
        supporting_evidence=(),
    )

    assert not _finding_matches_issue(finding, issue)
