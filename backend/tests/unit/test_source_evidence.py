"""Tests for provenance shared by direct and semantic source tools."""

from app.ai.source_evidence import (
    has_source_line_evidence,
    source_chunk_keys,
)


def test_source_provenance_unions_read_and_semantic_chunks() -> None:
    documents: list[object] = [
        {
            "tool_name": "read_file_chunk",
            "output": {
                "status": "ok",
                "file_path": "app/a.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 10,
            },
        },
        {
            "tool_name": "search_code_semantic",
            "output": {
                "status": "ok",
                "results": [
                    {
                        "status": "ok",
                        "file_path": "app/b.py",
                        "chunk_index": 2,
                        "line_start": 40,
                        "line_end": 78,
                    }
                ],
            },
        },
    ]

    assert source_chunk_keys(documents) == {
        ("app/a.py", 0),
        ("app/b.py", 2),
    }
    assert has_source_line_evidence(
        documents,
        file_path="app/b.py",
        line_start=45,
        line_end=50,
    )
