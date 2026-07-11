"""Tests for AST-based Python code chunking."""

from app.analyzers.code_chunker import (
    MAX_TOKENS_PER_CHUNK,
    StaticIssueRange,
    chunk_python_source,
    detect_risk_area,
)


def test_chunk_python_source_splits_classes_functions_and_metadata() -> None:
    source = """
import jwt
from sqlalchemy import select

class TokenService:
    def create_access_token(self):
        return "token"

    def verify_token(self):
        return True

class UserService:
    def create_user(self):
        return None

def hash_password(password):
    return password

async def login_user(email):
    return email
""".strip()

    chunks = chunk_python_source(
        source,
        file_path="app/auth/utils.py",
        static_issues=[
            StaticIssueRange(
                file_path="app/auth/utils.py",
                line_start=6,
                line_end=6,
            )
        ],
    )

    function_chunks = [
        chunk for chunk in chunks if chunk.metadata.chunk_type == "function"
    ]
    class_chunks = [chunk for chunk in chunks if chunk.metadata.chunk_type == "class"]

    assert len(chunks) >= 7
    assert len(class_chunks) == 2
    assert len(function_chunks) == 5
    assert {chunk.metadata.function_name for chunk in function_chunks} == {
        "create_access_token",
        "verify_token",
        "create_user",
        "hash_password",
        "login_user",
    }
    assert {chunk.metadata.class_name for chunk in class_chunks} == {
        "TokenService",
        "UserService",
    }
    assert all(chunk.metadata.file_path == "app/auth/utils.py" for chunk in chunks)
    assert all(chunk.metadata.language == "python" for chunk in chunks)
    assert all(chunk.metadata.module == "auth" for chunk in chunks)
    assert all(chunk.metadata.risk_area == "security" for chunk in chunks)
    assert all("jwt" in chunk.metadata.imports for chunk in chunks)
    assert all(chunk.metadata.total_chunks == len(chunks) for chunk in chunks)
    assert all(chunk.metadata.token_count <= MAX_TOKENS_PER_CHUNK for chunk in chunks)
    assert any(chunk.metadata.has_static_issues for chunk in function_chunks)


def test_chunk_python_source_falls_back_to_fixed_line_windows() -> None:
    source = "\n".join(f"broken line {line_number}" for line_number in range(1, 72))

    chunks = chunk_python_source(source, file_path="app/services/broken.py")

    assert [chunk.metadata.chunk_type for chunk in chunks] == ["module", "module"]
    assert chunks[0].metadata.line_start == 1
    assert chunks[0].metadata.line_end == 60
    assert chunks[1].metadata.line_start == 51
    assert chunks[1].metadata.line_end == 71


def test_chunk_python_source_skips_blank_module_gaps() -> None:
    source = """\
def first():
    return 1


def second():
    return 2
"""

    chunks = chunk_python_source(source, file_path="app/services/example.py")

    assert len(chunks) == 2
    assert all(chunk.content.strip() for chunk in chunks)
    assert [chunk.metadata.function_name for chunk in chunks] == ["first", "second"]


def test_detect_risk_area_uses_documented_heuristics() -> None:
    assert detect_risk_area("app/config.py", []) == "config"
    assert detect_risk_area("app/repositories/user.py", []) == "database"
    assert detect_risk_area("app/routers/users.py", []) == "api"
    assert detect_risk_area("app/services/token.py", ["secrets"]) == "security"
    assert detect_risk_area("app/services/report.py", []) == "general"
