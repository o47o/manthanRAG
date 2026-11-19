# Manthan RAG (standalone)

Self-hosted retrieval service. A customer application sends a question and gets the top-N
passages back (text, tables, clauses, images, knowledge graph) with document, page and score.
The customer's own LLM writes the answer. This service never writes answers.

Extracted from the Manthan platform RAG engine (`Agents/RAG`). Plan: `docs/manthan_standalone_rag_plan.md`.

## Layout
- `backend/` — FastAPI API + Celery ingestion worker
  - `app/api/auth.py` — API keys (`X-API-Key` or `Authorization: Bearer`), collection scope
  - `app/api/routes/retrieve.py` — `POST /api/v1/retrieve`, the customer endpoint
  - `app/api/routes/admin.py` — collections and API keys (admin key only)
  - `app/services/scope/` — question scope, model document picker, whole-document read, table repair
    (ported from the Manthan platform)
  - `db/schema/` — install SQL; `scripts/migrate.py` applies it
  - `scripts/create_api_key.py` — create the first admin key; `scripts/download_models.py` — one-time model download
- `admin-ui/` — Next.js admin console: sign in with an admin key; Collections, API keys, Upload,
  Pipelines, Documents, Retrieve tester
- `docker-compose.yml` — customer install; `docker-compose.dev.yml` — overlay for the shared dev server
- `docs/` — `API.md` (API reference), build plan, engine notes

## How it works
1. An admin creates **collections** and **API keys**. A key is either admin, or scoped to some collections.
2. Documents are uploaded into a collection (`POST /api/v1/ingest/pipeline`). The worker parses them
   (Docling, Tesseract OCR), chunks, embeds (e5-large-v2) and stores them in Postgres + pgvector.
   Re-uploading the same file (same name, same collection, same bytes) is skipped; a changed file
   becomes a new version.
3. The customer app calls `POST /api/v1/retrieve` with its scoped key. Retrieval is confined to the
   key's documents before ranking. When the question names a document, report id, period or document
   type, the search is narrowed to it (the model picks the document; rules are the fallback). A short
   named document comes back whole, in page order.

Full request/response reference: `docs/API.md` (interactive docs at `/api/docs`).

## Configuration
Copy `backend/.env.example` to `backend/.env` (environment variables override the file):

| Key | Purpose |
|---|---|
| `DATABASE_URL`, `DB_SCHEMA` | Postgres with pgvector; this service's own schema (default `manthan_rag`; platform schemas are refused) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `STORAGE_BUCKET` | File storage in Supabase Storage; leave the URL/key empty to store files on local disk |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Customer-supplied OpenAI-compatible LLM (base URL only; `/chat/completions` is added) |
| `EMBEDDING_MODEL`, `RERANKER_NAME` | Embedding model and reranker (downloaded once into the `models` volume) |
| `DOC_PICKER_ENABLED`, `RETRIEVE_MAX_TOP_K`, `RETRIEVE_FULL_DOC_*` | Retrieval behaviour (see `docs/API.md`) |
| `NEO4J_ENABLED`, `NEO4J_PASSWORD` | Optional knowledge graph. Put them in the `.env` **next to `docker-compose.yml`** (read by compose), then start with `--profile graph` |

## Run (customer install)

```bash
cp backend/.env.example backend/.env                 # fill DATABASE_URL, LLM_*, storage
docker compose build
docker compose --profile tools run --rm models       # one-time model download (needs internet)
docker compose --profile tools run --rm migrate      # create the database schema
docker compose up -d                                 # api (127.0.0.1:8000), worker, redis, admin-ui (127.0.0.1:3000)
docker compose exec api python scripts/create_api_key.py --name "Install admin" --admin
```

Then sign in to the admin console with that key, create a collection, upload documents, and create a
scoped key for the customer application.

## Run (development on the shared Manthan server)

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile graph up -d --build redis neo4j api worker admin-ui
curl 127.0.0.1:8100/api/v1/health
sudo docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T api \
    python scripts/create_api_key.py --name "Dev admin" --admin
