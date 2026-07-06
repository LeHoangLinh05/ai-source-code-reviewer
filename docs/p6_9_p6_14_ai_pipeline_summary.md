# P6.9-P6.14 AI Pipeline Summary

## Pham vi da lam

Da hoan thanh nhom task AI Pipeline:

- P6.9: Python AST Chunker theo class/function boundary.
- P6.10: Metadata day du cho moi code chunk.
- P6.11: ChromaDB PersistentClient wrapper cho collection `coding_standards`.
- P6.12: Knowledge base ingestion cho OWASP, Python best practices, Security Checklist, FastAPI, Clean Code, Repo README.
- P6.13: Embedding model `sentence-transformers/all-MiniLM-L6-v2` chay CPU.
- P6.14: BM25 keyword search va HybridRetriever merge vector + keyword score.

## File da thay doi hoac tao moi

### Thay doi

- `backend/app/analyzers/code_chunker.py`
  - Mo rong tu stub thanh AST-based Python chunker day du.
  - Them `CodeChunkMetadata`, `CodeChunk`, `StaticIssueRange`.
  - Them fallback fixed 60 lines, overlap 10 lines.
  - Them `MAX_TOKENS_PER_CHUNK = 1500`.
  - Them heuristic `risk_area` dung theo `AI_flow.md` muc 2.2.

- `backend/app/ai/rag/vectorstore.py`
  - Them ChromaDB PersistentClient wrapper theo singleton pattern.
  - Collection co ten `coding_standards`.
  - Dung cosine similarity qua metadata `{"hnsw:space": "cosine"}`.
  - Dung embedding model `sentence-transformers/all-MiniLM-L6-v2` tren CPU.

- `backend/app/ai/rag/ingestion.py`
  - Them `RAGIngestionPipeline`.
  - Load/split document 512 tokens, overlap 50.
  - Gan metadata: `source`, `chunk_index`, `language`, `doc_type`, `category`, `ingested_at`.
  - Insert vao ChromaDB va BM25 index.

- `backend/app/ai/rag/retriever.py`
  - Them `HybridRetriever`.
  - Merge vector + BM25 theo cong thuc:
    `final_score = 0.7 * vector_score + 0.3 * bm25_score`.
  - Ho tro `search(query, language=None, top_k=3)`.

- `backend/app/ai/rag/__init__.py`
  - Export cac class RAG chinh de import gon hon.

- `backend/app/core/config.py`
  - Them default config:
    - `rag_chroma_path`
    - `rag_embedding_model`

- `backend/requirements.txt`
  - Them dependencies:
    - `chromadb`
    - `sentence-transformers`
    - `torch`
    - `rank-bm25`

- `.env.example`
  - Them nhom bien RAG:
    - `RAG_CHROMA_PATH`
    - `RAG_EMBEDDING_MODEL`
    - `HF_HUB_OFFLINE`
    - `TRANSFORMERS_OFFLINE`

- `.gitignore`
  - Ignore `.chroma/` vi day la ChromaDB runtime data co the regenerate bang `scripts/seed_rag.py`.

- `task.md`
  - Danh dau P6.9-P6.14 la da hoan thanh.

### Tao moi

- `backend/app/ai/rag/bm25_index.py`
  - BM25 index builder/searcher dung `rank-bm25`.
  - Co fallback scoring khi corpus qua nho lam `rank-bm25` tra toan score 0.

- `scripts/seed_rag.py`
  - Seed 6 nguon knowledge base:
    1. OWASP Top 10 2021
    2. Python Best Practices PEP 8/PEP 20
    3. Security Checklist
    4. FastAPI Best Practices
    5. Clean Code Principles
    6. Repo README hoac placeholder neu repo chua co README

- `backend/tests/unit/test_code_chunker.py`
  - Test AST chunker voi file co 2 class + 5 function.
  - Test fallback 60 lines/overlap 10.
  - Test heuristic `risk_area`.

- `backend/tests/unit/test_bm25_index.py`
  - Test BM25 tra dung OWASP content cho query SQL injection.

## Giai thich de hieu

### AST Chunker lam gi?

Thay vi cat file Python theo so dong mot cach may moc, chunker doc AST cua Python de biet dau la `class`, dau la `function`.

