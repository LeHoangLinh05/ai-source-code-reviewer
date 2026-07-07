"""Git helpers used by deterministic roadmap rules."""

from pathlib import Path
import subprocess


def list_tracked_files(repo_path: Path) -> set[str]:
    """Return POSIX-style files tracked by git in the cloned repository."""

    completed_process = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed_process.returncode != 0:
        return set()

    return {
        line.strip().replace("\\", "/")
        for line in completed_process.stdout.splitlines()
        if line.strip()
    }
