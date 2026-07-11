"""Tests for report issue response enrichment."""

from app.services.report_service import _source_context_from_chunk


def test_source_context_falls_back_to_persisted_chunk() -> None:
    source_context = _source_context_from_chunk(
        {
            "line_start": 10,
            "line_end": 14,
            "chunk_text": "ten\neleven\nproblem\nthirteen\nfourteen\n",
        },
        line_start=12,
        line_end=12,
        context_radius=1,
    )

    assert source_context == {
        "start_line": 11,
        "lines": ["eleven", "problem", "thirteen"],
    }
