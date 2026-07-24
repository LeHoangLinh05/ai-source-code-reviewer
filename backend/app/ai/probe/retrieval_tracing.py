"""Redacted trace serialization for probe retrieval."""

from __future__ import annotations

from hashlib import sha256

from app.ai.probe.contracts import ProbeDefinition
from app.ai.probe.models import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    SyntheticTraceWriter,
)

PROBE_RETRIEVAL_TOOL_NAME = "probe_retrieval"


async def _write_probe_retrieval_trace(
    *,
    trace_writer: SyntheticTraceWriter,
    probe: ProbeDefinition,
    bundle: ProbeEvidenceBundle,
    duration_ms: int,
) -> None:
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_RETRIEVAL_TOOL_NAME,
        tool_input={
            "probe_id": probe.probe_id,
            "lane": probe.lane.value,
            "category": probe.category,
            "related_rule_ids": list(probe.related_rule_ids),
            "retrieval_queries": list(probe.retrieval_queries),
            "query": probe.primary_query,
        },
        output={
            "status": bundle.retrieval_status,
            "summary": "source content redacted from tool trace",
            "duration_ms": duration_ms,
            "strategies_used": bundle.strategies_used,
            "candidate_counts": bundle.strategy_candidate_counts,
            "selected_count": bundle.selected_count_before_trim,
            "trimmed_count": bundle.trimmed_count,
            "sent_to_judge": len(bundle.candidate_chunks),
            "result_count": len(bundle.candidate_chunks),
            "results": [_chunk_trace(chunk) for chunk in bundle.candidate_chunks],
        },
    )


def _chunk_trace(chunk: ProbeCandidateChunk) -> dict[str, object]:
    content_bytes = chunk.content.encode("utf-8")
    return {
        "status": "ok",
        "summary": "source content redacted from tool trace",
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "semantic_score": chunk.semantic_score,
        "lexical_score": chunk.lexical_score,
        "final_score": chunk.final_score,
        "content_sha256": sha256(content_bytes).hexdigest(),
        "content_size": len(content_bytes),
        "chunk_key": [chunk.file_path, chunk.chunk_index],
    }
