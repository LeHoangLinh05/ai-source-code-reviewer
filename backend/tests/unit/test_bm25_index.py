"""Tests for BM25 keyword retrieval."""

from app.ai.rag.bm25_index import BM25Document, BM25Index


def test_bm25_search_returns_security_keyword_match() -> None:
    index = BM25Index()
    index.add_documents(
        [
            BM25Document(
                id="owasp",
                content=(
                    "SQL injection prevention in Python requires parameterized "
                    "queries and safe SQLAlchemy bind parameters."
                ),
                metadata={"source": "OWASP Top 10 2021", "language": "python"},
            ),
            BM25Document(
                id="clean-code",
                content="Small functions and descriptive names improve readability.",
                metadata={"source": "Clean Code", "language": "python"},
            ),
        ]
    )

    results = index.search(
        "SQL injection prevention Python",
        top_k=1,
        language="python",
    )

    assert results[0].id == "owasp"
    assert results[0].score > 0
