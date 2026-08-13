"""Tests for report issue response enrichment."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.services.reporting.aggregation import build_report_aggregate
from app.services.reporting.issue_presenter import (
    _group_issues,
    _group_matches_filters,
    _issue_group_key,
    _issue_group_response,
    _source_context_from_chunk,
)
from app.services.reporting.service import ReportService


@pytest.mark.asyncio
async def test_refresh_report_aggregates_clears_deprecated_scores() -> None:
    job_id = uuid4()
    report = SimpleNamespace(
        job_id=job_id,
        total_issues=0,
        critical_count=0,
        high_count=0,
        medium_count=0,
        low_count=0,
        info_count=0,
        security_score=0.0,
        maintainability_score=0.0,
        performance_score=0.0,
        overall_score=0.0,
        top_risky_files=[],
        executive_summary=None,
        total_files_analyzed=4,
    )
    service = ReportService(
        report_repository=_IssueRepository(
            [
                _issue(job_id, IssueSeverity.CRITICAL, IssueCategory.SECURITY),
                _issue(job_id, IssueSeverity.HIGH, IssueCategory.BUG),
                _issue(job_id, IssueSeverity.LOW, IssueCategory.REQUIREMENT),
            ]
        ),  # type: ignore[arg-type]
        chunk_metadata_repository=object(),  # type: ignore[arg-type]
    )

    await service._refresh_report_aggregates(report)  # type: ignore[arg-type]

    assert report.total_issues == 3
    assert report.critical_count == 1
    assert report.high_count == 1
    assert report.low_count == 1
    assert report.security_score is None
    assert report.maintainability_score is None
    assert report.performance_score is None
    assert report.overall_score is None
    assert report.executive_summary is not None


def test_canonical_aggregate_merges_synonyms_and_preserves_provenance() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/services/importer.py",
                line_start=12,
                line_end=16,
                title="Unsafe deserialization via pickle",
                raw_output={
                    "probe_review": {
                        "probe_id": "coverage.app_services_importer_py",
                        "claim_type": "unsafe_deserialization_via_pickle",
                    }
                },
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app\\services\\importer.py",
                line_start=13,
                line_end=16,
                title="Insecure deserialization",
                source=IssueSource.BANDIT,
                raw_output={
                    "code": "12 config = pickle.loads(raw_bytes)",
                    "test_id": "B301",
                },
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 1
    assert aggregate.total_occurrences == 1
    assert aggregate.raw_issue_count == 2
    occurrence = aggregate.occurrences[0]
    assert {issue.source for issue in occurrence.issues} == {
        IssueSource.AI_REVIEW,
        IssueSource.BANDIT,
    }


def test_readme_findings_are_excluded_from_reports_and_issue_groups() -> None:
    job_id = uuid4()
    source_issue = cast(
        ReviewIssue,
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.SECURITY,
            file_path="app/auth.py",
        ),
    )
    readme_issue = cast(
        ReviewIssue,
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.REQUIREMENT,
            file_path="README.md",
            title="Missing refresh endpoint",
        ),
    )

    issues = [source_issue, readme_issue]
    aggregate = build_report_aggregate(issues)
    groups = _group_issues(issues)

    assert aggregate.raw_issue_count == 1
    assert aggregate.total_findings == 1
    assert list(groups) == [_issue_group_key(source_issue)]


def test_generic_ai_claim_uses_specific_legacy_title_taxonomy() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/otp.py",
                line_start=20,
                line_end=24,
                title="OTP Leakage in API Response",
                raw_output={"finding_key": "security:sensitive_data_exposure"},
            ),
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/otp.py",
                line_start=21,
                line_end=24,
                title="OTP Exposed in API Response",
                raw_output={"finding_key": "security:otp_exposure"},
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 1
    assert aggregate.total_occurrences == 1
    assert aggregate.raw_issue_count == 2


def test_full_audit_otp_randomness_merges_with_directed_probe() -> None:
    job_id = uuid4()
    source_lines = [
        "async def send_otp(phone: str):",
        "    code = random.randint(100000, 999999)",
        "    return {'debug_otp': code}",
    ]
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="backend/app/services/review_jobs/notifications.py",
                line_start=29,
                line_end=32,
                title="OTP generated with non-cryptographic random",
                raw_output={
                    "claim_type": "otp_weak_randomness",
                    "finding_key": "security:otp_weak_randomness",
                    "probe_review": {
                        "probe_id": "security.otp_exposure_rate_limit",
                        "source_context": {"lines": source_lines},
                    },
                },
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="backend/app/services/review_jobs/notifications.py",
                line_start=29,
                line_end=32,
                title="Insecure OTP generation using pseudo-random numbers",
                raw_output={
                    "claim_type": "weak_cryptographic_practice",
                    "finding_key": "security:weak_cryptographic_practice",
                    "probe_review": {
                        "probe_id": "coverage.full_audit.notification_service.0",
                        "source_context": {"lines": source_lines},
                    },
                },
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 1
    assert aggregate.total_occurrences == 1
    assert aggregate.raw_issue_count == 2
    assert aggregate.findings[0].finding_key == "security:otp_weak_randomness"
    assert len(aggregate.occurrences[0].issues) == 2


def test_non_otp_weak_cryptography_is_not_merged_with_otp_randomness() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/crypto.py",
                line_start=10,
                line_end=12,
                title="OTP generated with non-cryptographic random",
                raw_output={"claim_type": "otp_weak_randomness"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/crypto.py",
                line_start=10,
                line_end=12,
                title="Weak cryptographic practice",
                description="A certificate fingerprint uses hashlib.sha1.",
                raw_output={"claim_type": "weak_cryptographic_practice"},
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 2
    assert aggregate.total_occurrences == 2


def test_canonical_aggregate_keeps_distinct_claims_on_same_lines() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/otp.py",
                line_start=10,
                line_end=12,
                raw_output={"claim_type": "otp_exposure"},
            ),
            _issue(
                job_id,
                IssueSeverity.MEDIUM,
                IssueCategory.SECURITY,
                file_path="app/otp.py",
                line_start=10,
                line_end=12,
                raw_output={"claim_type": "otp_weak_randomness"},
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 2
    assert aggregate.total_occurrences == 2
    assert sum(aggregate.severity_counts.values()) == aggregate.total_findings


def test_same_finding_in_multiple_files_has_multiple_occurrences() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/a.py",
                raw_output={"claim_type": "hardcoded_secret"},
            ),
            _issue(
                job_id,
                IssueSeverity.MEDIUM,
                IssueCategory.SECURITY,
                file_path="app/b.py",
                raw_output={"claim_type": "hardcoded_secret"},
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 1
    assert aggregate.total_occurrences == 2
    assert aggregate.severity_counts[IssueSeverity.HIGH] == 1


def test_real_detector_aliases_merge_without_merging_distinct_defects() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/auth.py",
                line_start=9,
                line_end=10,
                title="Use of MD5 for Password Hashing",
                raw_output={"claim_type": "weak_cryptography"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/auth.py",
                line_start=9,
                line_end=10,
                title="Weak Password Hashing Algorithm",
                raw_output={"claim_type": "weak_password_hash"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="./app/auth.py",
                line_start=10,
                line_end=10,
                title="hashlib",
                description="Use of weak MD5 hash for security.",
                source=IssueSource.BANDIT,
                raw_output={
                    "test_id": "B324",
                    "source_context": {
                        "lines": [
                            "def hash_password(password: str) -> str:",
                            "    return hashlib.md5(password.encode()).hexdigest()",
                        ]
                    },
                },
            ),
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/utils.py",
                line_start=6,
                line_end=9,
                title="OS Command Injection via shell=True",
                raw_output={"claim_type": "os_command_injection"},
            ),
            _issue(
                job_id,
                IssueSeverity.CRITICAL,
                IssueCategory.SECURITY,
                file_path="app/utils.py",
                line_start=6,
                line_end=9,
                title="Command Injection via Shell Execution",
                raw_output={"claim_type": "command_injection"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="./app/utils.py",
                line_start=8,
                line_end=8,
                title="subprocess_popen_with_shell_equals_true",
                source=IssueSource.BANDIT,
                raw_output={"test_id": "B602"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.PERFORMANCE,
                file_path="app/tasks.py",
                line_start=25,
                line_end=31,
                title="N+1 Query in user_task_report",
                raw_output={"claim_type": "performance_n_plus_one"},
            ),
            _issue(
                job_id,
                IssueSeverity.MEDIUM,
                IssueCategory.PERFORMANCE,
                file_path="app/tasks.py",
                line_start=25,
                line_end=31,
                title="N+1 Query in User Task Report",
                raw_output={"claim_type": "n_plus_one_query"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/utils.py",
                line_start=21,
                line_end=21,
                title="Hardcoded JWT Secret",
                raw_output={"claim_type": "hardcoded_secret"},
            ),
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.SECURITY,
                file_path="app/utils.py",
                line_start=21,
                line_end=21,
                title="Hardcoded JWT Secret",
                raw_output={
                    "claim_type": "insecure_default_credentials",
                    "probe_review": {
                        "probe_id": "security.insecure_default_credentials"
                    },
                },
            ),
            _issue(
                job_id,
                IssueSeverity.LOW,
                IssueCategory.SECURITY,
                file_path="./app/utils.py",
                line_start=21,
                line_end=21,
                title="hardcoded_password_string",
                source=IssueSource.BANDIT,
                raw_output={"test_id": "B105"},
            ),
            _issue(
                job_id,
                IssueSeverity.MEDIUM,
                IssueCategory.BUG,
                file_path="app/utils.py",
                line_start=12,
                line_end=18,
                title="Bare except clause swallowing exceptions",
                raw_output={"claim_type": "bare_except"},
            ),
            _issue(
                job_id,
                IssueSeverity.LOW,
                IssueCategory.STYLE,
                file_path="app/utils.py",
                line_start=16,
                line_end=17,
                title="Ruff S110: try-except-pass",
                source=IssueSource.RUFF,
                raw_output={"code": "S110"},
            ),
            _issue(
                job_id,
                IssueSeverity.LOW,
                IssueCategory.SECURITY,
                file_path="./app/utils.py",
                line_start=16,
                line_end=16,
                title="try_except_pass",
                source=IssueSource.BANDIT,
                raw_output={"test_id": "B110"},
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.raw_issue_count == 14
    assert aggregate.total_findings == 5
    assert aggregate.total_occurrences == 5
    assert {finding.finding_key for finding in aggregate.findings} == {
        "bug:bare_except",
        "performance:n_plus_one_query",
        "security:command_injection",
        "security:hardcoded_secret",
        "security:weak_password_hash",
    }


def test_roadmap_rules_do_not_share_a_generic_missing_requirement_key() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.REQUIREMENT,
                file_path="app/utils.py",
                line_start=6,
                line_end=9,
                title="Missing docker-compose.yml",
                source=IssueSource.KB,
                raw_output={
                    "claim_type": "missing_requirement",
                    "finding_key": "requirement:missing_requirement",
                    "probe_review": {"rule_id": "RC-W2-05"},
                },
            ),
            _issue(
                job_id,
                IssueSeverity.LOW,
                IssueCategory.REQUIREMENT,
                file_path="app/utils.py",
                line_start=6,
                line_end=9,
                title="Missing demo deployment link in README",
                source=IssueSource.KB,
                raw_output={
                    "claim_type": "missing_requirement",
                    "finding_key": "requirement:missing_requirement",
                    "probe_review": {"rule_id": "RC-W2-12"},
                },
            ),
        ],
    )

    aggregate = build_report_aggregate(issues)

    assert aggregate.total_findings == 2
    assert {finding.finding_key for finding in aggregate.findings} == {
        "roadmap:rc_w2_05",
        "roadmap:rc_w2_12",
    }


def test_ai_review_source_filter_includes_roadmap_ai_findings() -> None:
    job_id = uuid4()
    issues = cast(
        list[ReviewIssue],
        [
            _issue(
                job_id,
                IssueSeverity.HIGH,
                IssueCategory.REQUIREMENT,
                source=IssueSource.KB,
            ),
        ],
    )

    assert _group_matches_filters(
        issues,
        severity=None,
        category=None,
        source=IssueSource.AI_REVIEW,
        file_path=None,
    )


def test_source_context_falls_back_to_persisted_chunk() -> None:
    source_context = _source_context_from_chunk(
        {
            "line_start": 10,
            "line_end": 14,
            "chunk_text": "ten\neleven\nproblem\nthirteen\nfourteen\n",
        },
        line_start=12,
        line_end=12,
        context_radius=1,
    )

    assert source_context == {
        "start_line": 11,
        "lines": ["eleven", "problem", "thirteen"],
    }


def test_issue_group_key_prefers_static_rule_id() -> None:
    job_id = uuid4()
    first_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Hardcoded secret in settings",
        raw_output={"test_id": "B105"},
    )
    second_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Hardcoded password literal",
        raw_output={"test_id": "B105"},
    )

    assert _issue_group_key(cast(ReviewIssue, first_issue)) == _issue_group_key(
        cast(ReviewIssue, second_issue)
    )


def test_issue_group_key_prefers_semantic_finding_key_for_ai_issues() -> None:
    job_id = uuid4()
    first_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Password is written to logs",
        raw_output={"finding_key": "security:sensitive_data_in_logs"},
    )
    second_issue = _issue(
        job_id,
        IssueSeverity.MEDIUM,
        IssueCategory.SECURITY,
        title="Login diagnostics expose a credential",
        raw_output={"finding_key": "security:sensitive_data_logging"},
    )

    assert _issue_group_key(cast(ReviewIssue, first_issue)) == _issue_group_key(
        cast(ReviewIssue, second_issue)
    )


def test_issue_group_response_counts_occurrences_and_files() -> None:
    job_id = uuid4()
    issues = [
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.SECURITY,
            file_path="app/auth.py",
            description="Hardcoded password literal in auth flow",
            suggestion="Move the password to a secret manager.",
            raw_output={"test_id": "B105"},
        ),
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.SECURITY,
            file_path="app/settings.py",
            description="Hardcoded secret-like value in settings",
            suggestion="Read the setting from an environment variable.",
            raw_output={"test_id": "B105"},
        ),
    ]
    groups = _group_issues(cast(list[ReviewIssue], issues))
    group_key, grouped_issues = next(iter(groups.items()))

    response = _issue_group_response(
        group_key,
        grouped_issues,
        include_occurrences=True,
    )

    assert response.occurrence_count == 2
    assert response.affected_files == ["app/auth.py", "app/settings.py"]
    assert [occurrence.file_path for occurrence in response.occurrences] == [
        "app/auth.py",
        "app/settings.py",
    ]
    assert [occurrence.description for occurrence in response.occurrences] == [
        "Hardcoded password literal in auth flow",
        "Hardcoded secret-like value in settings",
    ]
    assert [occurrence.suggestion for occurrence in response.occurrences] == [
        "Move the password to a secret manager.",
        "Read the setting from an environment variable.",
    ]


class _IssueRepository:
    def __init__(self, issues: list[SimpleNamespace]) -> None:
        self.issues = issues

    async def list_all_issues(self, job_id: object) -> list[SimpleNamespace]:
        return [issue for issue in self.issues if issue.job_id == job_id]

    async def save_report(self, report: object) -> object:
        return report


def _issue(
    job_id: object,
    severity: IssueSeverity,
    category: IssueCategory,
    *,
    file_path: str | None = None,
    raw_output: dict[str, object] | None = None,
    title: str = "Finding",
    description: str = "Finding description",
    suggestion: str | None = None,
    source: IssueSource = IssueSource.AI_REVIEW,
    line_start: int = 1,
    line_end: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        job_id=job_id,
        file_path=file_path or f"{category.value}.py",
        line_start=line_start,
        line_end=line_end,
        severity=severity,
        category=category,
        title=title,
        description=description,
        suggestion=suggestion,
        source=source,
        confidence=0.9,
        raw_output=raw_output,
        created_at=datetime.now(UTC),
    )
