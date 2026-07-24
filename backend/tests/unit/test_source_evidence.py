"""Tests for backend-directed probe source evidence."""

from app.ai.review.source_evidence import (
    has_source_line_evidence,
    source_chunk_keys,
)


def test_source_provenance_uses_probe_retrieval_results_only() -> None:
    documents: list[object] = [
        {
            "tool_name": "unrelated_source_tool",
            "output": {
                "status": "ok",
                "file_path": "app/a.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 10,
            },
        },
        {
            "tool_name": "probe_retrieval",
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

    assert source_chunk_keys(documents) == {("app/b.py", 2)}
    assert has_source_line_evidence(
        documents, file_path="app/b.py", line_start=45, line_end=50
    )


def test_source_line_evidence_can_span_adjacent_probe_chunks() -> None:
    documents: list[object] = [
        {
            "tool_name": "probe_retrieval",
            "output": {
                "status": "ok",
                "results": [
                    {
                        "file_path": "app/auth.py",
                        "chunk_index": 5,
                        "line_start": 23,
                        "line_end": 25,
                    },
                    {
                        "file_path": "app/auth.py",
                        "chunk_index": 6,
                        "line_start": 26,
                        "line_end": 38,
                    },
                ],
            },
        },
    ]

    assert has_source_line_evidence(
        documents, file_path="app/auth.py", line_start=23, line_end=38
    )
