# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All backend commands run from the `backend/` directory with the venv activated.

```bash
# Backend
cd backend
.venv\Scripts\activate                            # Windows
uvicorn app.main:app --reload                     # API server (port 8000)
celery -A app.core.background_tasks worker --loglevel=info  # Celery worker

# Tests
pytest tests/ -v

pytest tests/ -v -m "not slow"                   # Skip embedding/reranker model tests
pytest tests/test_parser.py -v                   # Single test file

# Frontend
cd frontend
npm run dev                                       # Next.js dev server (port 3000)
npm run lint
```

**Docker (preferred for full stack):**
```bash
docker-compose up                                 # Starts redis, api, worker, frontend
```

## Architecture

MANTHAN is a multi-store RAG system. Documents are classified by type and routed to specialised PostgreSQL stores (backed by Supabase + pgvector), then retrieved and synthesised by an LLM.

### Ingestion Pipeline (Celery — LIVE path is the split chain in `app/services/ingestion_tasks.py`)

**Two implementations exist.** The one that actually runs is the split task chain
`ingestion_tasks.parse_task → chunk_embed_store_task` (queues `parse`, `embed`, `graph`). The
monolithic `ingestion_orchestrator.ingest_document` is the older single-task form. Any hook that
must run at a stage boundary (e.g. the document-versioning flip and the `embedding_model` stamp on
completion) has to be wired into BOTH — `tests/test_ingestion_completion_hooks.py` enforces it after
the Sep 2026 incident where a hook added only to the orchestrator never ran.

Five sequential stages — progress tracked in `ingestion_jobs` table, state in `document_registry`:

1. **Parsing** → Docling (OCR + table extraction) → `ParsedDocument`
2. **Routing** → Gemma 4 on CDAC endpoint classifies document type; falls back to rule-based if unavailable
3. **Chunking** → type-aware splitter; legal docs also run `extract_legal_clauses()` separately
4. **Embedding** → `intfloat/e5-large-v2` (1024-dim, `"passage: "` prefix; env `BGE_MODEL_NAME` — key name historical, was BGE until 21 Sep 2026); financial table summaries embedded separately. `document_registry.embedding_model` records which model produced each document's vectors.
5. **Storing** → `storage_service.store_chunks()` routes by document type (see Multi-Store below)

Upload flow: file uploaded to Supabase Storage bucket → storage path passed to Celery → worker downloads to a local `tempfile`, processes, then deletes local copy.

### Query Pipeline (`app/api/routes/query.py` + services)

1. Embed query with the e5 search prefix (`"query: …"`; stored text used `"passage: "`)
2. HNSW vector search across relevant stores (up to 15 results per store)
3. Re-rank with `cross-encoder/ms-marco-MiniLM-L-6-v2` (CrossEncoder, scores [0,1]; env `RERANKER_NAME`)
4. Synthesise answer via Gemma 4 (CDAC OpenAI-compatible endpoint)

### Multi-Store Routing

| Document type | Primary store | Notes |
|---|---|---|
| `policy` | `vector_store` | Semantic chunks with HNSW |
| `financial` | `table_store` + `vector_store` | Tables as JSON + markdown; text chunks |
| `legal` | `clause_store` | Clause-level with `risk_level`, `parties`, `key_dates` JSONB |
| `research` | `document_store` | Chunks with citation metadata |

All stores live in the `manthan_rag_working` schema (set via `search_path` in the connection pool).

### DB Layer

- **Connection**: `app/db/connection.py` — `ThreadedConnectionPool` (psycopg2, min=2, max=10), `sslmode=require`. Use `get_db()` context manager; it registers pgvector and auto-commits/rolls back.
- **Repositories**: `app/db/repositories/` — thin functions (`update_status`, `update_job`) that take explicit kwargs for each metadata field. No ORM.
- **Schema**: raw SQL in `app/db/migrations/001_initial_schema.sql`; no Alembic. Apply manually via Supabase SQL Editor. Schema is `manthan_rag_working`, extensions: `vector`, `uuid-ossp`, `pg_trgm`.

### Configuration (`app/config.py`)

`pydantic-settings` reads root `.env` then `backend/.env` (both resolved via absolute paths anchored to `config.py`, so it works regardless of launch cwd) — `backend/.env` values override root `.env`. The intended end state is a single root `.env`, but the fallback to `backend/.env` was restored because some checkouts (including secrets like `NEO4J_URI`/`NEO4J_USERNAME`) still only have a `backend/.env`, and a root-only source silently zeroed those out. `extra = "ignore"` so frontend-only vars (e.g. `NEXT_PUBLIC_*`) don't cause validation errors.

`SUPABASE_SERVICE_KEY` is required for Supabase Storage operations (upload/download). The Gemma 4 endpoint (`GEMMA4_BASE_URL`) is an OpenAI-compatible API hosted by CDAC — confirm format before changing call structure.

### Embedding Model Loading

The 1.3 GB embedding model (`intfloat/e5-large-v2`) is pre-loaded at Celery worker module import (`embedding_service.warmup()`). Restarting the worker causes a ~30–60s reload. Don't import `embedding_service` in the API process unless necessary. **Never change the model without re-embedding every store** — see `docs/EMBEDDING_MODEL_SWAP.md` (vectors from two models are not comparable; `reembed_documents.py` skips documents whose `embedding_model` already matches).

### Frontend

Next.js 14 app-router. `/api/*` requests are proxied to `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) via `next.config.js` rewrites. State management via `@tanstack/react-query`. No Redux.

## graphify

This project's knowledge graph always lives at the project root: `D:\GITHUB PROJECTS\MANTHAN_AI-RAG\graphify-out\` (god nodes, community structure, cross-file relationships).

Rules (always apply in this repo, regardless of which subdirectory a session starts in):
- Treat graphify as the default entry point for any codebase question in this project — always run it from the project root, not from `backend/` or `frontend/`.
- For codebase questions, first run `graphify query "<question>"` when `graphify-out/graph.json` exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If `graphify-out/graph.json` does not exist yet, run `/graphify` on the project root before falling back to manual grep/exploration.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation instead of raw source browsing.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` from the project root to keep the graph current (AST-only, no API cost).
