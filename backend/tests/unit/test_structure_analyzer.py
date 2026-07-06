"""Tests for project structure analysis."""

from pathlib import Path

from app.analyzers.structure_analyzer import analyze_structure


def test_analyze_structure_detects_languages_frameworks_and_tree(
    tmp_path: Path,
) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"dependencies":{"next":"15.0.0","react":"19.0.0"}}',
        encoding="utf-8",
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")
    app_file = tmp_path / "app.py"
    app_file.write_text("from fastapi import FastAPI\n", encoding="utf-8")
    page_file = tmp_path / "src" / "page.tsx"
    page_file.parent.mkdir()
    page_file.write_text(
        "export default function Page() { return null }\n", encoding="utf-8"
    )

    result = analyze_structure(tmp_path, [app_file, page_file])

    assert result.project_structure["languages"] == {"python": 1, "typescript": 1}
    assert result.project_structure["primary_language"] == "python"
    assert result.project_structure["frameworks"] == [
        "fastapi",
        "nextjs",
        "node",
        "python",
        "react",
    ]
    assert result.file_tree[0]["name"] == "app.py"
    assert result.file_tree[1]["name"] == "src"
