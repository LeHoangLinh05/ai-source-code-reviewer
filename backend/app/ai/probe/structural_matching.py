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
from app.ai.probe.contracts import (
    OTP_SECURITY_PROBE_ID,
    SENSITIVE_DATA_LOGGING_PROBE_ID,
    UNRESTRICTED_FILE_UPLOAD_PROBE_ID,
    ProbeDefinition,
)
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
            function_bonus = _structural_shape_bonus(
                probe=probe,
                chunk_type=chunk_type,
                file_path=candidate.file_path,
                content=content,
            )
            candidates.append(
                replace(
                    candidate,
                    final_score=candidate.final_score + 0.5 + function_bonus,
                )
            )
    return sorted(candidates, key=_structural_candidate_rank, reverse=True)


def _structural_shape_bonus(
    *,
    probe: ProbeDefinition,
    chunk_type: str,
    file_path: str,
    content: str,
) -> float:
    normalized_path = file_path.replace("\\", "/").lower()
    if probe.probe_id == "security.sensitive_response_exposure":
        if chunk_type == "class" and "/schemas/" in normalized_path:
            return 0.8
        return 0.0
    if probe.probe_id == OTP_SECURITY_PROBE_ID:
        return _otp_candidate_bonus(content, chunk_type=chunk_type)
    return 0.4 if chunk_type == "function" else 0.0


def _otp_candidate_bonus(content: str, *, chunk_type: str) -> float:
    normalized = content.casefold()
    signal_groups = (
        ("@router.", '"/otp', "'/otp"),
        ("debug_otp", "return", "response", "logger"),
        ("depends(", "current_user", "authenticated", "authorize"),
        ("random.randint", "random.randrange", "secrets.", "token_urlsafe"),
        (
            "create_otp",
            "save_otp",
            "store_otp",
            "otprecord",
            "otp_code",
            "otp_hash",
            "hashed_otp",
        ),
        ("rate_limit", "attempt", "throttle", "lockout"),
        ("expire", "ttl", "delete", "used_at", "consumed"),
    )
    signal_bonus = sum(
        0.12 for terms in signal_groups if any(term in normalized for term in terms)
    )
    route_bonus = 0.4 if "@router." in normalized and "/otp" in normalized else 0.0
    function_bonus = 0.4 if chunk_type == "function" and signal_bonus else 0.0
    return route_bonus + function_bonus + min(signal_bonus, 0.6)


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
    has_authenticated_route = "@router." in content and "get_current_user" in content
    has_privileged_semantics = _contains_any(
        content,
        "admin",
        "cost_price",
        "margin",
        "profit",
        "report",
    )
    has_stronger_authorization = _contains_any(
        content,
        "require_admin",
        "require_role",
        "is_admin",
        "check_permission",
        "has_permission",
    )
    return (
        has_authenticated_route
        and has_privileged_semantics
        and not has_stronger_authorization
    )


def _matches_mass_assignment(content: str) -> bool:
    has_dynamic_assignment = "setattr(" in content and _contains_any(
        content,
        "payload",
        ".items()",
        "model_dump(",
        "dict",
    )
    has_sensitive_field = (
        re.search(
            r"\b(cost_price|is_admin|is_active|owner_id|role)\b",
            content,
        )
        is not None
    )
    defines_request_fields = "basemodel" in content or "model_dump(" in content
    return has_dynamic_assignment or (has_sensitive_field and defines_request_fields)


def _matches_jwt_algorithm_allowlist(content: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "jwt.decode(" in content and _contains_any(
            content,
            '"none"',
            "'none'",
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_jwt_decode_call(node):
            continue
        algorithms = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "algorithms"),
            None,
        )
        if algorithms is None:
            return True
        if _unsafe_jwt_algorithms(algorithms):
            return True
    return False


def _is_jwt_decode_call(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "decode"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "jwt"
    )


def _unsafe_jwt_algorithms(algorithms: ast.expr) -> bool:
    if not isinstance(algorithms, (ast.List, ast.Tuple, ast.Set)):
        return True

    values = algorithms.elts
    if len(values) != 1:
        return True
    value = values[0]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return True
    return not (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "settings"
        and value.attr == "algorithm"
    )


