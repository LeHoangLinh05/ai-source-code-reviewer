"""Structural and file-scoped candidate matching for probe retrieval."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from typing import Any

from app.ai.probe.candidate_retrieval import (
    _candidate_from_document,
    _exact_score,
)
from app.ai.probe.contracts import ProbeDefinition
from app.ai.probe.models import ProbeCandidateChunk


def _structural_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
) -> list[ProbeCandidateChunk]:
    matcher = _STRUCTURAL_MATCHERS.get(probe.probe_id)
    if matcher is None:
        return []

    candidates: list[ProbeCandidateChunk] = []
    for document in chunk_documents:
        if str(document.get("language") or "").lower() != "python":
            continue
        content = document.get("chunk_text")
        if not isinstance(content, str) or not matcher(content.lower()):
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=probe.primary_query,
            semantic_score=0.0,
            lexical_score=1.0,
            strategy="structural",
        )
        if candidate is not None:
            chunk_type = str(document.get("chunk_type") or "").lower()
            function_bonus = 0.4 if chunk_type == "function" else 0.0
            candidates.append(
                replace(
                    candidate,
                    final_score=candidate.final_score + 0.5 + function_bonus,
                )
            )
    return sorted(candidates, key=_structural_candidate_rank, reverse=True)


def _structural_candidate_rank(
    candidate: ProbeCandidateChunk,
) -> tuple[float, int, str, int]:
    line_span = candidate.line_end - candidate.line_start
    return (
        candidate.final_score,
        -line_span,
        candidate.file_path,
        -candidate.chunk_index,
    )


def _file_scope_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
) -> list[ProbeCandidateChunk]:
    if probe.file_scope is None:
        return []

    candidates: list[ProbeCandidateChunk] = []
    for document in chunk_documents:
        if document.get("file_path") != probe.file_scope:
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=probe.primary_query,
            semantic_score=0.0,
            lexical_score=_exact_score(
                probe.primary_query,
                str(document.get("chunk_text") or ""),
                probe.file_scope,
            ),
            strategy="file_scope",
        )
        if candidate is not None:
            candidates.append(
                replace(candidate, final_score=candidate.final_score + 0.4)
            )
    return sorted(candidates, key=_file_scope_candidate_rank, reverse=True)


def _file_scope_candidate_rank(
    candidate: ProbeCandidateChunk,
) -> tuple[int, float, int]:
    is_function = int(candidate.line_end > candidate.line_start)
    return is_function, candidate.final_score, -candidate.chunk_index


def _candidates_in_file(
    candidates: list[ProbeCandidateChunk],
    file_path: str,
) -> list[ProbeCandidateChunk]:
    return [candidate for candidate in candidates if candidate.file_path == file_path]


def _exclude_candidates(
    candidates: list[ProbeCandidateChunk],
    excluded_keys: set[tuple[str, int]],
) -> list[ProbeCandidateChunk]:
    if not excluded_keys:
        return candidates
    return [candidate for candidate in candidates if candidate.key not in excluded_keys]


def _contains_all(content: str, *terms: str) -> bool:
    return all(term in content for term in terms)


def _contains_any(content: str, *terms: str) -> bool:
    return any(term in content for term in terms)


def _matches_sql_injection(content: str) -> bool:
    has_sink = _contains_any(content, "execute(", "from_statement(", "raw(")
    has_construction = _contains_any(content, 'f"', "f'", ".format(", " + ")
    return has_sink and has_construction


def _matches_command_injection(content: str) -> bool:
    return _contains_any(content, "subprocess", "os.system", "popen(") and (
        "shell=true" in content or _contains_any(content, 'f"', "f'", ".format(")
    )


def _matches_ssrf(content: str) -> bool:
    has_client = _contains_any(
        content,
        "httpx",
        "requests.",
        "client.get(",
        "axios",
        "fetch(",
    )
    return has_client and _contains_any(content, "url", "uri", "webhook")


def _matches_object_authorization(content: str) -> bool:
    return _contains_all(content, "current_user", "get_by_id") and _contains_any(
        content,
        "_id",
        "id:",
    )


def _matches_role_authorization(content: str) -> bool:
    return "admin" in content and "get_current_user" in content


def _matches_mass_assignment(content: str) -> bool:
    return "setattr(" in content and _contains_any(
        content, "payload", ".items()", "dict"
    )


def _matches_weak_hash(content: str) -> bool:
    return "password" in content and _contains_any(content, "md5(", "sha1(")


def _matches_reset_token(content: str) -> bool:
    return (
        "reset" in content
        and "token" in content
        and _contains_any(
            content,
            "redis.set(",
            ".set(",
            "setex(",
        )
    )


def _matches_refresh_validation(content: str) -> bool:
    return "refresh" in content and _contains_any(content, "jwt", "decode(", "token")


def _matches_logout(content: str) -> bool:
    return "logout" in content and _contains_any(
        content, "revoke", "blacklist", "success"
    )


def _matches_race(content: str) -> bool:
    has_resource = _contains_any(content, "stock", "inventory", "quantity")
    has_read = _contains_any(content, "get_by_", "select(")
    has_write = "update_" in content
    has_subtraction_assignment = (
        re.search(
            r"=\s*[a-z_][a-z0-9_.]*\s+-\s+[a-z_]",
            content,
        )
        is not None
    )
    return has_resource and has_read and has_write and has_subtraction_assignment


def _matches_transaction(content: str) -> bool:
    return "except" in content and _contains_any(
        content, "commit(", "update_", "create_"
    )


def _matches_n_plus_one(content: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Call) and _is_io_call(child):
                    return True
    return False


def _is_io_call(call: ast.Call) -> bool:
    try:
        call_name = ast.unparse(call.func).lower()
    except ValueError:
        return False
    sink_names = (
        ".execute",
        ".query",
        ".fetch",
        ".find",
        ".get_by_",
        "repository.",
        "repo.",
        "client.",
    )
    return any(sink in call_name for sink in sink_names)


def _matches_pagination(content: str) -> bool:
    loads_all = _contains_any(content, ".all()", "scalars().all", "list(")
    in_memory = _contains_any(content, "filtered_", "[p for ", "[item for ", "offset :")
    return loads_all and in_memory


def _matches_resource_lifecycle(content: str) -> bool:
    return _contains_any(
        content, "create_engine(", "create_async_engine("
    ) and _contains_any(
        content,
        "def ",
        "async def ",
    )


_STRUCTURAL_MATCHERS: dict[str, Any] = {
    "security.sql_nosql_injection": _matches_sql_injection,
    "security.command_injection": _matches_command_injection,
    "security.ssrf_external_calls": _matches_ssrf,
    "security.object_authorization": _matches_object_authorization,
    "security.role_authorization": _matches_role_authorization,
    "security.mass_assignment": _matches_mass_assignment,
    "security.weak_password_hash": _matches_weak_hash,
    "security.reset_token_lifecycle": _matches_reset_token,
    "security.refresh_token_validation": _matches_refresh_validation,
    "security.logout_revocation": _matches_logout,
    "bug.async_concurrency": _matches_race,
    "bug.state_transaction_consistency": _matches_transaction,
    "performance.n_plus_one": _matches_n_plus_one,
    "performance.pagination_bounds": _matches_pagination,
    "maintainability.resource_lifecycle": _matches_resource_lifecycle,
}