Thu tu uu tien:

1. Tao chunk cho class.
2. Tao chunk cho function/method.
3. Phan code ngoai class/function duoc gan `chunk_type="module"`.
4. Neu file Python bi loi syntax, fallback ve chunk 60 dong, overlap 10 dong.

Moi chunk co line number chinh xac, ten function/class, imports, module, risk area va token count. Metadata nay giup AI Agent doc dung doan code can review thay vi search ca repo.

### Risk area duoc detect nhu the nao?

Heuristic theo `AI_flow.md` muc 2.2:

- `security`: file trong `auth/`, `security/`, `crypto/`, `middleware/` hoac import `jwt`, `bcrypt`, `hashlib`, `cryptography`, `secrets`.
- `database`: file trong `db/`, `models/`, `repositories/` hoac import `sqlalchemy`, `pymongo`.
- `api`: file trong `routers/`, `api/`, `endpoints/`.
- `config`: file ten `config.py`, `settings.py`, `.env`.
- `general`: mac dinh.

### RAG Knowledge Base lam gi?

RAG luu coding standards vao ChromaDB de AI co can cu khi review. Khi nghi ngo security issue, Agent co the search knowledge base truoc khi tao issue.

Pipeline ingestion:

1. Load document.
2. Split 512 tokens, overlap 50.
3. Gan metadata.
4. Embed bang `all-MiniLM-L6-v2`.
5. Insert vao ChromaDB persistent.
6. Insert cung chunk vao BM25 keyword index.

### HybridRetriever merge ket qua ra sao?

Retriever chay 2 search song song:

- Vector search: tim doan lien quan ve nghia.
- BM25 search: bat keyword chinh xac nhu `SQL injection`, `hardcoded secret`, `JWT`.

Sau do deduplicate theo chunk id va tinh:

```text
final_score = 0.7 * vector_score + 0.3 * bm25_score
```

Ket qua sap xep theo `final_score` giam dan.

## Bien moi truong can biet

Khong bat buoc them gi neu dung default.

Co the them vao `.env` neu muon tuy bien:

```env
RAG_CHROMA_PATH=.chroma
RAG_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

Neu may da download model mot lan va dang chay trong moi truong bi chan Internet, co the bat offline mode:

```env
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

Luu y: lan dau seed RAG can Internet de tai embedding model tu Hugging Face.

## Lenh da verify

Chay seed RAG:

```bash
python scripts/seed_rag.py --reset
```

Ket qua da dat:

```text
Ingested 6 chunks from 6 sources.

Query: SQL injection prevention Python
- OWASP Top 10 2021 | final=0.667 ...

Query: hardcoded secret
- Security Checklist | final=0.524 ...
```

Kiem tra retriever trong process moi, dung ChromaDB persistent va BM25 rebuild tu data persisted:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -c "from app.ai.rag.retriever import HybridRetriever; ..."
```

Ket qua:

```text
SQL injection prevention Python -> OWASP Top 10 2021 dung dau
hardcoded secret -> Security Checklist dung dau
```

Kiem tra chunker:

```text
File Python mau co 2 class + 5 function -> tra 7 chunks
Moi chunk co metadata: file_path, language, chunk_type, chunk_index, total_chunks,
function_name, class_name, line_start, line_end, imports, module, risk_area,
has_static_issues, token_count
```

Ruff:

```bash
ruff check --no-cache app/analyzers/code_chunker.py app/ai/rag tests/unit/test_code_chunker.py tests/unit/test_bm25_index.py ..\scripts\seed_rag.py
```

Ket qua:

```text
All checks passed!
```

Manual assertions:

```text
manual assertions passed
```

## Luu y ve test hien tai

`pytest` trong environment nay co hien tuong chay xong test nhung process khong thoat truoc timeout. Vi vay da verify bang:

- Ruff subset.
- Manual assertion truc tiep cho cac test moi.
- Smoke test chunker.
- Seed RAG end-to-end.
- HybridRetriever search trong process moi.

`mypy` hien tai bi internal error cua tool:

```text
error: INTERNAL ERROR
version: 2.1.0
```

Day la loi tool/runtime trong environment, khong phai report type error cu the tu source code.