```

- API on `127.0.0.1:8100` (Swagger at `/api/docs`), admin console on `127.0.0.1:3100`.
  Both listen on localhost only; from another machine use an SSH tunnel
  (PuTTY: Connection → SSH → Tunnels, Local `8100 → 127.0.0.1:8100` and `3100 → 127.0.0.1:3100`).
- The dev image adds only this repo's code on top of the platform's RAG image (same packages), and
  mounts the platform's model volumes read-only. Memory is capped (api 2.5 GB, worker 3.5 GB,
  admin-ui 512 MB) so these containers cannot push the production containers into the OOM killer.
- Knowledge graph: our own Neo4j container (`--profile graph`, 1.5 GB cap, no host ports, usage reporting off). The
  platform's Neo4j is Community edition (one database only) and is never shared.
- The dev database is the shared Supabase project, schema `manthan_rag`, bucket `manthan-rag-docs`.
  Never point this service at the platform's `tracopp_v2` schema or `rag-documents` bucket.

## Current dev deployment (as running on 19 Nov 2026)

How this service runs today on the shared Manthan AWS server (`13.126.161.22`, EC2 ap-south-1,
4 vCPU / 16 GB RAM), next to the production Manthan platform (`datamind-*` containers, untouched).

### Containers (`docker compose` project `manthan-rag`)

| Container | Image | Port | Memory cap | Role |
|---|---|---|---|---|
| `manthan-rag-api-1` | `manthan-rag:dev` | `127.0.0.1:8100` | 2.5 GB | FastAPI: `/api/v1/*`, Swagger at `/api/docs` |
| `manthan-rag-worker-1` | `manthan-rag:dev` | — | 3.5 GB | Celery ingestion worker, queues `ingestion,parse,embed,graph`, concurrency 1 |
| `manthan-rag-admin-ui-1` | `manthan-rag-admin:latest` | `127.0.0.1:3100` | 512 MB | Next.js admin console |
| `manthan-rag-redis-1` | `redis:7-alpine` | — | — | Celery broker / results |
| `manthan-rag-neo4j-1` | `neo4j:5.26-community` | — | 1.5 GB (heap 512 MB, page cache 256 MB) | Knowledge graph (our own; not the platform's); usage reporting off |

- `manthan-rag:dev` = this repo's `backend/` copied on top of the platform's `datamind-rag:latest`
  image (identical Python packages, Python 3.11, torch 2.5.1 CPU, Docling, Tesseract), so no second 3 GB install.
- Model files come from the platform's Docker volumes, mounted **read-only**:
  `datamind_hf_models` → `/models` (e5 + reranker), `datamind_docling_models` → `docling_models`.
- Only localhost ports are published. Access from a PC is by SSH tunnel (see above).

### Models

| Purpose | Model | Where it runs |
|---|---|---|
| LLM (document routing, picture reading, table re-read, table/clause enrichment, record extraction, graph entities, query routing, document picker) | `Gemma4-26B-A4B-AWQ` | C-DAC AIRAWAT endpoint `apis.airawat.cdac.in` (OpenAI-compatible; same endpoint as the platform) |
| Embeddings | `intfloat/e5-large-v2` (1024 dimensions, `passage:` / `query:` prefixes) | CPU, inside api and worker |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | CPU, inside api |
| OCR | Tesseract (`eng`) | CPU, inside worker |
| Layout / tables | Docling (layout + TableFormer weights) | CPU, inside worker |

### Database: Supabase Postgres, schema `manthan_rag`

Same Supabase project as the platform (connection through the pooler, port 6543), but a **separate schema**.
The platform schema `tracopp_v2` is never written; the service refuses to start on `tracopp`, `tracopp_v2`
or `public`. Extensions `vector` (pgvector 0.8) and `pg_trgm` are the project's existing ones in `public`.
Created by `backend/db/schema/001_install.sql` via `scripts/migrate.py` (recorded in `schema_migrations`).

| Table | Content |
|---|---|
| `collections` | **New.** Document groups; the access boundary for API keys |
| `api_keys` | **New.** Key name, prefix, SHA-256 hash (the key itself is never stored), admin flag, `collection_ids`, expiry, revoked/last-used time |
| `schema_migrations` | **New.** Applied install/upgrade files |
| `document_registry` | One row per document version: file, status, type, periods, hash, `version_no`, `is_active`, **`collection_id`** (new column) |
| `pipeline_runs`, `ingestion_jobs`, `rag_ingestion_errors` | Upload runs, per-document job progress and stage timings, errors |
| `parse_staging` | Parsed document handed from the parse task to the embed task |
| `vector_store` | Text passages + e5 vectors (HNSW index) + full-text index |
| `table_store`, `table_chunk_store`, `table_row_store`, `table_cell_store` | Tables: whole table, row windows, single rows, single cells (for exact lookups) |
| `clause_store` | Contract clauses (type, parties, risk) |
| `document_store` | Research-paper passages |
| `image_store` | Pictures and table images with OCR text and Gemma vision description |
| `document_records` | Records listed in documents (ids, dates, amounts) |
| views `document_overview`, `pipeline_run_overview` | Read by the documents / pipelines API |

The 14 engine tables have the same columns as the platform's tables. Foreign keys to the platform's
domain / category tables were dropped; the old `domain_id`-style columns remain but are unused.

### File storage

Supabase Storage, private bucket **`manthan-rag-docs`** (new; the platform's bucket `rag-documents` is
never used): originals under `documents/`, parse staging, table and picture crops under `images/`.

### Knowledge graph

Our own Neo4j 5.26 Community container. The platform's Neo4j is Community edition too, which allows only
one database, so a separate database inside it is not possible and it is not shared. Settings live in
the **compose `.env`** next to `docker-compose.yml` (gitignored): `NEO4J_ENABLED=true`, `NEO4J_PASSWORD`.
`backend/.env` still says `NEO4J_ENABLED=false`; the compose values override it inside the containers.
Neo4j was switched on during the first real load (19 Nov, 10:08 UTC): documents completed before that
have no graph yet and need `POST /api/v1/graph/rebuild/{document_id}`.

### Configuration files

| File | In git? | Holds |
|---|---|---|
| `backend/.env` | No | `DATABASE_URL`, `DB_SCHEMA=manthan_rag`, `DB_POOL_MAX=5`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `STORAGE_BUCKET=manthan-rag-docs`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL=Gemma4-26B-A4B-AWQ`, `EMBEDDING_MODEL`, `RERANKER_NAME`, `HF_HUB_OFFLINE=1` |
| `.env` (repo root) | No | Neo4j switch and password, read by docker compose |
| `backend/.env.example` | Yes | Template with every key and no values |

### Retrieval and ingestion settings in effect (code defaults unless noted)

| Setting | Value | Meaning |
|---|---|---|
| `HYBRID_SEARCH_ENABLED` | true | Keyword (full-text) + vector search, fused, then reranked |
| `AGENTIC_RAG_ENABLED` / `AGENTIC_MAX_LOOPS` | true / 2 | Engine re-checks and retries weak retrievals (LLM calls; main cause of the 4–5 s latency) |
| `INTENT_USE_LLM` | true | LLM decides which stores to search when rules are unsure |
| `DOC_PICKER_ENABLED` | true | The model picks the document a question is about |
| `RETRIEVE_MAX_TOP_K` | 50 | Upper limit for `top_k` |
| `GRAPHRAG_ENABLED` / `MULTI_HOP_MAX_RELATED_DOCS` | true / 5 | Graph facts and related-document expansion (the platform runs 1 loop / 0 related docs to save memory) |
| `GRAPH_EXTRACTION_MAX_TOKENS` | 2048 | Output tokens for per-passage entity/relationship extraction (was a fixed 768; long passages were cut off — fixed 19 Nov) |
| `RECORD_EXTRACTION_ENABLED` | true | Records extracted after each document |
| `GEMMA4_MAX_CONCURRENT` | 5 | Parallel LLM calls per process |
| `MAX_UPLOAD_SIZE_MB` | 100 | Per file |
| Worker concurrency | 1 | One document at a time (memory headroom next to production); about 5 min per document on the Sales corpus |

### Data loaded so far

- Collection **"Sales Data"**: 118 files (≈40 MB: PDF, DOCX, XLSX, PNG/JPG), uploaded 19 Nov 2026 through the
  admin console, ingesting overnight.
- Test collections "E2E Compliance" and "E2E Other" (4 compliance `.docx`) from the end-to-end test; to be removed.

## Tests

```bash
cd backend
python -m pytest tests/test_doc_scope.py tests/test_doc_picker.py tests/test_passages.py -q   # no database
RUN_DB_TESTS=1 python -m pytest tests/test_auth_scope.py -q                                   # writes to DB_SCHEMA, cleans up
```

## Status (19 Nov 2026)
Phase 1 (extraction) steps 1–8 are done: own config and schema, API-key auth and collections, `/retrieve`
with question scope and the model document picker, Docker images, and the admin console. End-to-end
test passed on the dev server. Own Neo4j added for the knowledge graph. First real corpus (Sales, 118 files)
loading.

Open:
- Retrieval latency: a ranked `/retrieve` takes about 4–5 s warm (the engine's built-in LLM steps); a fast mode is planned.
- `top_k` is not a hard limit: whole-document mode returns every passage, and related tables are added on top.
- Public HTTPS access (route through a reverse proxy) is not set up; the API listens on localhost only.
- PyMuPDF (AGPL-3.0) must be replaced or licensed before images ship to customers.
- `TABLE_VLM_RECONSTRUCT` is defined but not checked by the ingestion code (the Gemma table re-read always runs).
- Graph rebuild needed for documents completed before 19 Nov 10:35 UTC (before Neo4j was switched on, or
  before the extraction fix): `POST /api/v1/graph/rebuild/{document_id}`.
- The same graph-extraction cut-off bug exists in the platform engine
  (`Agents/RAG/backend/app/services/graph_extraction_service.py`, `max_tokens=768`); fixed only here.
- Later phases: licence check, signed images, opt-in telemetry, support access.
