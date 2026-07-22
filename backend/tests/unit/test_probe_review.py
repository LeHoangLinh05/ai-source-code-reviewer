"""Tests for backend-directed probe retrieval."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.ai.probe_contracts import ProbeDefinition, ProbeLane
from app.ai.probe_review import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssueCandidate,
    ProbeJudgeResponse,
    ProbeRetrievalService,
    _candidate_rule_id,
    _dependency_manifest_contradicts_candidate,
    _expand_related_candidates,
    _fuse_probe_candidates,
    _judge_batches,
    _judge_prompt,
    _path_category,
    _probe_judge_response_from_payload,
    _RoadmapMetadataStore,
    _structural_candidates,
    _supporting_bundle_chunk,
    _trim_bundles,
    _unique_candidates,
)
from app.ai.roadmap.knowledge import load_roadmap_requirements
from app.ai.roadmap.selection import ROADMAP_PROFILE_ID, build_roadmap_context
from app.ai.semantic_audit_plan import BASELINE_PROBES, build_semantic_audit_plan
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


class _FakeSemanticRetriever:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self.results = results
        self.requests: list[object] = []

    def search_many(self, requests: list[object]) -> list[list[SimpleNamespace]]:
        self.requests = requests
        return [self.results for _request in requests]


@pytest.mark.parametrize(
    ("probe_id", "content"),
    [
        (
            "security.sql_nosql_injection",
            "sql = f\"SELECT * FROM records WHERE name='{value}'\"\nexecute(sql)",
        ),
        (
            "security.command_injection",
            'command = f"backup {filename}"\nsubprocess.Popen(command, shell=True)',
        ),
        (
            "security.ssrf_external_calls",
            "async with httpx.AsyncClient() as client:\n"
            "    await client.get(target_url)",
        ),
        (
            "security.object_authorization",
            "async def get_record(record_id, current_user):\n"
            "    return await repo.get_by_id(record_id)",
        ),
        (
            "security.object_authorization",
            "async def get_profile(user_id, current_user):\n"
            "    return await users.get_by_id(user_id)",
        ),
        (
            "security.mass_assignment",
            "for field, value in payload.items():\n    setattr(account, field, value)",
        ),
        (
            "security.weak_password_hash",
            "password_digest = hashlib.md5(new_password.encode()).hexdigest()",
        ),
        (
            "security.reset_token_lifecycle",
            "reset_token = make_token()\nawait redis.set('reset', reset_token)",
        ),
        (
            "bug.async_concurrency",
            "inventory = await repo.get_by_product(item)\n"
            "new_stock = inventory.quantity - item.quantity\n"
            "await repo.update_stock(new_stock)",
        ),
        (
            "performance.n_plus_one",
            "for account in accounts:\n    rows = await db.execute(query(account.id))",
        ),
        (
            "performance.pagination_bounds",
            "all_rows = list(result.scalars().all())\n"
            "filtered_rows = [item for item in all_rows]\n"
            "return filtered_rows[offset : offset + size]",
        ),
        (
            "maintainability.resource_lifecycle",
            "async def load():\n"
            "    engine = create_async_engine(settings.database_url)",
        ),
    ],
)
def test_python_structural_candidates_cover_high_value_patterns(
    probe_id: str,
    content: str,
) -> None:
    probe = next(probe for probe in BASELINE_PROBES if probe.probe_id == probe_id)
    document = _chunk(
        job_id=uuid4(),
        file_path="backend/app/example.py",
        content=content,
    )

    candidates = _structural_candidates(
        chunk_documents=[document],
        probe=probe,
    )

    assert len(candidates) == 1
    assert candidates[0].strategies == ("structural",)


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
    assert len([item for item in plan if item.lane is ProbeLane.ROADMAP]) == 28


def test_probe_judge_response_accepts_llm_text_and_null_evidence() -> None:
    response = ProbeJudgeResponse.model_validate(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "claim_type": "bug",
                    "title": "Logout does not revoke refresh token",
                    "description": (
                        "The provided chunk returns success without revocation."
                    ),
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
                    "description": (
                        "The provided chunk returns success without revocation."
                    ),
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


def test_batched_dependency_candidate_uses_matching_rule_id() -> None:
    requirements = {
        requirement.rule_id: requirement for requirement in load_roadmap_requirements()
    }
    candidate = _probe_candidate(
        title="Missing Required Dependency: Axios",
        description="The project is missing the required Axios dependency.",
        file_path="frontend/package.json",
        line_start=13,
        line_end=13,
    )

    rule_id = _candidate_rule_id(
        candidate,
        [
            _bundle(
                probe={
                    "probe_id": (
                        "structure.frontend_next_js_15.required_dependency.rc_w4_01"
                    ),
                    "related_rule_ids": ["RC-W4-01", "RC-W4-02", "RC-W4-03"],
                },
                chunks=[],
            )
        ],
        roadmap_by_id=requirements,
    )

    assert rule_id == "RC-W4-03"


def test_dependency_manifest_rejects_missing_claim_when_package_exists() -> None:
    requirements = {
        requirement.rule_id: requirement for requirement in load_roadmap_requirements()
    }
    candidate = _probe_candidate(
        title="Missing Required Dependency: TailwindCSS",
        description="The project is missing the required TailwindCSS dependency.",
        file_path="frontend/package.json",
        line_start=21,
        line_end=21,
    )
    manifest_chunk = _candidate_chunk(
        file_path="frontend/package.json",
        content=(
            "{\n"
            '  "dependencies": {"axios": "^1.17.0", "next": "15.1.0"},\n'
            '  "devDependencies": {"tailwindcss": "^4"}\n'
            "}\n"
        ),
    )

    assert _dependency_manifest_contradicts_candidate(
        candidate=candidate,
        rule=requirements["RC-W4-02"],
        evidence_chunk=manifest_chunk,
    )


def test_dependency_manifest_allows_version_mismatch_claim() -> None:
    requirements = {
        requirement.rule_id: requirement for requirement in load_roadmap_requirements()
    }
    candidate = _probe_candidate(
        title="Required Dependency Version Mismatch: Next.js 15",
        description="The project is using Next.js version 16.2.9.",
        file_path="frontend/package.json",
        line_start=15,
        line_end=15,
    )
    manifest_chunk = _candidate_chunk(
        file_path="frontend/package.json",
        content='{"dependencies": {"next": "16.2.9"}}',
    )

    assert not _dependency_manifest_contradicts_candidate(
        candidate=candidate,
        rule=requirements["RC-W4-01"],
        evidence_chunk=manifest_chunk,
    )


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


def test_supporting_bundle_chunk_requires_explicit_candidate_evidence() -> None:
    candidate = ProbeJudgeResponse.model_validate(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "title": "Missing logout revocation",
                    "description": "Logout returns without revoking tokens.",
                    "severity": "high",
                    "category": "security",
                    "confidence": 0.91,
                    "file_path": "backend/app/auth.py",
                    "line_start": 2,
                    "line_end": 2,
                    "supporting_evidence": [],
                }
            ]
        }
    ).candidates[0]

    assert (
        _supporting_bundle_chunk(
            candidate,
            [
                _bundle(
                    probe={"probe_id": "security.jwt_session_auth"},
                    chunks=[
                        _candidate_chunk(
                            file_path="backend/app/auth.py",
                            content="def logout():\n    return {'ok': True}",
                        )
                    ],
                )
            ],
        )
        is None
    )


def test_supporting_bundle_chunk_rejects_unsupported_reference() -> None:
    candidate = _probe_candidate(
        title="Missing logout revocation",
        description="Logout returns without revoking tokens.",
        file_path="backend/app/auth.py",
        line_start=2,
        line_end=2,
    )

    assert (
        _supporting_bundle_chunk(
            candidate,
            [
                _bundle(
                    probe={"probe_id": "security.jwt_session_auth"},
                    chunks=[
                        _candidate_chunk(
                            file_path="backend/app/auth.py",
                            chunk_index=1,
                            content="def logout():\n    return {'ok': True}",
                        )
                    ],
                )
            ],
        )
        is None
    )


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
        _definition(
            {
                "probe_id": "security.jwt_session_auth",
                "category": "security",
                "priority": "high",
                "query": "JWT login token refresh logout blacklist",
                "top_k": 3,
            }
        ),
        _definition(
            {
                "probe_id": "maintainability.dead_complex_code",
                "category": "maintainability",
                "priority": "medium",
                "query": "dead code complex function",
                "top_k": 3,
            }
        ),
    ]

    bundles = await service.retrieve(
        job_id=job_id,
        probes=probes,
        trace_writer=trace_writer,
    )

    assert [bundle.probe.probe_id for bundle in bundles] == [
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
            _definition(
                {
                    "probe_id": "security.jwt_session_auth",
                    "category": "security",
                    "priority": "high",
                    "query": "JWT session auth login token refresh password verify",
                    "top_k": 3,
                }
            )
        ],
        trace_writer=_TraceWriter(),
    )

    assert bundles[0].retrieval_status == "ok"
    assert bundles[0].candidate_chunks[0].file_path == "backend/app/auth.py"


@pytest.mark.asyncio
async def test_probe_retrieval_rrf_prefers_cross_strategy_consensus() -> None:
    job_id = uuid4()
    database = _FakeDatabase(
        [
            _chunk(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content="def status():\n    return {'ok': True}",
            ),
            _chunk(
                job_id=job_id,
                file_path="backend/app/sessions.py",
                content=(
                    "def logout(refresh_token):\n"
                    "    revoke_refresh_token(refresh_token)\n"
                    "    blacklist_token(refresh_token)\n"
                ),
            ),
        ]
    )
    semantic_retriever = _FakeSemanticRetriever(
        [
            _semantic_result(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content="def status():\n    return {'ok': True}",
                score=0.99,
            ),
            _semantic_result(
                job_id=job_id,
                file_path="backend/app/sessions.py",
                content=(
                    "def logout(refresh_token):\n"
                    "    revoke_refresh_token(refresh_token)\n"
                    "    blacklist_token(refresh_token)\n"
                ),
                score=0.62,
            ),
        ]
    )
    service = ProbeRetrievalService(
        database=database,  # type: ignore[arg-type]
        code_retriever=semantic_retriever,  # type: ignore[arg-type]
        enable_semantic_search=True,
        chunks_per_probe=2,
    )

    bundles = await service.retrieve(
        job_id=job_id,
        probes=[
            _definition(
                {
                    "probe_id": "security.jwt_session_auth",
                    "category": "security",
                    "priority": "high",
                    "query": "logout refresh token blacklist revoke",
                    "top_k": 2,
                }
            )
        ],
        trace_writer=_TraceWriter(),
    )

    assert bundles[0].candidate_chunks[0].file_path == "backend/app/sessions.py"
    assert bundles[0].strategies_used == [
        "semantic",
        "bm25",
        "exact",
        "structural",
    ]


@pytest.mark.asyncio
async def test_probe_retrieval_keeps_file_diversity_in_top_results() -> None:
    job_id = uuid4()
    database = _FakeDatabase(
        [
            _chunk(
                job_id=job_id,
                file_path="backend/app/auth.py",
                content="def login():\n    return create_access_token(user)",
            ),
            {
                **_chunk(
                    job_id=job_id,
                    file_path="backend/app/auth.py",
                    content="def refresh():\n    return create_refresh_token(user)",
                ),
                "chunk_index": 1,
            },
            _chunk(
                job_id=job_id,
                file_path="backend/app/users.py",
                content="def authenticate_user():\n    return verify_password(user)",
            ),
        ]
    )
    service = ProbeRetrievalService(
        database=database,  # type: ignore[arg-type]
        enable_semantic_search=False,
        chunks_per_probe=2,
    )

    bundles = await service.retrieve(
        job_id=job_id,
        probes=[
            _definition(
                {
                    "probe_id": "security.jwt_session_auth",
                    "category": "security",
                    "priority": "high",
                    "query": "login refresh token authenticate password",
                    "top_k": 2,
                }
            )
        ],
        trace_writer=_TraceWriter(),
    )

    assert {chunk.file_path for chunk in bundles[0].candidate_chunks} == {
        "backend/app/auth.py",
        "backend/app/users.py",
    }


@pytest.mark.asyncio
async def test_file_scope_is_a_hard_retrieval_filter() -> None:
    job_id = uuid4()
    scoped_path = "backend/app/routes/accounts.py"
    service = ProbeRetrievalService(
        database=_FakeDatabase(
            [
                _chunk(
                    job_id=job_id,
                    file_path=scoped_path,
                    content="def update_account(): return payload",
                ),
                _chunk(
                    job_id=job_id,
                    file_path="backend/app/routes/admin.py",
                    content="def update_account(): return payload",
                ),
            ]
        ),  # type: ignore[arg-type]
        enable_semantic_search=False,
    )

    bundles = await service.retrieve(
        job_id=job_id,
        probes=[
            _definition(
                {
                    "probe_id": "coverage.accounts",
                    "lane": "coverage",
                    "file_scope": scoped_path,
                    "query": "update account payload",
                    "top_k": 3,
                }
            )
        ],
        trace_writer=_TraceWriter(),
    )

    assert bundles[0].candidate_chunks
    assert {chunk.file_path for chunk in bundles[0].candidate_chunks} == {scoped_path}


def test_related_expansion_adds_called_repository_function() -> None:
    probe = _definition(
        {
            "probe_id": "security.object_authorization",
            "category": "security",
            "query": "object id ownership repository",
            "top_k": 2,
        }
    )
    selected = [
        _candidate_chunk(
            file_path="backend/app/routes/records.py",
            content=(
                "async def read_record(record_id):\n"
                "    return await lookup_record(record_id)"
            ),
        )
    ]
    related_document = _chunk(
        job_id=uuid4(),
        file_path="backend/app/repositories/records.py",
        content=(
            "async def lookup_record(record_id):\n    return await db.get(record_id)"
        ),
        function_name="lookup_record",
    )

    expanded = _expand_related_candidates(
        selected=selected,
        chunk_documents=[related_document],
        probe=probe,
        top_k=2,
        excluded_keys=set(),
    )

    assert [chunk.file_path for chunk in expanded] == [
        "backend/app/routes/records.py",
        "backend/app/repositories/records.py",
    ]
    assert expanded[1].strategies == ("related",)


def test_fusion_reserves_two_candidates_per_primary_strategy() -> None:
    probe = _definition(
        {
            "probe_id": "security.object_authorization",
            "category": "security",
            "query": "object ownership",
            "top_k": 6,
        }
    )
    structural = [
        _candidate_chunk(
            file_path=f"app/structural_{index}.py",
            content="def inspect(): pass",
            strategies=("structural",),
        )
        for index in range(3)
    ]
    lexical = [
        _candidate_chunk(
            file_path=f"app/lexical_{index}.py",
            content="def inspect(): pass",
            strategies=("bm25",),
        )
        for index in range(3)
    ]
    semantic = [
        _candidate_chunk(
            file_path=f"app/semantic_{index}.py",
            content="def inspect(): pass",
            strategies=("semantic",),
        )
        for index in range(3)
    ]

    selected = _fuse_probe_candidates(
        semantic_candidates=semantic,
        bm25_candidates=lexical,
        exact_candidates=[],
        structural_candidates=structural,
        probe=probe,
        query=probe.primary_query,
        top_k=6,
    )

    assert {chunk.file_path for chunk in selected} == {
        "app/structural_0.py",
        "app/structural_1.py",
        "app/lexical_0.py",
        "app/lexical_1.py",
        "app/semantic_0.py",
        "app/semantic_1.py",
    }


def test_nested_parent_chunk_deduplication_keeps_narrow_evidence() -> None:
    parent = _candidate_chunk(
        file_path="app/service.py",
        chunk_index=0,
        content="\n".join(f"line_{index}" for index in range(20)),
        final_score=2.0,
    )
    function = _candidate_chunk(
        file_path="app/service.py",
        chunk_index=1,
        content="def operation():\n    return value",
        final_score=1.0,
    )

    unique = _unique_candidates([parent, function])

    assert [candidate.chunk_index for candidate in unique] == [1]


def test_auth_filename_stem_is_classified_as_security() -> None:
    assert _path_category("backend/app/auth.py") == "security"


@pytest.mark.asyncio
async def test_retrieval_applies_lane_order_caps_and_traces_after_trim() -> None:
    job_id = uuid4()
    file_path = "backend/app/routes.py"
    documents = []
    for index in range(4):
        document = _chunk(
            job_id=job_id,
            file_path=file_path,
            chunk_index=index,
            content=f"def handler_{index}():\n    return request_token_{index}",
        )
        document["line_start"] = index * 10 + 1
        document["line_end"] = index * 10 + 2
        documents.append(document)
    trace_writer = _TraceWriter()
    service = ProbeRetrievalService(
        database=_FakeDatabase(documents),  # type: ignore[arg-type]
        enable_semantic_search=False,
        defect_max_chunks=1,
        coverage_max_chunks=1,
        roadmap_max_chunks=1,
        max_chunks=3,
    )
    probes = [
        _definition(
            {
                "probe_id": "roadmap.boundary",
                "lane": "roadmap",
                "query": "request token boundary",
                "top_k": 3,
            }
        ),
        _definition(
            {
                "probe_id": "coverage.routes",
                "lane": "coverage",
                "file_scope": file_path,
                "query": "request token route behavior",
                "top_k": 3,
            }
        ),
        _definition(
            {
                "probe_id": "security.route_token",
                "lane": "defect",
                "category": "security",
                "priority": "high",
                "query": "request token route",
                "top_k": 3,
            }
        ),
    ]

    bundles = await service.retrieve(
        job_id=job_id,
        probes=probes,
        trace_writer=trace_writer,
    )

    assert [bundle.probe.lane for bundle in bundles] == [
        ProbeLane.DEFECT,
        ProbeLane.COVERAGE,
        ProbeLane.ROADMAP,
    ]
    assert [len(bundle.candidate_chunks) for bundle in bundles] == [1, 1, 1]
    assert bundles[0].candidate_chunks[0].key != bundles[1].candidate_chunks[0].key
    trace_inputs = [cast(dict[str, object], log["input"]) for log in trace_writer.logs]
    trace_outputs = [
        cast(dict[str, object], log["output"]) for log in trace_writer.logs
    ]
    assert [tool_input["lane"] for tool_input in trace_inputs] == [
        "defect",
        "coverage",
        "roadmap",
    ]
    assert all(output["sent_to_judge"] == 1 for output in trace_outputs)
    assert all(
        len(cast(list[object], output["results"])) == 1 for output in trace_outputs
    )
    assert trace_outputs[0]["trimmed_count"] == 2


def test_judge_batches_do_not_mix_lanes() -> None:
    bundles = [
        _bundle(
            probe={"probe_id": "defect.one", "lane": "defect"},
            chunks=[_candidate_chunk(file_path="app/a.py", content="def a(): pass")],
        ),
        _bundle(
            probe={"probe_id": "coverage.one", "lane": "coverage"},
            chunks=[_candidate_chunk(file_path="app/b.py", content="def b(): pass")],
        ),
        _bundle(
            probe={"probe_id": "roadmap.one", "lane": "roadmap"},
            chunks=[_candidate_chunk(file_path="app/c.py", content="def c(): pass")],
        ),
    ]

    batches = _judge_batches(bundles, max_probes=8, max_chunks=24)

    assert [[bundle.probe.lane for bundle in batch] for batch in batches] == [
        [ProbeLane.DEFECT],
        [ProbeLane.COVERAGE],
        [ProbeLane.ROADMAP],
    ]


def test_smart_lane_budget_reserves_at_most_nine_judge_batches() -> None:
    service = ProbeRetrievalService(
        database=_FakeDatabase([]),  # type: ignore[arg-type]
        defect_max_chunks=120,
        coverage_max_chunks=24,
        roadmap_max_chunks=44,
        max_probes_per_batch=8,
        max_chunks_per_batch=24,
    )

    assert (
        service._lane_chunk_cap(
            lane=ProbeLane.DEFECT,
            has_roadmap=True,
        )
        == 96
    )
    assert (
        service._lane_chunk_cap(
            lane=ProbeLane.COVERAGE,
            has_roadmap=True,
        )
        == 24
    )
    assert (
        service._lane_chunk_cap(
            lane=ProbeLane.ROADMAP,
            has_roadmap=True,
        )
        == 44
    )


@pytest.mark.asyncio
async def test_full_audit_adds_every_unscheduled_chunk() -> None:
    job_id = uuid4()
    documents = [
        _chunk(
            job_id=job_id,
            file_path=f"backend/app/module_{index}.py",
            content=f"def operation_{index}(): return value_{index}",
        )
        for index in range(4)
    ]
    service = ProbeRetrievalService(
        database=_FakeDatabase(documents),  # type: ignore[arg-type]
        enable_semantic_search=False,
        full_audit=True,
    )

    bundles = await service.retrieve(
        job_id=job_id,
        probes=[
            _definition(
                {
                    "probe_id": "bug.operation",
                    "category": "bug",
                    "query": "operation value",
                    "top_k": 1,
                }
            )
        ],
        trace_writer=_TraceWriter(),
    )

    sent_keys = {chunk.key for bundle in bundles for chunk in bundle.candidate_chunks}
    assert sent_keys == {(f"backend/app/module_{index}.py", 0) for index in range(4)}
    assert any(bundle.probe.reason == "full_audit" for bundle in bundles)


def _chunk(
    *,
    job_id: object,
    file_path: str,
    chunk_index: int = 0,
    content: str,
    function_name: str | None = None,
) -> dict[str, object]:
    return {
        "job_id": str(job_id),
        "file_path": file_path,
        "language": "python",
        "chunk_type": "function",
        "chunk_index": chunk_index,
        "total_chunks": 1,
        "function_name": function_name,
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


def _semantic_result(
    *,
    job_id: object,
    file_path: str,
    content: str,
    score: float,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        semantic_score=score,
        metadata={
            "job_id": str(job_id),
            "file_path": file_path,
            "language": "python",
            "chunk_index": 0,
            "line_start": 1,
            "line_end": max(1, len(content.splitlines())),
            "risk_area": "security" if "auth" in file_path else "general",
        },
    )


def _candidate_chunk(
    *,
    file_path: str,
    chunk_index: int = 0,
    content: str,
    strategies: tuple[str, ...] = (),
    final_score: float = 1.2,
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
        final_score=final_score,
        strategies=strategies,
    )


def _probe_candidate(
    *,
    title: str,
    description: str,
    file_path: str,
    line_start: int,
    line_end: int,
) -> ProbeJudgeIssueCandidate:
    response = ProbeJudgeResponse.model_validate(
        {
            "candidates": [
                {
                    "verdict": "issue",
                    "title": title,
                    "description": description,
                    "severity": "high",
                    "category": "requirement",
                    "confidence": 0.8,
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "supporting_evidence": [
                        {
                            "file_path": file_path,
                            "chunk_index": 0,
                            "line_start": line_start,
                            "line_end": line_end,
                        }
                    ],
                }
            ]
        }
    )
    return response.candidates[0]


def _bundle(
    *,
    probe: dict[str, object],
    chunks: list[ProbeCandidateChunk],
) -> ProbeEvidenceBundle:
    return ProbeEvidenceBundle(
        probe=_definition(probe),
        retrieval_status="ok",
        candidate_chunks=chunks,
        strategies_used=["exact"],
        strategy_candidate_counts={"exact": len(chunks)},
        selected_count_before_trim=len(chunks),
    )


def _related_rule_ids(item: object) -> list[str]:
    if not isinstance(item, ProbeDefinition):
        return []
    return list(item.related_rule_ids)


def _definition(value: dict[str, object]) -> ProbeDefinition:
    query = str(value.get("query") or "test source evidence")
    category = str(value.get("category") or "maintainability")
    raw_related_rule_ids = value.get("related_rule_ids", [])
    related_rule_ids = (
        raw_related_rule_ids if isinstance(raw_related_rule_ids, list) else []
    )
    raw_top_k = value.get("top_k")
    top_k = raw_top_k if isinstance(raw_top_k, int) else 3
    default_lane = "roadmap" if related_rule_ids else "defect"
    return ProbeDefinition(
        probe_id=str(value.get("probe_id") or "test.probe"),
        lane=ProbeLane(str(value.get("lane") or default_lane)),
        category=category,
        priority=str(value.get("priority") or "medium"),
        risk_area=category,
        retrieval_queries=(query,),
        lexical_terms=tuple(query.lower().split()),
        judge_question=query,
        top_k=top_k,
        file_scope=(
            str(value["file_scope"])
            if isinstance(value.get("file_scope"), str)
            else None
        ),
        related_rule_ids=tuple(str(item) for item in related_rule_ids),
    )
