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
| `NEO4J_ENABLED`, `NEO4J_*` | Optional knowledge graph (`--profile graph`) |

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
sudo docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build redis api worker admin-ui
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
- The dev database is the shared Supabase project, schema `manthan_rag`, bucket `manthan-rag-docs`.
  Never point this service at the platform's `tracopp_v2` schema or `rag-documents` bucket.

## Tests

```bash
cd backend
python -m pytest tests/test_doc_scope.py tests/test_doc_picker.py tests/test_passages.py -q   # no database
RUN_DB_TESTS=1 python -m pytest tests/test_auth_scope.py -q                                   # writes to DB_SCHEMA, cleans up
```

## Status (18 Nov 2026)
Phase 1 (extraction) steps 1–8 are done: own config and schema, API-key auth and collections, `/retrieve`
with question scope and the model document picker, Docker images, and the admin console. End-to-end
test passed on the dev server.

Open:
- Retrieval latency: a ranked `/retrieve` takes about 4–5 s warm (the engine's built-in LLM steps); a fast mode is planned.
- `top_k` is not a hard limit: whole-document mode returns every passage, and related tables are added on top.
- Public HTTPS access (route through a reverse proxy) is not set up; the API listens on localhost only.
- PyMuPDF (AGPL-3.0) must be replaced or licensed before images ship to customers.
- Later phases: licence check, signed images, opt-in telemetry, support access.
