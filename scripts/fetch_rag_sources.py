"""Fetch external source documents used by the RAG knowledge base."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "docs" / "rag_sources" / "raw"


@dataclass(frozen=True, slots=True)
class RawFileSource:
    """One raw file that should be downloaded as-is."""

    url: str
    output_path: str


@dataclass(frozen=True, slots=True)
class GitHubDirectorySource:
    """One GitHub directory whose Markdown files should be downloaded."""

    repository: str
    ref: str
    source_path: str
    output_path: str


RAW_FILE_SOURCES = (
    RawFileSource(
        url="https://raw.githubusercontent.com/python/peps/main/peps/pep-0008.rst",
        output_path="python/peps/peps/pep-0008.rst",
    ),
    RawFileSource(
        url="https://raw.githubusercontent.com/python/peps/main/peps/pep-0020.rst",
        output_path="python/peps/peps/pep-0020.rst",
    ),
    RawFileSource(
        url="https://raw.githubusercontent.com/google/styleguide/gh-pages/pyguide.md",
        output_path="google/styleguide/pyguide.md",
    ),
)

GITHUB_DIRECTORY_SOURCES = (
    GitHubDirectorySource(
        repository="fastapi/fastapi",
        ref="master",
        source_path="docs/en/docs/tutorial",
        output_path="fastapi/fastapi/docs/en/docs/tutorial",
    ),
    GitHubDirectorySource(
        repository="fastapi/fastapi",
        ref="master",
        source_path="docs/en/docs/advanced",
        output_path="fastapi/fastapi/docs/en/docs/advanced",
    ),
    GitHubDirectorySource(
        repository="OWASP/CheatSheetSeries",
        ref="master",
        source_path="cheatsheets",
        output_path="owasp/CheatSheetSeries/cheatsheets",
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Directory where source files should be written.",
    )
    args = parser.parse_args()

    downloaded_files: list[Path] = []
    for raw_source in RAW_FILE_SOURCES:
        downloaded_files.append(
            download_raw_file(raw_source, output_root=args.output_root)
        )

    for directory_source in GITHUB_DIRECTORY_SOURCES:
        downloaded_files.extend(
            download_github_directory(directory_source, output_root=args.output_root)
        )

    print(f"Downloaded {len(downloaded_files)} RAG source files.")


def download_raw_file(source: RawFileSource, *, output_root: Path) -> Path:
    """Download one raw URL into the RAG source directory."""

    target_path = output_root / source.output_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(_load_bytes(source.url))
    return target_path


def download_github_directory(
    source: GitHubDirectorySource,
    *,
    output_root: Path,
) -> list[Path]:
    """Download all Markdown files under a GitHub directory recursively."""

    tree = _load_github_tree(source.repository, source.ref)
    source_prefix = f"{source.source_path.rstrip('/')}/"
    target_root = output_root / source.output_path
    downloaded_files: list[Path] = []

    for item in tree:
        path = str(item.get("path", ""))
        if item.get("type") != "blob":
            continue

        if not path.startswith(source_prefix) or not path.endswith(".md"):
            continue

        relative_path = path.removeprefix(source_prefix)
        raw_url = (
            f"https://raw.githubusercontent.com/{source.repository}/{source.ref}/{path}"
        )
        target_path = target_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(_load_bytes(raw_url))
        downloaded_files.append(target_path)

    return downloaded_files


def _load_github_tree(repository: str, ref: str) -> list[dict[str, object]]:
    url = f"https://api.github.com/repos/{repository}/git/trees/{ref}?recursive=1"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)

    tree = data.get("tree")
    if not isinstance(tree, list):
        raise ValueError(f"Expected GitHub tree response for {repository}@{ref}")

    return tree


def _load_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


if __name__ == "__main__":
    main()
