# MANTHAN RAG — Enterprise Agentic RAG

Multi-retriever document intelligence system for Policy, Financial, Legal, Entity & Research queries.

## Architecture

```
PDF Upload → Docling Parse → Gemma 4 26B Router → BGE Embeddings → Supabase Stores
                                                                   ↓
             policy     → vector_store  (PgVector HNSW, 1024-dim)
             financial  → table_store   (JSON tables + embeddings)
             legal      → clause_store  (clause boundaries + risk metadata)
             research   → document_store (chunks + citations)
             entity     → vector_store  (Phase 1 fallback) / Neo4j (Phase 3)
                                                                   ↓
                          BGE Re-Ranker (bge-reranker-large CrossEncoder)
                                                                   ↓
                          Gemma 4 26B Synthesis → Answer + Citations
```

## Tech Stack

| Component | Technology |
|---|---|
| LLM Router + Synthesis | Gemma 4 26B (CDAC server) |
| Embeddings | BAAI/bge-large-en-v1.5 (1024-dim, local) |
| Re-Ranker | BAAI/bge-reranker-large (CrossEncoder, local) |
| Document Parse | Docling (OCR + table extraction) |
| Primary DB | Supabase PostgreSQL + pgvector |
| Schema | `manthan_rag_working` |
| Backend | FastAPI + Celery + Redis |
| Frontend | Next.js 14 + Tailwind CSS |

## Quick Start

### 1. Apply the Database Schema

Open Supabase SQL Editor and run:

```
backend/app/db/migrations/001_initial_schema.sql
```

### 2. Configure Environment

```bash
cp .env.example .env
# Fill in SUPABASE_PASSWORD, GEMMA4_BASE_URL, GEMMA4_API_KEY, etc.
```

### 3. Start with Docker

```bash
docker-compose up
```

- API: http://localhost:8000/api/docs
- Frontend: http://localhost:3000

### 4. Start Manually (Development)

**Backend:**
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
# In a second terminal:
celery -A app.core.background_tasks worker --loglevel=info -Q ingestion
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

**Redis (required for Celery):**
```bash
docker run -d -p 6379:6379 redis:7-alpine
```

## Run Tests

```bash
cd backend
pytest tests/ -v
# Skip slow embedding/reranker tests (require model download):
pytest tests/ -v -m "not slow"
```

## Database Schema

| Table | Purpose | Document Type |
|---|---|---|
| `document_registry` | Central document registry | All |
| `vector_store` | Semantic text chunks (HNSW) | policy, entity |
| `table_store` | Structured tables as JSON | financial |
| `clause_store` | Legal clauses with risk metadata | legal |
| `document_store` | Research chunks with citations | research |
| `ingestion_jobs` | Pipeline stage tracking | All |
| `document_overview` | View with chunk counts | All |

## Ingestion Pipeline (Phase 1)

```
uploaded → parsing → routing → chunking → embedding → storing → completed
```

Poll `GET /api/v1/ingest/status/{job_id}` for live progress (frontend polls every 2s).

## Query Pipeline (Phase 2)

```
POST /api/v1/query
        ↓
BGE query embedding  ("Represent this question for searching relevant passages: …")
        ↓
HNSW cosine search across all relevant stores (up to 15 candidates per store)
        ↓
BGE Re-Ranker  (CrossEncoder logits → sigmoid normalisation → [0, 1] relevance score)
        ↓
Top-K chunks passed to Gemma 4 26B  (falls back to direct chunk display if unavailable)
        ↓
JSON response: answer + confidence + citations + retrieval stats
```

### Query API

```
POST /api/v1/query
{
  "query": "What is the data retention policy?",
  "document_types": ["policy", "legal"],   // optional filter
  "document_id": "<uuid>",                 // optional single-doc scope
  "top_k": 5,                              // default 5, max 20
  "use_reranker": true
}
```

Response includes: `answer`, `confidence` (0–1), `citations` (with page, clause type, risk level, relevance score), `retrieval_stats`, `processing_time_seconds`.

### Chat UI

The `/query` page is a full chat interface:
- Conversation thread with user and assistant bubbles
- Typing indicator while the pipeline runs
- Collapsible citations panel per assistant message — shows source file, page, store type, relevance %, and full chunk text
- Document type filter chips to scope retrieval
- Suggested starter queries in the empty state
- Clear conversation button

## AI Coding Agents

This repository is configured for Claude Code and graphify-assisted development.

### Claude Code (CLAUDE.md)

`CLAUDE.md` at the project root gives Claude Code full context on the architecture, commands, pipeline stages, DB layer, and configuration — Claude can navigate this codebase without being walked through it manually.

### Graphify — Code Knowledge Graph

Graphify builds a graph of cross-file relationships (AST nodes, imports, call edges) so Claude and teammates can answer structural questions instantly (`graphify query`, `graphify path`, `graphify explain`).

`graphify-out/` is gitignored (generated artifact). After cloning, build it locally — no API key or LLM needed:

```bash
# Install graphify
uv tool install graphifyy

# Build code graphs (AST-only, ~5 seconds each)
graphify extract backend/app --no-cluster
graphify extract frontend/src --no-cluster

# Wire it into Claude Code
graphify claude install
```

Query examples:
```bash
graphify query "how does ingestion work" --graph backend/app/graphify-out/graph.json
graphify path "upload_document" "ingest_document" --graph backend/app/graphify-out/graph.json
graphify explain "retriever_service" --graph backend/app/graphify-out/graph.json
```

After modifying code, keep the graph current:
```bash
graphify update backend/app   # AST-only, no API cost
graphify update frontend/src
```

## Implemented Phases

| Phase | Scope | Status |
|---|---|---|
| Phase 1 | Ingestion pipeline — Docling, Gemma 4 router, BGE embeddings, 4 Supabase stores, Celery, upload UI, document list + detail | ✅ Complete |
| Phase 2 | Query pipeline — HNSW retrieval, BGE Re-Ranker, Gemma 4 synthesis, citations, chat UI | ✅ Complete |
| Phase 3 | Neo4j Graph Store for entity documents, multi-hop retrieval | Planned |
