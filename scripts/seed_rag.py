"""Seed the coding standards RAG knowledge base."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.rag.ingestion import RAGDocument, RAGIngestionPipeline  # noqa: E402
from app.ai.rag.retriever import HybridRetriever  # noqa: E402


OWASP_TOP_10_2021 = """
# OWASP Top 10 2021

OWASP A01:2021 Broken Access Control: enforce authorization checks on every
request, deny by default, and avoid relying only on hidden UI controls.

OWASP A02:2021 Cryptographic Failures: protect sensitive data in transit and at
rest, use modern hashing for passwords, and avoid weak or custom cryptography.

OWASP A03:2021 Injection: SQL injection happens when untrusted input is
concatenated into SQL, NoSQL, OS commands, or interpreters. In Python, avoid
f-strings, string concatenation, or %-formatting to build SQL statements with
user input. Use SQLAlchemy bind parameters, ORM filters, or parameterized
queries. Validate input and use least-privilege database accounts.

OWASP A04:2021 Insecure Design: model threats early, document trust boundaries,
and design controls before implementation.

OWASP A05:2021 Security Misconfiguration: run with secure defaults, disable
debug mode in production, and restrict CORS origins.

OWASP A07:2021 Identification and Authentication Failures: validate JWT
signatures and expiry, rotate refresh tokens, hash passwords with bcrypt,
argon2, or passlib, and do not hardcode token payloads.
"""

PYTHON_BEST_PRACTICES = """
# Python Best Practices: PEP 8 and PEP 20

Write readable Python with descriptive names, small functions, clear imports,
and explicit error handling. PEP 8 recommends consistent formatting, import
ordering, line length discipline, and naming conventions. PEP 20 emphasizes
that explicit is better than implicit, simple is better than complex, and
readability counts.

Prefer type hints for public functions and service or repository methods.
Use dataclasses with slots for pure data structures. Avoid broad exceptions,
hidden side effects, mutable default arguments, and clever code that is hard to
maintain.
"""

SECURITY_CHECKLIST = """
# Security Checklist

Never commit hardcoded secrets, API keys, JWT signing keys, database passwords,
OAuth tokens, private keys, or cloud credentials. Read secrets from environment
variables or a secret manager. Mask secrets in logs and avoid returning them in
API responses.

For authentication, hash passwords with bcrypt, argon2, or passlib. Validate
JWT signature, issuer, audience, and expiration before trusting claims. Rotate
refresh tokens and revoke them on logout.

For injection prevention, use parameterized queries and safe ORM APIs. Never
concatenate user input into SQL, shell commands, LDAP filters, template code, or
NoSQL query objects.

For file handling, validate paths, reject traversal, limit file size, and do
not deserialize untrusted pickle payloads.
"""

FASTAPI_BEST_PRACTICES = """
# FastAPI Best Practices

Keep routes thin. Put business logic in services and persistence logic in
repositories. Validate external input with Pydantic v2 schemas for request
bodies, query parameters, headers, and file metadata.

Use APIRouter for domain modules. Inject dependencies such as database sessions,
repositories, and services instead of constructing them inside route handlers.
Raise HTTPException or custom application exceptions with meaningful status
codes. Do not log passwords, tokens, or personal data.
"""

CLEAN_CODE_PRINCIPLES = """
# Clean Code Principles

Optimize for correctness, readability, maintainability, simplicity, and then
performance. Functions should have one responsibility, short parameter lists,
early returns, and limited nesting. Remove dead code, commented-out code,
unused variables, and duplicated logic.

Apply SOLID and composition over inheritance where they reduce real complexity.
Avoid creating factories, interfaces, or utility classes for a single
implementation. Introduce abstractions only after repeated patterns justify
them.
"""

README_PLACEHOLDER = """
# Repository README Placeholder

When a repository is cloned, ingest its README or CONTRIBUTING guide as
project-specific context. This chunk should describe local setup, architecture,
coding rules, test commands, and any project-specific conventions that the AI
review agent should respect.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing coding_standards collection before seeding.",
    )
    args = parser.parse_args()

    pipeline = RAGIngestionPipeline()
    if args.reset:
        pipeline.reset()

    documents = build_seed_documents()
    chunks = pipeline.ingest_documents(documents)
    print(f"Ingested {len(chunks)} chunks from {len(documents)} sources.")

    retriever = HybridRetriever(bm25_index=pipeline.bm25_index)
    for query in ("SQL injection prevention Python", "hardcoded secret"):
        results = retriever.search(query, language="python", top_k=3)
        print(f"\nQuery: {query}")
        for result in results:
            print(
                f"- {result.source} | final={result.final_score:.3f} "
                f"vector={result.vector_score:.3f} bm25={result.bm25_score:.3f}"
            )


def build_seed_documents() -> list[RAGDocument]:
    repo_readme = _load_repo_readme()
    return [
        RAGDocument(
            source="OWASP Top 10 2021",
            content=OWASP_TOP_10_2021,
            language="python",
            doc_type="standard",
            category="security",
        ),
        RAGDocument(
            source="Python Best Practices PEP 8/PEP 20",
            content=PYTHON_BEST_PRACTICES,
            language="python",
            doc_type="guideline",
            category="maintainability",
        ),
        RAGDocument(
            source="Security Checklist",
            content=SECURITY_CHECKLIST,
            language="python",
            doc_type="checklist",
            category="security",
        ),
        RAGDocument(
            source="FastAPI Best Practices",
            content=FASTAPI_BEST_PRACTICES,
            language="python",
            doc_type="guideline",
            category="maintainability",
        ),
        RAGDocument(
            source="Clean Code Principles",
            content=CLEAN_CODE_PRINCIPLES,
            language="python",
            doc_type="guideline",
            category="maintainability",
        ),
        RAGDocument(
            source="Repo README",
            content=repo_readme,
            language="python",
            doc_type="project_doc",
            category="general",
        ),
    ]


def _load_repo_readme() -> str:
    for readme_name in ("README.md", "README.rst", "README.txt"):
        readme_path = PROJECT_ROOT / readme_name
        if readme_path.is_file():
            return readme_path.read_text(encoding="utf-8", errors="ignore")

    return README_PLACEHOLDER


if __name__ == "__main__":
    main()
