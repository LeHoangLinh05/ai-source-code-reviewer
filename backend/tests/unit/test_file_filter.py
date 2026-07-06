"""Tests for sandbox source file filtering."""

from pathlib import Path

from app.analyzers.file_filter import filter_files, to_relative_posix_path


def test_filter_files_skips_ignored_binary_and_large_files(tmp_path: Path) -> None:
    source_file = tmp_path / "app.py"
    source_file.write_text("x=1\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("alert(1)", encoding="utf-8")
    binary_file = tmp_path / "binary.bin"
    binary_file.write_bytes(b"abc\0def")
    large_file = tmp_path / "large.py"
    large_file.write_text("x" * 20, encoding="utf-8")

    filtered_files = filter_files(tmp_path, max_source_file_size_bytes=10)

    assert [to_relative_posix_path(path, tmp_path) for path in filtered_files] == [
        "app.py"
    ]