def _matches_sensitive_data_logging(content: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return bool(
            re.search(
                r"(?:logger|logging)\.(?:debug|info|warning|error|exception|critical)"
                r"\s*\([^\n]*(?:password|passwd|token|secret|api_key|cookie|"
                r"authorization|credential)",
                content,
            )
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_logging_call(node):
            continue
        values = [*node.args, *(keyword.value for keyword in node.keywords)]
        if any(_contains_sensitive_runtime_value(value) for value in values):
            return True
    return False


def _is_logging_call(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr.lower() not in {
        "critical",
        "debug",
        "error",
        "exception",
        "info",
        "log",
        "warning",
    }:
        return False
    try:
        owner = ast.unparse(call.func.value).lower()
    except ValueError:
        return False
    return "log" in owner


def _contains_sensitive_runtime_value(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Constant):
        return False

    sensitive_terms = {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "passwd",
        "password",
        "secret",
        "token",
    }
    for node in ast.walk(expression):
        if isinstance(node, ast.Name) and _identifier_has_term(
            node.id,
            sensitive_terms,
        ):
            return True
        if isinstance(node, ast.Attribute) and _identifier_has_term(
            node.attr,
            sensitive_terms,
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and _identifier_has_term(
                node.slice.value,
                sensitive_terms,
            )
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            key = node.args[0]
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _identifier_has_term(key.value, sensitive_terms)
            ):
                return True
    return False


def _identifier_has_term(identifier: str, terms: set[str]) -> bool:
    normalized = identifier.lower()
    return any(term in normalized for term in terms)


def _matches_file_upload_flow(content: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        has_upload = _contains_any(content, "uploadfile", "multipart", "filename")
        has_sink = _contains_any(
            content,
            ".write(",
            "copyfileobj(",
            "shutil.copy(",
            "shutil.copy2(",
        )
        return has_upload and has_sink

    has_upload = any(
        isinstance(node, ast.Name) and node.id.lower() == "uploadfile"
        for node in ast.walk(tree)
    )
    if not has_upload:
        return False
    return any(
        isinstance(node, ast.Call) and _is_file_write_sink(node)
        for node in ast.walk(tree)
    )


def _is_file_write_sink(call: ast.Call) -> bool:
    try:
        call_name = ast.unparse(call.func).lower()
    except ValueError:
        return False
    return call_name.endswith(".write") or call_name.endswith(
        ("copyfileobj", "shutil.copy", "shutil.copy2")
    )


def _matches_sensitive_response(content: str) -> bool:
    has_sensitive_field = _contains_any(
        content,
        "cost_price",
        "hashed_password",
        "private_key",
        "profit",
        "margin",
    )
    defines_response_schema = (
        has_sensitive_field
        and "basemodel" in content
        and re.search(r"class\s+[a-z0-9_]*response\b", content) is not None
    )
    return defines_response_schema


def _matches_insecure_randomness(content: str) -> bool:
    has_security_value = _contains_any(
        content,
        "token",
        "secret",
        "password",
        "credential",
        "nonce",
    )
    has_predictable_randomness = _contains_any(
        content,
        "random.seed(",
        "random.choice(",
        "random.randint(",
        "random.random(",
    )
    return has_security_value and has_predictable_randomness


def _matches_open_redirect(content: str) -> bool:
    has_redirect_sink = "redirectresponse(" in content and "url=" in content
    has_request_target = _contains_any(content, "next", "redirect", "return_url")
    if not has_redirect_sink or not has_request_target:
        return False
    has_relative_path_allowlist = (
        "startswith(" in content
        and "//" in content
        and _contains_any(content, "\\\\", "backslash")
    )
    return not has_relative_path_allowlist


def _matches_inventory_invariant(content: str) -> bool:
    has_quantity_mutation = (
        re.search(r"\bquantity\s*\+=\s*[a-z_]", content) is not None
        or re.search(
            r"\bquantity\s*=\s*[a-z0-9_.]+\.quantity\s*\+\s*[a-z_]",
            content,
        )
        is not None
    )
    if not has_quantity_mutation or "delta" not in content:
        return False
    has_non_negative_guard = re.search(
        r"(?:quantity\s*\+\s*delta|new_quantity)\s*<\s*0",
        content,
    ) is not None or _contains_any(
        content,
        "checkconstraint",
        "quantity >= 0",
        "quantity>=0",
        "max(0",
    )
    return not has_non_negative_guard


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


def _matches_otp_flow(content: str) -> bool:
    return _contains_any(
        content,
        "otp",
        "verification_code",
        "one_time_password",
        "one_time",
    )


def _matches_insecure_default_credentials(content: str) -> bool:
    has_credential = _contains_any(content, "password", "secret", "api_key", "token")
    has_fallback = _contains_any(content, "getenv(", "field(default=", ' or "')
    return has_credential and has_fallback


def _matches_task_reliability(content: str) -> bool:
    has_task = _contains_any(content, "@shared_task", "@celery_app.task", "@app.task")
    has_reliability_policy = _contains_any(
        content,
        "autoretry_for",
        "time_limit",
        "soft_time_limit",
        "self.retry(",
    )
    return has_task and not has_reliability_policy


def _matches_idempotency_race(content: str) -> bool:
    has_idempotency_flow = _contains_any(content, "idempotency", "webhook")
    has_check = _contains_any(content, "exists(", "get_by_")
    has_write = _contains_any(content, "create(", "insert(", "add(")
    has_check_then_write = has_check and has_write
    return has_idempotency_flow and has_check_then_write


_STRUCTURAL_MATCHERS: dict[str, Any] = {
    "security.sql_nosql_injection": _matches_sql_injection,
    "security.command_injection": _matches_command_injection,
    "security.ssrf_external_calls": _matches_ssrf,
    "security.object_authorization": _matches_object_authorization,
    "security.role_authorization": _matches_role_authorization,
    "security.mass_assignment": _matches_mass_assignment,
    "security.jwt_algorithm_allowlist": _matches_jwt_algorithm_allowlist,
    "security.insecure_randomness": _matches_insecure_randomness,
    "security.open_redirect": _matches_open_redirect,
    SENSITIVE_DATA_LOGGING_PROBE_ID: _matches_sensitive_data_logging,
    "security.sensitive_response_exposure": _matches_sensitive_response,
    UNRESTRICTED_FILE_UPLOAD_PROBE_ID: _matches_file_upload_flow,
    "security.weak_password_hash": _matches_weak_hash,
    "security.reset_token_lifecycle": _matches_reset_token,
    "security.refresh_token_validation": _matches_refresh_validation,
    "security.logout_revocation": _matches_logout,
    OTP_SECURITY_PROBE_ID: _matches_otp_flow,
    "security.insecure_default_credentials": _matches_insecure_default_credentials,
    "bug.async_concurrency": _matches_race,
    "bug.inventory_invariant": _matches_inventory_invariant,
    "bug.state_transaction_consistency": _matches_transaction,
    "bug.task_retry_timeout": _matches_task_reliability,
    "bug.idempotency_race": _matches_idempotency_race,
    "performance.n_plus_one": _matches_n_plus_one,
    "performance.pagination_bounds": _matches_pagination,
    "maintainability.resource_lifecycle": _matches_resource_lifecycle,
}
