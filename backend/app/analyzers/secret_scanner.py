"""Secret scanning analyzer for detecting sensitive values in source code."""

import re
from dataclasses import dataclass
from pathlib import Path

from app.analyzers.file_filter import to_relative_posix_path
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue


@dataclass(frozen=True, slots=True)
class SecretPattern:
    """Regex metadata used to build normalized secret findings."""

    name: str
    regex: re.Pattern[str]
    title: str
    description: str


SECRET_PATTERNS = (
    SecretPattern(
        name="aws_access_key",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        title="Potential AWS access key committed",
        description=(
            "A value matching the AWS access key format appears in source code."
        ),
    ),
    SecretPattern(
        name="private_key",
        regex=re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        title="Private key material committed",
        description="A private key header appears in source code.",
    ),
    SecretPattern(
        name="generic_api_key",
        regex=re.compile(
            r"""(?ix)
            \b(api[_-]?key|access[_-]?token|secret|token)\b
            \s*[:=]\s*
            ["'][A-Za-z0-9_\-./=]{16,}["']
            """
        ),
        title="Potential hardcoded API key",
        description="A hardcoded token-like value appears in source code.",
    ),
    SecretPattern(
        name="hardcoded_password",
        regex=re.compile(
            r"""(?ix)
            \b(password|passwd|pwd)\b
            \s*[:=]\s*
            ["'][^"'\s]{8,}["']
            """
        ),
        title="Potential hardcoded password",
        description="A hardcoded password-like value appears in source code.",
    ),
)

SECRET_MASK = "***MASKED***"
SECRET_ASSIGNMENT_REGEX = re.compile(
    r"""(?ix)
    (?P<prefix>
        \b(api[_-]?key|access[_-]?token|secret|token|password|passwd|pwd)\b
        \s*[:=]\s*
    )
    (?P<quote>["']?)
    (?P<value>[^\s"',}\]]{6,})
    (?P=quote)
    """
)


def scan_secrets(
    sandbox_path: Path, filtered_files: list[Path]
) -> list[NormalizedIssue]:
    """Scan filtered text files for high-confidence secret patterns."""

    issues: list[NormalizedIssue] = []
    for file_path in filtered_files:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        relative_path = to_relative_posix_path(file_path, sandbox_path)
        for line_number, line in enumerate(lines, start=1):
            issues.extend(_scan_line(relative_path, line_number, line))

    return issues


def mask_secret_values(content: str) -> str:
    """Mask secret-like values while preserving surrounding file structure."""

    masked_content = SECRET_ASSIGNMENT_REGEX.sub(_mask_assignment_match, content)
    for pattern in SECRET_PATTERNS:
        masked_content = pattern.regex.sub(_mask_pattern_match, masked_content)

    return masked_content


def _mask_assignment_match(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{SECRET_MASK}{quote}"


def _mask_pattern_match(match: re.Match[str]) -> str:
    if SECRET_MASK in match.group(0):
        return match.group(0)

    return SECRET_MASK


def _scan_line(
    file_path: str,
    line_number: int,
    line: str,
) -> list[NormalizedIssue]:
    issues: list[NormalizedIssue] = []
    for pattern in SECRET_PATTERNS:
        if pattern.regex.search(line) is None:
            continue

        issues.append(
            NormalizedIssue(
                file_path=file_path,
                line_start=line_number,
                line_end=line_number,
                severity=IssueSeverity.CRITICAL,
                category=IssueCategory.SECURITY,
                title=pattern.title,
                description=pattern.description,
                suggestion=(
                    "Remove the secret from source control, rotate the credential, "
                    "and load it from environment variables or a secret manager."
                ),
                source=IssueSource.SECRET_SCANNER,
                confidence=0.95,
                raw_output={
                    "pattern": pattern.name,
                    "redacted": True,
                },
            )
        )

    return issues
