"""Chunk selection policy for AI review coverage."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

REVIEW_MODE_SMART = "smart"
REVIEW_MODE_FULL_AUDIT = "full_audit"
VALID_REVIEW_MODES = {REVIEW_MODE_SMART, REVIEW_MODE_FULL_AUDIT}
DEFAULT_SMART_REVIEW_MAX_CHUNKS = 180
SMART_MIN_BREADTH_FILES = 12
SMART_ROADMAP_CONTEXT_CHUNKS_PER_ITEM = 4

SMART_RISK_AREAS = {"security", "api", "database", "config"}
SMART_PATH_PARTS = {
    "agent",
    "agents",
    "ai",
    "api",
    "auth",
    "chat",
    "crypto",
    "db",
    "deps",
    "endpoints",
    "llm",
    "memory",
    "middleware",
    "rag",
    "repositories",
    "routers",
    "security",
}
SMART_CONFIG_FILENAMES = {
    ".env.example",
    ".gitignore",
    "docker-compose.yaml",
    "docker-compose.yml",
    "dockerfile",
    "dockerfile.dev",
    "dockerfile.prod",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "tsconfig.json",
}
ROADMAP_VERIFICATION_TERMS_BY_RULE_ID = {
    "RC-W1-10": {"login", "password", "verify", "refresh_token", "access_token", "jwt"},
    "RC-W1-11": {"refresh", "refresh_token", "revoke", "token", "jwt", "expiry"},
    "RC-W1-13": {"logout", "blacklist", "revoke", "refresh_token", "token"},
    "RC-W1-17": {"register", "password", "hash", "bcrypt", "passlib", "argon2"},
    "RC-W3-06": {"cache", "ttl", "expire", "setex", "products", "redis"},
    "RC-W3-07": {"cache", "delete", "invalidate", "product", "redis"},
    "RC-W3-09": {"sync", "celery", "beat", "scheduler", "cron", "mysql", "mongo"},
    "RC-W4-05": {"middleware", "guard", "protected", "login", "redirect", "auth"},
    "RC-W5-01": {"websocket", "sse", "broadcast", "client", "connection"},
    "RC-W5-02": {"connectionmanager", "connections", "clients", "room", "manager"},
    "RC-W5-03": {"websocket", "token", "jwt", "connect", "close", "auth"},
    "RC-W5-04": {"publish", "subscribe", "redis", "channel", "broadcast"},
    "RC-W6-02": {"memory", "conversation", "buffer", "summary", "session"},
    "RC-W6-04": {"reset", "clear", "session", "context", "memory"},
    "RC-W7-04": {"retrieve", "retriever", "rag", "llm", "prompt", "context"},
    "RC-W7-06": {"memory", "rag", "retriever", "context", "conversation"},
}
ROADMAP_VERIFICATION_STOP_WORDS = {
    "true",
    "false",
    "khong",
    "không",
    "nhung",
    "nhưng",
    "that",
    "thật",
    "duoc",
    "được",
    "trong",
    "khong",
    "không",
    "phai",
    "phải",
}


@dataclass(frozen=True, slots=True)
class ChunkInfo:
    """Minimal chunk metadata needed to build a review plan."""

    file_path: str
    chunk_index: int
    total_chunks: int
    risk_area: str
    language: str
    module: str
    chunk_type: str
    function_name: str
    class_name: str
    has_static_issues: bool
    token_count: int
    search_text: str

    @property
    def key(self) -> tuple[str, int]:
        return self.file_path, self.chunk_index


@dataclass(frozen=True, slots=True)
class RoadmapVerification:
    """One roadmap rule that needs AI correctness verification."""

    rule_id: str
    file_path: str
    ai_hint: str


def get_review_mode(options: dict[str, object] | None) -> str:
    """Return the AI review mode, defaulting to targeted smart review."""

    if not options:
        return REVIEW_MODE_SMART

    value = options.get("review_mode", REVIEW_MODE_SMART)
    if not isinstance(value, str):
        raise ValueError("review_mode must be a string")

    review_mode = value.strip().lower()
    if review_mode not in VALID_REVIEW_MODES:
        raise ValueError(
            f"review_mode must be one of: {', '.join(sorted(VALID_REVIEW_MODES))}"
        )

    return review_mode


def get_smart_review_max_chunks(options: dict[str, object] | None) -> int:
    """Return the soft cap for smart-mode chunk selection."""

    if not options:
        return DEFAULT_SMART_REVIEW_MAX_CHUNKS

    value = options.get("smart_review_max_chunks")
    if not isinstance(value, int):
        return DEFAULT_SMART_REVIEW_MAX_CHUNKS

    return max(20, min(500, value))


def build_chunk_review_plan(
    *,
    chunk_documents: Sequence[object],
    review_mode: str,
    verification_queue: Sequence[dict[str, object]],
    max_smart_chunks: int = DEFAULT_SMART_REVIEW_MAX_CHUNKS,
) -> dict[str, object]:
    """Build the chunk checklist the AI agent must complete."""

    chunks = _chunk_infos(chunk_documents)
    verification_items = _verification_items(verification_queue)
    if review_mode == REVIEW_MODE_FULL_AUDIT:
        selected_reasons = {chunk.key: "full_audit" for chunk in chunks}
    else:
        selected_reasons = _select_smart_chunks(
            chunks=chunks,
            verification_items=verification_items,
            max_chunks=max_smart_chunks,
        )

    return _build_plan_payload(
        chunks=chunks,
        review_mode=review_mode,
        selected_reasons=selected_reasons,
        verification_items=verification_items,
    )


def expected_chunk_keys_from_plan(plan: dict[str, object]) -> set[tuple[str, int]]:
    """Extract required chunk keys from a serialized review plan."""

    expected: set[tuple[str, int]] = set()
    files = plan.get("files", [])
    if not isinstance(files, list):
        return expected

    for file_plan in files:
        if not isinstance(file_plan, dict):
            continue
        file_path = file_plan.get("file_path")
        required_indexes = file_plan.get("required_chunk_indexes", [])
        if not isinstance(file_path, str) or not isinstance(required_indexes, list):
            continue
        for chunk_index in required_indexes:
            if isinstance(chunk_index, int):
                expected.add((file_path, chunk_index))

    return expected


def all_chunk_keys(chunk_documents: Sequence[object]) -> set[tuple[str, int]]:
    """Return all persisted chunk keys for full repository coverage."""

    return {chunk.key for chunk in _chunk_infos(chunk_documents)}


def _chunk_infos(chunk_documents: Sequence[object]) -> list[ChunkInfo]:
    chunks: list[ChunkInfo] = []
    for document in chunk_documents:
        if not isinstance(document, dict):
            continue

        file_path = document.get("file_path")
        chunk_index = document.get("chunk_index")
        total_chunks = document.get("total_chunks")
        if not isinstance(file_path, str) or not isinstance(chunk_index, int):
            continue

        raw_token_count = document.get("token_count")
        chunk_text = str(document.get("chunk_text") or "")
        function_name = str(document.get("function_name") or "")
        class_name = str(document.get("class_name") or "")

        chunks.append(
            ChunkInfo(
                file_path=file_path,
                chunk_index=chunk_index,
                total_chunks=total_chunks if isinstance(total_chunks, int) else 1,
                risk_area=str(document.get("risk_area") or "general"),
                language=str(document.get("language") or "unknown"),
                module=str(document.get("module") or ""),
                chunk_type=str(document.get("chunk_type") or ""),
                function_name=function_name,
                class_name=class_name,
                has_static_issues=document.get("has_static_issues") is True,
                token_count=raw_token_count if isinstance(raw_token_count, int) else 0,
                search_text=" ".join(
                    [
                        file_path,
                        str(document.get("module") or ""),
                        function_name,
                        class_name,
                        chunk_text,
                    ]
                ).lower(),
            )
        )

    return sorted(chunks, key=lambda chunk: (chunk.file_path, chunk.chunk_index))


def _verification_items(
    queue: Sequence[dict[str, object]],
) -> list[RoadmapVerification]:
    items: list[RoadmapVerification] = []
    for item in queue:
        rule_id = item.get("rule_id")
        file_path = item.get("file_path")
        ai_hint = item.get("ai_hint")
        if not isinstance(rule_id, str) or not rule_id:
            continue
        items.append(
            RoadmapVerification(
                rule_id=rule_id,
                file_path=file_path if isinstance(file_path, str) else "",
                ai_hint=ai_hint if isinstance(ai_hint, str) else "",
            )
        )

    return items


def _select_smart_chunks(
    *,
    chunks: list[ChunkInfo],
    verification_items: list[RoadmapVerification],
    max_chunks: int,
) -> dict[tuple[str, int], str]:
    hard_required: dict[tuple[str, int], str] = {}
    soft_candidates: list[tuple[int, ChunkInfo, str]] = []
    chunks_by_file: dict[str, list[ChunkInfo]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_file[chunk.file_path].append(chunk)

    for item in verification_items:
        for chunk in chunks_by_file.get(item.file_path, []):
            hard_required[chunk.key] = "roadmap_verification"

    for chunk in _roadmap_context_chunks(
        chunks=chunks,
        verification_items=verification_items,
        direct_keys=set(hard_required),
    ):
        hard_required.setdefault(chunk.key, "roadmap_context")

    for chunk in chunks:
        reason = _smart_selection_reason(chunk)
        if reason is None:
            continue
        if reason == "static_issue":
            hard_required[chunk.key] = reason
            continue
        soft_candidates.append((_selection_rank(reason), chunk, reason))

    selected = dict(hard_required)
    remaining_budget = max(0, max_chunks - len(selected))
    for _, chunk, reason in sorted(
        soft_candidates,
        key=lambda item: (item[0], item[1].file_path, item[1].chunk_index),
    )[:remaining_budget]:
        selected.setdefault(chunk.key, reason)

    _add_breadth_fallback(
        selected=selected,
        chunks=chunks,
        max_chunks=max_chunks,
    )
    return selected


def _smart_selection_reason(
    chunk: ChunkInfo,
) -> str | None:
    if chunk.has_static_issues:
        return "static_issue"
    if chunk.risk_area in SMART_RISK_AREAS:
        return f"risk_area:{chunk.risk_area}"
    if _path_parts(chunk.file_path) & SMART_PATH_PARTS:
        return "risk_path"
    if _is_config_file(chunk.file_path):
        return "project_config"

    return None


def _add_breadth_fallback(
    *,
    selected: dict[tuple[str, int], str],
    chunks: list[ChunkInfo],
    max_chunks: int,
) -> None:
    selected_files = {file_path for file_path, _chunk_index in selected}
    if len(selected_files) >= SMART_MIN_BREADTH_FILES or len(selected) >= max_chunks:
        return

    first_chunks_by_file: dict[str, ChunkInfo] = {}
    for chunk in chunks:
        if chunk.chunk_index == 0:
            first_chunks_by_file.setdefault(chunk.file_path, chunk)

    for chunk in sorted(
        first_chunks_by_file.values(),
        key=lambda item: (_fallback_rank(item), item.file_path),
    ):
        if len(selected) >= max_chunks:
            break
        if (
            len({file_path for file_path, _chunk_index in selected})
            >= SMART_MIN_BREADTH_FILES
        ):
            break
        selected.setdefault(chunk.key, "breadth_sample")


def _build_plan_payload(
    *,
    chunks: list[ChunkInfo],
    review_mode: str,
    selected_reasons: dict[tuple[str, int], str],
    verification_items: list[RoadmapVerification],
) -> dict[str, object]:
    chunks_by_file: dict[str, list[ChunkInfo]] = defaultdict(list)
    selected_by_file: dict[str, list[ChunkInfo]] = defaultdict(list)
    reasons_by_file: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        chunks_by_file[chunk.file_path].append(chunk)
        reason = selected_reasons.get(chunk.key)
        if reason is None:
            continue
        selected_by_file[chunk.file_path].append(chunk)
        reasons_by_file[chunk.file_path].add(reason)

    files: list[dict[str, object]] = []
    target_chunk_count = 0
    for file_path, selected_chunks in sorted(selected_by_file.items()):
        all_file_chunks = chunks_by_file[file_path]
        required_chunk_indexes = [
            chunk.chunk_index
            for chunk in sorted(
                selected_chunks,
                key=lambda item: item.chunk_index,
            )
        ]
        target_chunk_count += len(required_chunk_indexes)
        files.append(
            {
                "file_path": file_path,
                "total_chunks": max(chunk.total_chunks for chunk in all_file_chunks),
                "required_chunk_indexes": required_chunk_indexes,
                "selection_reasons": sorted(reasons_by_file[file_path]),
                "roadmap_verifications": _roadmap_verifications_for_file(
                    file_path=file_path,
                    selected_chunks=selected_chunks,
                    selected_reasons=selected_reasons,
                    verification_items=verification_items,
                ),
                "risk_area": selected_chunks[0].risk_area,
                "language": selected_chunks[0].language,
            }
        )

    return {
        "mode": review_mode,
        "total_chunked_files": len(chunks_by_file),
        "total_available_chunks": len(chunks),
        "roadmap_verification_items": len(verification_items),
        "target_files": len(files),
        "target_chunks": target_chunk_count,
        "files": files,
    }


def _selection_rank(reason: str) -> int:
    if reason.startswith("risk_area:security"):
        return 0
    if reason == "risk_path":
        return 1
    if reason.startswith("risk_area:api"):
        return 2
    if reason.startswith("risk_area:database"):
        return 3
    if reason.startswith("risk_area:config"):
        return 4
    if reason == "project_config":
        return 5
    return 10


def _fallback_rank(chunk: ChunkInfo) -> int:
    if chunk.risk_area in SMART_RISK_AREAS:
        return 0
    if _path_parts(chunk.file_path) & SMART_PATH_PARTS:
        return 1
    if _is_config_file(chunk.file_path):
        return 2
    return 3


def _roadmap_context_chunks(
    *,
    chunks: list[ChunkInfo],
    verification_items: list[RoadmapVerification],
    direct_keys: set[tuple[str, int]],
) -> list[ChunkInfo]:
    selected: dict[tuple[str, int], ChunkInfo] = {}
    for item in verification_items:
        terms = _verification_terms(item)
        if not terms:
            continue

        candidates: list[tuple[int, ChunkInfo]] = []
        for chunk in chunks:
            if chunk.key in direct_keys or chunk.file_path == item.file_path:
                continue
            score = _verification_context_score(chunk, terms)
            if score > 0:
                candidates.append((score, chunk))

        for _score, chunk in sorted(
            candidates,
            key=lambda item: (-item[0], item[1].file_path, item[1].chunk_index),
        )[:SMART_ROADMAP_CONTEXT_CHUNKS_PER_ITEM]:
            selected.setdefault(chunk.key, chunk)

    return sorted(
        selected.values(), key=lambda chunk: (chunk.file_path, chunk.chunk_index)
    )


def _roadmap_verifications_for_file(
    *,
    file_path: str,
    selected_chunks: list[ChunkInfo],
    selected_reasons: dict[tuple[str, int], str],
    verification_items: list[RoadmapVerification],
) -> list[dict[str, object]]:
    verifications: list[dict[str, object]] = []
    has_context_chunk = any(
        selected_reasons.get(chunk.key) == "roadmap_context"
        for chunk in selected_chunks
    )
    for item in verification_items:
        if item.file_path == file_path:
            verifications.append(
                {
                    "rule_id": item.rule_id,
                    "ai_hint": item.ai_hint,
                    "match_type": "direct",
                }
            )
            continue
        if not has_context_chunk:
            continue
        terms = _verification_terms(item)
        if any(
            _verification_context_score(chunk, terms) > 0 for chunk in selected_chunks
        ):
            verifications.append(
                {
                    "rule_id": item.rule_id,
                    "ai_hint": item.ai_hint,
                    "match_type": "related_context",
                }
            )

    return verifications


def _verification_context_score(chunk: ChunkInfo, terms: set[str]) -> int:
    if not terms:
        return 0

    path_text = " ".join(
        [
            chunk.file_path,
            chunk.module,
            chunk.function_name,
            chunk.class_name,
            chunk.risk_area,
        ]
    ).lower()
    score = 0
    for term in terms:
        normalized_term = term.lower()
        if normalized_term in path_text:
            score += 8
        elif normalized_term in chunk.search_text:
            score += 3

    if chunk.risk_area in SMART_RISK_AREAS:
        score += 1
    return score


def _verification_terms(item: RoadmapVerification) -> set[str]:
    terms = set(ROADMAP_VERIFICATION_TERMS_BY_RULE_ID.get(item.rule_id, set()))
    terms.update(_tokenize_verification_text(item.ai_hint))
    terms.update(_tokenize_verification_text(item.file_path))
    return {
        term.lower()
        for term in terms
        if len(term) >= 4 and term.lower() not in ROADMAP_VERIFICATION_STOP_WORDS
    }


def _tokenize_verification_text(value: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+", value.lower()))


def _path_parts(file_path: str) -> set[str]:
    return {part.lower() for part in PurePosixPath(file_path.replace("\\", "/")).parts}


def _is_config_file(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lower()
    filename = PurePosixPath(normalized).name
    return filename in SMART_CONFIG_FILENAMES or normalized.endswith("/dockerfile")
