"""Tests for backend-directed probe retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

import pytest

from app.ai.probe_review import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeResponse,
    ProbeRetrievalService,
    _RoadmapMetadataStore,
    _judge_prompt,
    _probe_judge_response_from_payload,
    _trim_bundles,
)
from app.ai.roadmap.knowledge import load_roadmap_requirements
from app.ai.roadmap.selection import ROADMAP_PROFILE_ID, build_roadmap_context
from app.ai.semantic_audit_plan import build_semantic_audit_plan
from app.db.mongodb import CHUNK_METADATA_COLLECTION


class _FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, length: int | None) -> list[dict[str, object]]:
        _ = length
        return self.documents


class _FakeCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self, filters: dict[str, object]) -> _FakeCursor:
        job_id = filters.get("job_id")
        return _FakeCursor(
            [
                document
                for document in self.documents
                if job_id is None or document.get("job_id") == job_id
            ]
        )


class _FakeDatabase:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.collection = _FakeCollection(documents)

    def __getitem__(self, collection_name: str) -> _FakeCollection:
        assert collection_name == CHUNK_METADATA_COLLECTION
        return self.collection


class _TraceWriter:
    def __init__(self) -> None:
        self.logs: list[dict[str, object]] = []

    async def write_synthetic_tool_log(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, object],
        output: dict[str, object],
    ) -> None:
        self.logs.append(
            {
                "tool_name": tool_name,
                "input": tool_input,
                "output": output,
            }
        )


def test_real_roadmap_catalog_rules_are_in_unified_probe_plan() -> None:
    roadmap_context = build_roadmap_context(
        {"rule_profile": {"id": ROADMAP_PROFILE_ID}},
        vectorstore=_RoadmapMetadataStore(),
    )
    assert roadmap_context is not None

    plan = build_semantic_audit_plan(
        roadmap_context=roadmap_context,
        files_to_review=[],
        static_issues=[],
    )

    planned_rule_ids = {rule_id for item in plan for rule_id in _related_rule_ids(item)}
    catalog_rule_ids = {
        requirement.rule_id for requirement in load_roadmap_requirements()
    }

    assert len(catalog_rule_ids) == 79
    assert planned_rule_ids == catalog_rule_ids


def test_probe_judge_response_accepts_llm_text_and_null_evidence() -> None:
    response = ProbeJudgeResponse.model_validate(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "claim_type": "bug",
                    "title": "Logout does not revoke refresh token",
                    "description": "The provided chunk returns success without revocation.",
                    "suggestion": "Revoke or blacklist refresh tokens on logout.",
                    "severity": "high",
                    "category": "security",
                    "confidence": 0.91,
                    "file_path": "backend/app/auth.py",
                    "line_start": 42,
                    "line_end": 45,
                    "supporting_evidence": "logout returns without blacklist update",
                    "contradicting_evidence": None,
                }
            ]
        }
    )

    candidate = response.candidates[0]

    assert candidate.supporting_evidence == []
    assert candidate.contradicting_evidence == []


def test_probe_judge_response_wraps_single_evidence_object() -> None:
    response = ProbeJudgeResponse.model_validate(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "title": "Refresh token is not hashed",
                    "description": "The provided chunk stores the raw refresh token.",
                    "severity": "medium",
                    "category": "security",
                    "confidence": 0.8,
                    "file_path": "backend/app/auth.py",
                    "line_start": 11,
                    "line_end": 12,
                    "supporting_evidence": {
                        "file_path": "backend/app/auth.py",
                        "chunk_index": 0,
                        "line_start": 11,
                        "line_end": 12,
                    },
                }
            ]
        }
    )

    assert len(response.candidates[0].supporting_evidence) == 1


def test_probe_judge_response_keeps_valid_candidates_from_mixed_batch() -> None:
    response = _probe_judge_response_from_payload(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "title": "Logout does not revoke refresh token",
                    "description": "The provided chunk returns success without revocation.",
                    "severity": "high",
                    "category": "security",
                    "confidence": 0.91,
                    "file_path": "backend/app/auth.py",
                    "line_start": 42,
                    "line_end": 45,
                    "supporting_evidence": None,
                },
                {
                    "verdict": "issue",
                    "title": "Invalid candidate",
                    "description": "This candidate has an invalid confidence type.",
                    "severity": "high",
                    "category": "security",
                    "confidence": "very high",
                    "file_path": "backend/app/auth.py",
                    "line_start": 1,
                    "line_end": 2,
                },
            ]
        }
    )

    assert len(response.candidates) == 1
    assert response.candidates[0].title == "Logout does not revoke refresh token"
    assert response.schema_rejected_count == 1


def test_probe_judge_prompt_requires_evidence_arrays() -> None:
    prompt = _judge_prompt(
        [
            _bundle(
                probe={
                    "probe_id": "security.jwt_session_auth",
                    "category": "security",
                    "priority": "high",
                    "query": "JWT token logout blacklist",
                },
                chunks=[
                    _candidate_chunk(
                        file_path="backend/app/auth.py",
                        content="def logout():\n    return {'ok': True}",
                    )
                ],
            )
        ]
    )

    assert "supporting_evidence and contradicting_evidence must be arrays" in prompt
    assert "never use null or a string" in prompt
    assert '"output_schema"' in prompt


def test_trim_bundles_handles_tied_chunk_rank_without_comparing_chunks() -> None:
    bundles = [
        _bundle(
            probe={
                "probe_id": "security.jwt_session_auth",
                "category": "security",
                "priority": "high",
                "query": "JWT token logout blacklist",
            },
            chunks=[
                _candidate_chunk(
                    file_path="backend/app/auth.py",
                    chunk_index=0,
                    content="def login():\n    return token",
                ),
                _candidate_chunk(
                    file_path="backend/app/auth.py",
                    chunk_index=1,
                    content="def logout():\n    return {'ok': True}",
                ),
                _candidate_chunk(
                    file_path="backend/app/auth.py",
                    chunk_index=2,
                    content="def refresh():\n    return refresh_token",
                ),
            ],
        )
    ]

    trimmed = _trim_bundles(bundles, max_chunks=1)

    assert len(trimmed[0].candidate_chunks) == 1
    assert trimmed[0].candidate_chunks[0].chunk_index == 0


@pytest.mark.asyncio
async def test_probe_retrieval_runs_every_probe_without_llm_calls() -> None:
    job_id = uuid4()
    database = _FakeDatabase(
        [
            _chunk(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content="def login():\n    return create_access_token(user)",
            )
        ]
    )
    trace_writer = _TraceWriter()
    service = ProbeRetrievalService(
        database=database,  # type: ignore[arg-type]
        enable_semantic_search=False,
    )
    probes = [
        {
            "probe_id": "security.jwt_session_auth",
            "category": "security",
            "priority": "high",
            "query": "JWT login token refresh logout blacklist",
            "top_k": 3,
        },
        {
            "probe_id": "maintainability.dead_complex_code",
            "category": "maintainability",
            "priority": "medium",
            "query": "dead code complex function",
            "top_k": 3,
        },
    ]

    bundles = await service.retrieve(
        job_id=job_id,
        probes=probes,
        trace_writer=trace_writer,
    )

    assert [bundle.probe["probe_id"] for bundle in bundles] == [
        "security.jwt_session_auth",
        "maintainability.dead_complex_code",
    ]
    assert [log["tool_name"] for log in trace_writer.logs] == [
        "probe_retrieval",
        "probe_retrieval",
    ]


@pytest.mark.asyncio
async def test_jwt_session_probe_retrieves_auth_token_chunk() -> None:
    job_id = uuid4()
    database = _FakeDatabase(
        [
            _chunk(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content=(
                    "def login(username, password):\n"
                    "    if not verify_password(password):\n"
                    "        return None\n"
                    "    access_token = create_access_token(username)\n"
                    "    refresh_token = create_refresh_token(username)\n"
                    "    return access_token, refresh_token\n"
                ),
            ),
            _chunk(
                job_id=job_id,
                file_path="backend/app/products.py",
                content="def list_products():\n    return []",
            ),
        ]
    )
    service = ProbeRetrievalService(
        database=database,  # type: ignore[arg-type]
        enable_semantic_search=False,
    )

    bundles = await service.retrieve(
        job_id=job_id,
        probes=[
            {
                "probe_id": "security.jwt_session_auth",
                "category": "security",
                "priority": "high",
                "query": "JWT session auth login token refresh password verify",
                "top_k": 3,
            }
        ],
        trace_writer=_TraceWriter(),
    )

    assert bundles[0].retrieval_status == "ok"
    assert bundles[0].candidate_chunks[0].file_path == "backend/app/auth.py"


def _chunk(
    *,
    job_id: object,
    file_path: str,
    content: str,
) -> dict[str, object]:
    return {
        "job_id": str(job_id),
        "file_path": file_path,
        "language": "python",
        "chunk_type": "function",
        "chunk_index": 0,
        "total_chunks": 1,
        "function_name": None,
        "class_name": None,
        "line_start": 1,
        "line_end": max(1, len(content.splitlines())),
        "imports": [],
        "module": "app",
        "risk_area": "security" if "auth" in file_path else "general",
        "has_static_issues": False,
        "token_count": 20,
        "chunk_text": content,
    }


def _candidate_chunk(
    *,
    file_path: str,
    chunk_index: int = 0,
    content: str,
) -> ProbeCandidateChunk:
    return ProbeCandidateChunk(
        file_path=file_path,
        chunk_index=chunk_index,
        line_start=1,
        line_end=max(1, len(content.splitlines())),
        language="python",
        risk_area="security",
        content=content,
        semantic_score=0.0,
        lexical_score=1.0,
        path_score=0.2,
        static_score=0.0,
        final_score=1.2,
    )


def _bundle(
    *,
    probe: dict[str, object],
    chunks: list[ProbeCandidateChunk],
) -> ProbeEvidenceBundle:
    return ProbeEvidenceBundle(
        probe=probe,
        retrieval_status="ok",
        candidate_chunks=chunks,
        strategies_used=["exact"],
    )


def _related_rule_ids(item: object) -> list[str]:
    if not isinstance(item, Mapping):
        return []

    rule_ids = item.get("related_rule_ids")
    if not isinstance(rule_ids, list):
        return []

    return [rule_id for rule_id in rule_ids if isinstance(rule_id, str)]
