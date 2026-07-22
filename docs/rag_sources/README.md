# RAG Source Documents

This directory stores the source-of-truth documents ingested into the coding
standards RAG knowledge base.

Current sources:

- OWASP Top 10 2021, copied from `OWASP/Top10` `2021/docs/en`.
- OWASP Top 10 2025, copied from `OWASP/Top10` `2025/docs/en`.
- OWASP Cheat Sheet Series, copied from `OWASP/CheatSheetSeries` `cheatsheets`.
- Python PEP 8 and PEP 20, copied from `python/peps`.
- Google Python Style Guide, copied from `google/styleguide` `pyguide.md`.
- FastAPI tutorial docs, copied from `fastapi/fastapi` `docs/en/docs/tutorial`.
- FastAPI advanced docs, copied from `fastapi/fastapi` `docs/en/docs/advanced`.

The manifest is `sources.yaml`. `scripts/seed_rag.py` reads that manifest,
loads the Markdown files, splits them into chunks, embeds them, and persists
them in the local ChromaDB directory configured by `RAG_CHROMA_PATH`.

To refresh OWASP documents from GitHub:

```powershell
python scripts/fetch_owasp_top10.py
```

To refresh Python, Google, and FastAPI documents from GitHub:

```powershell
python scripts/fetch_rag_sources.py
```

To rebuild the local vector database:

```powershell
python scripts/seed_rag.py --reset
```
