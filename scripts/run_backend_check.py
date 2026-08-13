"""Run one backend quality gate from the correct project directory."""

from __future__ import annotations

import argparse
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
QUALITY_CACHE_ROOT = REPOSITORY_ROOT / ".tmp" / "quality"


class CheckName(StrEnum):
    """Backend checks exposed to local development hooks."""

    RUFF_CHECK = "ruff-check"
    RUFF_FORMAT = "ruff-format"
    MYPY = "mypy"
    PYTEST = "pytest"


CHECK_COMMANDS: dict[CheckName, list[str]] = {
    CheckName.RUFF_CHECK: [sys.executable, "-m", "ruff", "check", "."],
    CheckName.RUFF_FORMAT: [
        sys.executable,
        "-m",
        "ruff",
        "format",
        "--check",
        ".",
    ],
    CheckName.MYPY: [
        sys.executable,
        "-m",
        "mypy",
        ".",
        "--cache-dir",
        str(QUALITY_CACHE_ROOT / "mypy"),
    ],
    CheckName.PYTEST: [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(QUALITY_CACHE_ROOT / "pytest"),
        "-p",
        "no:cacheprovider",
    ],
}


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the quality gate selected by a pre-commit hook."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", type=CheckName, choices=CheckName)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Run the selected check and preserve its process exit code."""

    check = parse_args(arguments).check
    # Commands come exclusively from the closed static mapping above.
    completed_process = subprocess.run(  # noqa: S603
        CHECK_COMMANDS[check],
        cwd=BACKEND_ROOT,
        check=False,
    )
    return completed_process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
