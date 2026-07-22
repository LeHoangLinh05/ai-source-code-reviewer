"""Fetch OWASP Top 10 Markdown source documents from GitHub."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "docs" / "rag_sources" / "raw" / "owasp"
GITHUB_API_BASE_URL = "https://api.github.com/repos/OWASP/Top10/contents"
GITHUB_REF = "master"
SUPPORTED_YEARS = ("2021", "2025")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Directory where OWASP Markdown files should be written.",
    )
    parser.add_argument(
        "--year",
        choices=SUPPORTED_YEARS,
        action="append",
        help="OWASP Top 10 year to fetch. Defaults to all supported years.",
    )
    args = parser.parse_args()

    years = tuple(args.year or SUPPORTED_YEARS)
    for year in years:
        downloaded_files = fetch_owasp_year(year, output_root=args.output_root)
        print(f"Downloaded {len(downloaded_files)} OWASP Top 10 {year} files.")


def fetch_owasp_year(year: str, *, output_root: Path) -> list[Path]:
    """Fetch one OWASP Top 10 year from `docs/en` in the GitHub repository."""

    api_url = f"{GITHUB_API_BASE_URL}/{year}/docs/en?ref={GITHUB_REF}"
    items = _load_json(api_url)
    target_dir = output_root / year / "docs" / "en"
    target_dir.mkdir(parents=True, exist_ok=True)

    downloaded_files: list[Path] = []
    for item in items:
        if item.get("type") != "file" or not str(item.get("name", "")).endswith(".md"):
            continue

        download_url = item.get("download_url")
        if not isinstance(download_url, str):
            continue

        content = _load_bytes(download_url)
        target_path = target_dir / str(item["name"])
        target_path.write_bytes(content)
        downloaded_files.append(target_path)

    return downloaded_files


def _load_json(url: str) -> list[dict[str, object]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)

    if not isinstance(data, list):
        raise ValueError(f"Expected GitHub directory listing for {url}")

    return data


def _load_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


if __name__ == "__main__":
    main()
