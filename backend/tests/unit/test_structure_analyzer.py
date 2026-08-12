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

    result = analyze_structure(
        tmp_path,
        [app_file, package_json, page_file, requirements],
    )

    assert result.project_structure["languages"] == {
        "python": 1,
        "json": 1,
        "typescript": 1,
    }
    assert result.project_structure["primary_language"] == "python"
    assert result.project_structure["frameworks"] == [
        "fastapi",
        "nextjs",
        "node",
        "python",
        "react",
    ]
    assert result.file_tree[0]["name"] == "app.py"
    assert [entry["name"] for entry in result.file_tree] == [
        "app.py",
        "package.json",
        "requirements.txt",
        "src",
    ]


def test_analyze_structure_detects_nested_monorepo_manifests(tmp_path: Path) -> None:
    backend_path = tmp_path / "backend"
    frontend_path = tmp_path / "frontend"
    backend_path.mkdir()
    frontend_path.mkdir()
    requirements = backend_path / "requirements-prod.txt"
    requirements.write_text("FastAPI[standard]>=0.115\n", encoding="utf-8")
    package_json = frontend_path / "package.json"
    package_json.write_text(
        '{"dependencies":{"next":"15.0.0","react":"19.0.0"}}',
        encoding="utf-8",
    )
    app_file = backend_path / "main.py"
    app_file.write_text("app = object()\n", encoding="utf-8")

    result = analyze_structure(
        tmp_path,
        [app_file, package_json, requirements],
    )

    assert result.project_structure["frameworks"] == [
        "fastapi",
        "nextjs",
        "node",
        "python",
        "react",
    ]


def test_analyze_structure_detects_fastapi_import_without_manifest(
    tmp_path: Path,
) -> None:
    app_file = tmp_path / "main.py"
    app_file.write_text("from fastapi import FastAPI\n", encoding="utf-8")

    result = analyze_structure(tmp_path, [app_file])

    assert result.project_structure["frameworks"] == ["fastapi", "python"]


def test_analyze_structure_ignores_malformed_nested_manifests(tmp_path: Path) -> None:
    package_json = tmp_path / "frontend" / "package.json"
    package_json.parent.mkdir()
    package_json.write_text("{not-json", encoding="utf-8")
    pyproject = tmp_path / "backend" / "pyproject.toml"
    pyproject.parent.mkdir()
    pyproject.write_text("[project\n", encoding="utf-8")

    result = analyze_structure(tmp_path, [package_json, pyproject])

    assert result.project_structure["frameworks"] == ["node"]
