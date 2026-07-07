"""Deterministic roadmap compliance rule engine."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatch
import json
from pathlib import Path
import re
import tomllib
from typing import Any
from uuid import UUID

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rules.git_utils import list_tracked_files
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.repositories.mongodb_repository import RoadmapComplianceResultRepository
from app.schemas.mongodb import (
    RoadmapComplianceResultDocument,
    RoadmapRuleResult,
    RoadmapVerificationQueueItem,
)
from app.schemas.normalized_issue import NormalizedIssue

RULE_PROFILE_ID = "roadmap_bootcamp_v1"
RULES_PATH = Path(__file__).with_name("roadmap_rules_v2.yaml")
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
    ".venv",
}
ROADMAP_SOURCE_PRIORITY = 10_000


class RuleStatus(StrEnum):
    """Statuses produced for each applied roadmap rule."""

    PASS = "pass"
    FAIL = "fail"
    PROVISIONAL_PASS = "provisional_pass"


class CheckType(StrEnum):
    """Supported deterministic check types from AI_flow.md section 3.2."""

    REQUIRED_FILE = "required_file"
    REQUIRED_ANY_OF = "required_any_of"
    REQUIRED_FOLDER = "required_folder"
    FORBIDDEN_TRACKED_FILE = "forbidden_tracked_file"
    REQUIRED_DEPENDENCY = "required_dependency"
    REQUIRED_CODE_PATTERN = "required_code_pattern"
    MIN_FILE_COUNT = "min_file_count"
    REQUIRED_CONFIG_KEY = "required_config_key"


class RoadmapRule(BaseModel):
    """One validated roadmap rule loaded from roadmap_rules_v2.yaml."""

    model_config = ConfigDict(extra="allow")

    rule_id: str
    week: int | str
    skill_group: str
    requirement: str
    check_type: CheckType
    target: dict[str, Any]
    priority: str
    severity_if_missing: IssueSeverity
    rationale_if_missing: str
    needs_ai_verification: bool
    ai_hint: str | None = None

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: str) -> str:
        """Accept only roadmap priority labels used by the scoring contract."""

        if value not in {"P0", "P1", "P2"}:
            raise ValueError("priority must be one of P0, P1, P2")
        return value

    @field_validator("week")
    @classmethod
    def validate_week(cls, value: int | str) -> int | str:
        """Accept numeric training weeks or the global GEN bucket."""

        if value == "GEN" or isinstance(value, int):
            return value
        raise ValueError("week must be an integer or GEN")


class RuleSet(BaseModel):
    """Validated root object for roadmap_rules_v2.yaml."""

    rules: list[RoadmapRule]


class RuleEvaluation(BaseModel):
    """Internal result of one deterministic check."""

    passed: bool
    matched_file: str | None = None


class RuleResult(BaseModel):
    """Result contract stored in MongoDB and returned to callers."""

    rule_id: str
    status: RuleStatus
    severity: IssueSeverity | None = None
    week: int | str
    skill_group: str
    priority: str
    requirement: str


class VerificationItem(BaseModel):
    """AI verification queue item ready for Tool 1 / Agent merging."""

    rule_id: str
    file_path: str
    ai_hint: str


class RoadmapCheckOutput(BaseModel):
    """Roadmap checker output for persistence and later Agent context injection."""

    rule_profile: dict[str, object]
    results: list[RuleResult]
    verification_queue: list[VerificationItem]
    compliance_score: float | None
    bonus_score: float | None
    issues: list[NormalizedIssue]


class RoadmapComplianceChecker:
    """Run deterministic roadmap rules without using an LLM."""

    def __init__(self, rules_path: Path = RULES_PATH) -> None:
        self.rules_path = rules_path

    def run(
        self,
        sandbox_path: str | Path,
        rule_profile: dict[str, object] | None,
    ) -> RoadmapCheckOutput | None:
        """Evaluate rules for an enabled roadmap profile.

        A null rule_profile is the default contract and means this engine does not run.
        """

        if rule_profile is None:
            return None

        normalized_profile = self._normalize_rule_profile(rule_profile)
        weeks_included = self._get_weeks_included(normalized_profile)
        rules = self.load_rules(weeks_included=weeks_included)
        repo_path = Path(sandbox_path)
        results: list[RuleResult] = []
        verification_queue: list[VerificationItem] = []
        issues: list[NormalizedIssue] = []

        for rule in rules:
            evaluation = self._evaluate(rule, repo_path)
            if not evaluation.passed:
                result = self._build_result(rule, RuleStatus.FAIL)
                issues.append(self._build_issue(rule))
            elif rule.needs_ai_verification:
                result = self._build_result(rule, RuleStatus.PROVISIONAL_PASS)
                verification_queue.append(
                    VerificationItem(
                        rule_id=rule.rule_id,
                        file_path=evaluation.matched_file or "",
                        ai_hint=rule.ai_hint or "",
                    )
                )
            else:
                result = self._build_result(rule, RuleStatus.PASS)

            results.append(result)

        compliance_score = self._calculate_score(
            results,
            included_priorities={"P0", "P1"},
        )
        bonus_score = self._calculate_score(results, included_priorities={"P2"})
        return RoadmapCheckOutput(
            rule_profile=normalized_profile,
            results=results,
            verification_queue=verification_queue,
            compliance_score=compliance_score,
            bonus_score=bonus_score,
            issues=issues,
        )

    async def run_and_persist(
        self,
        *,
        job_id: UUID,
        sandbox_path: str | Path,
        rule_profile: dict[str, object] | None,
        postgres_session: AsyncSession,
        roadmap_repository: RoadmapComplianceResultRepository,
    ) -> RoadmapCheckOutput | None:
        """Run enabled rules and write MongoDB results plus requirement issues."""

        output = self.run(sandbox_path, rule_profile)
        if output is None:
            return None

        await roadmap_repository.insert_one(
            RoadmapComplianceResultDocument(
                job_id=job_id,
                rule_profile=output.rule_profile,
                checked_at=datetime.now(UTC),
                results=[
                    RoadmapRuleResult(
                        rule_id=result.rule_id,
                        status=result.status.value,
                        severity=result.severity.value if result.severity else None,
                        week=result.week,
                        skill_group=result.skill_group,
                    )
                    for result in output.results
                ],
                verification_queue=[
                    RoadmapVerificationQueueItem(
                        rule_id=item.rule_id,
                        file_path=item.file_path,
                        ai_hint=item.ai_hint,
                    )
                    for item in output.verification_queue
                ],
                compliance_score=output.compliance_score,
                bonus_score=output.bonus_score,
            )
        )
        postgres_session.add_all(
            [
                ReviewIssue(
                    job_id=job_id,
                    file_path=issue.file_path,
                    line_start=issue.line_start,
                    line_end=issue.line_end,
                    severity=issue.severity,
                    category=issue.category,
                    title=issue.title,
                    description=issue.description,
                    suggestion=issue.suggestion,
                    source=issue.source,
                    confidence=issue.confidence,
                    raw_output=issue.raw_output,
                )
                for issue in output.issues
            ]
        )
        await postgres_session.commit()
        return output

    def load_rules(
        self, *, weeks_included: set[int] | None = None
    ) -> list[RoadmapRule]:
        """Load and validate the official roadmap YAML rule set."""

        try:
            raw_rules = yaml.safe_load(self.rules_path.read_text(encoding="utf-8"))
            rule_set = RuleSet.model_validate(raw_rules)
        except ValidationError as error:
            raise ValueError(f"Invalid roadmap rules schema: {error}") from error
        except OSError as error:
            raise ValueError(
                f"Unable to read roadmap rules: {self.rules_path}"
            ) from error

        rules = rule_set.rules
        if weeks_included is None:
            return rules

        return [
            rule
            for rule in rules
            if rule.week == "GEN"
            or (isinstance(rule.week, int) and rule.week in weeks_included)
        ]

    def _normalize_rule_profile(
        self,
        rule_profile: dict[str, object],
    ) -> dict[str, object]:
        profile_id = rule_profile.get("id")
        if profile_id != RULE_PROFILE_ID:
            raise ValueError(f"Unsupported roadmap rule profile: {profile_id}")

        return dict(rule_profile)

    def _get_weeks_included(
        self,
        rule_profile: dict[str, object],
    ) -> set[int] | None:
        raw_weeks = rule_profile.get("weeks_included")
        if raw_weeks is None:
            return None
        if not isinstance(raw_weeks, list):
            raise ValueError("rule_profile.weeks_included must be a list of integers")

        weeks: set[int] = set()
        for raw_week in raw_weeks:
            if not isinstance(raw_week, int):
                raise ValueError("rule_profile.weeks_included must contain integers")
            weeks.add(raw_week)
        return weeks

    def _evaluate(self, rule: RoadmapRule, repo_path: Path) -> RuleEvaluation:
        checkers = {
            CheckType.REQUIRED_FILE: self._check_required_file,
            CheckType.REQUIRED_ANY_OF: self._check_required_any_of,
            CheckType.REQUIRED_FOLDER: self._check_required_folder,
            CheckType.FORBIDDEN_TRACKED_FILE: self._check_forbidden_tracked_file,
            CheckType.REQUIRED_DEPENDENCY: self._check_required_dependency,
            CheckType.REQUIRED_CODE_PATTERN: self._check_required_code_pattern,
            CheckType.MIN_FILE_COUNT: self._check_min_file_count,
            CheckType.REQUIRED_CONFIG_KEY: self._check_required_config_key,
        }
        return checkers[rule.check_type](repo_path, rule.target)

    def _check_required_file(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        return self._first_existing_path(
            repo_path, target.get("glob", []), want_dir=False
        )

    def _check_required_any_of(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        return self._first_existing_path(
            repo_path,
            target.get("glob_any_of", []),
            want_dir=None,
        )

    def _check_required_folder(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        return self._first_existing_path(
            repo_path, target.get("glob", []), want_dir=True
        )

    def _check_forbidden_tracked_file(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        tracked_files = list_tracked_files(repo_path)
        patterns = [str(pattern) for pattern in target.get("glob", [])]
        for tracked_file in tracked_files:
            if any(fnmatch(tracked_file, pattern) for pattern in patterns):
                return RuleEvaluation(passed=False, matched_file=tracked_file)

        return RuleEvaluation(passed=True)

    def _check_required_dependency(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        manifest_patterns = (
            target.get("manifest_glob") or target.get("glob_any_of") or []
        )
        packages = {
            str(package).lower() for package in target.get("package_any_of", [])
        }
        version_constraint = target.get("version_constraint")

        for manifest_path in self._iter_matching_paths(repo_path, manifest_patterns):
            dependencies = self._read_dependencies(manifest_path)
            if not packages and dependencies:
                return RuleEvaluation(
                    passed=True,
                    matched_file=self._relative_path(repo_path, manifest_path),
                )

            for package_name, package_version in dependencies.items():
                if package_name.lower() not in packages:
                    continue
                if not self._version_satisfies(package_version, version_constraint):
                    continue
                return RuleEvaluation(
                    passed=True,
                    matched_file=self._relative_path(repo_path, manifest_path),
                )

        return RuleEvaluation(passed=False)

    def _check_required_code_pattern(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        files = list(self._iter_matching_paths(repo_path, target.get("glob", [])))
        if regex_all_of := target.get("regex_all_of"):
            matched_file: str | None = None
            for pattern in regex_all_of:
                pattern_matched = False
                for file_path in files:
                    if self._file_contains_regex(file_path, str(pattern)):
                        pattern_matched = True
                        matched_file = matched_file or self._relative_path(
                            repo_path,
                            file_path,
                        )
                        break
                if not pattern_matched:
                    return RuleEvaluation(passed=False)
            return RuleEvaluation(passed=True, matched_file=matched_file)

        pattern = str(target.get("regex", ""))
        min_matches = int(target.get("min_matches", 1))
        match_count = 0
        first_matched_file: str | None = None
        for file_path in files:
            count = self._count_regex_matches(file_path, pattern)
            if count == 0:
                continue

            match_count += count
            first_matched_file = first_matched_file or self._relative_path(
                repo_path,
                file_path,
            )
            if match_count >= min_matches:
                return RuleEvaluation(passed=True, matched_file=first_matched_file)

        return RuleEvaluation(passed=False)

    def _check_min_file_count(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        min_count = int(target.get("min_count", 1))
        matching_paths = list(
            self._iter_matching_paths(repo_path, target.get("glob", []))
        )
        first_path = matching_paths[0] if matching_paths else None
        return RuleEvaluation(
            passed=len(matching_paths) >= min_count,
            matched_file=self._relative_path(repo_path, first_path)
            if first_path
            else None,
        )

    def _check_required_config_key(
        self,
        repo_path: Path,
        target: dict[str, Any],
    ) -> RuleEvaluation:
        pattern = str(target.get("key_pattern", ""))
        for file_path in self._iter_matching_paths(
            repo_path, target.get("file_glob", [])
        ):
            if self._file_contains_regex(file_path, pattern):
                return RuleEvaluation(
                    passed=True,
                    matched_file=self._relative_path(repo_path, file_path),
                )

        return RuleEvaluation(passed=False)

    def _first_existing_path(
        self,
        repo_path: Path,
        patterns: list[str],
        *,
        want_dir: bool | None,
    ) -> RuleEvaluation:
        for path in self._iter_matching_paths(repo_path, patterns):
            if want_dir is True and not path.is_dir():
                continue
            if want_dir is False and not path.is_file():
                continue
            return RuleEvaluation(
                passed=True,
                matched_file=self._relative_path(repo_path, path),
            )

        return RuleEvaluation(passed=False)

    def _iter_matching_paths(
        self,
        repo_path: Path,
        patterns: list[str],
    ) -> list[Path]:
        matched_paths: list[Path] = []
        for pattern in patterns:
            normalized_pattern = str(pattern).replace("\\", "/").rstrip("/")
            for path in repo_path.glob(normalized_pattern):
                if self._is_excluded(path, repo_path):
                    continue
                matched_paths.append(path)

        return sorted(
            set(matched_paths), key=lambda path: self._relative_path(repo_path, path)
        )

    def _is_excluded(self, path: Path, repo_path: Path) -> bool:
        try:
            relative_parts = path.relative_to(repo_path).parts
        except ValueError:
            return True
        return any(part in EXCLUDED_DIRS for part in relative_parts)

    def _read_dependencies(self, manifest_path: Path) -> dict[str, str | None]:
        suffix = manifest_path.suffix.lower()
        if manifest_path.name == "requirements.txt":
            return self._read_requirements_dependencies(manifest_path)
        if suffix == ".toml":
            return self._read_toml_dependencies(manifest_path)
        if manifest_path.name == "package.json":
            return self._read_package_json_dependencies(manifest_path)
        return {}

    def _read_requirements_dependencies(
        self, manifest_path: Path
    ) -> dict[str, str | None]:
        dependencies: dict[str, str | None] = {}
        for raw_line in manifest_path.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines():
            line = raw_line.split("#", maxsplit=1)[0].strip()
            if not line or line.startswith(("-", "git+")):
                continue

            match = re.match(
                r"([A-Za-z0-9_.-]+)\s*(?:\[.*?\])?\s*([<>=!~]=?.*)?$", line
            )
            if match is None:
                continue
            dependencies[match.group(1).lower()] = match.group(2)
        return dependencies

    def _read_toml_dependencies(self, manifest_path: Path) -> dict[str, str | None]:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
        dependencies: dict[str, str | None] = {}

        project = data.get("project", {})
        if isinstance(project, dict):
            for dependency in project.get("dependencies", []):
                if isinstance(dependency, str):
                    parsed = re.match(r"([A-Za-z0-9_.-]+)\s*(.*)$", dependency)
                    if parsed:
                        dependencies[parsed.group(1).lower()] = parsed.group(2) or None

        poetry_deps = (
            data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            if isinstance(data.get("tool"), dict)
            else {}
        )
        if isinstance(poetry_deps, dict):
            for package_name, version in poetry_deps.items():
                dependencies[str(package_name).lower()] = str(version)

        return dependencies

    def _read_package_json_dependencies(
        self,
        manifest_path: Path,
    ) -> dict[str, str | None]:
        data = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
        dependencies: dict[str, str | None] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            section_dependencies = data.get(section, {})
            if not isinstance(section_dependencies, dict):
                continue
            for package_name, version in section_dependencies.items():
                dependencies[str(package_name).lower()] = str(version)
        return dependencies

    def _version_satisfies(
        self,
        package_version: str | None,
        version_constraint: object,
    ) -> bool:
        if version_constraint is None:
            return True
        if package_version is None:
            return False

        constraint = str(version_constraint)
        if constraint.startswith("^"):
            return package_version.lstrip("^~>=< ").startswith(constraint[1:])
        return constraint in package_version

    def _file_contains_regex(self, file_path: Path, pattern: str) -> bool:
        return self._count_regex_matches(file_path, pattern) > 0

    def _count_regex_matches(self, file_path: Path, pattern: str) -> int:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return len(re.findall(pattern, content, flags=re.MULTILINE))

    def _build_result(self, rule: RoadmapRule, status: RuleStatus) -> RuleResult:
        return RuleResult(
            rule_id=rule.rule_id,
            status=status,
            severity=rule.severity_if_missing if status == RuleStatus.FAIL else None,
            week=rule.week,
            skill_group=rule.skill_group,
            priority=rule.priority,
            requirement=rule.requirement,
        )

    def _build_issue(self, rule: RoadmapRule) -> NormalizedIssue:
        # Issues from source=roadmap_rule have mandatory priority override. Any future
        # merge layer (Agent, static cross-check, dedup) must preserve them exactly:
        # never lower severity and never delete source=roadmap_rule findings. This is
        # a data contract, not just a local sorting convention.
        return NormalizedIssue(
            file_path=None,
            line_start=None,
            line_end=None,
            severity=rule.severity_if_missing,
            category=IssueCategory.REQUIREMENT,
            title=rule.requirement,
            description=rule.rationale_if_missing,
            suggestion=None,
            source=IssueSource.ROADMAP_RULE,
            confidence=1.0,
            raw_output={
                "rule_id": rule.rule_id,
                "week": rule.week,
                "skill_group": rule.skill_group,
                "priority": rule.priority,
                "priority_rank": ROADMAP_SOURCE_PRIORITY,
            },
        )

    def _calculate_score(
        self,
        results: list[RuleResult],
        *,
        included_priorities: set[str],
    ) -> float | None:
        included_results = [
            result for result in results if result.priority in included_priorities
        ]
        if not included_results:
            return None

        passed_count = sum(
            1
            for result in included_results
            if result.status in {RuleStatus.PASS, RuleStatus.PROVISIONAL_PASS}
        )
        return round((passed_count / len(included_results)) * 100, 2)

    def _relative_path(self, repo_path: Path, path: Path) -> str:
        return path.relative_to(repo_path).as_posix()
