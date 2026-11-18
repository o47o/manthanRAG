# Manthan RAG (standalone)

Self-hosted retrieval service. A customer application sends a question and gets the top-N
passages back (text, tables, clauses, images, knowledge graph) with document, page and score.
The customer's own LLM writes the answer. This service never writes answers.

Extracted from the Manthan platform RAG engine (`Agents/RAG`). Plan: `docs/manthan_standalone_rag_plan.md`.

## Layout
- `backend/` — FastAPI API + Celery worker (ingestion, retrieval)
- `admin-ui/` — Next.js admin screens (upload, documents, pipelines, query)
- `docs/` — plans and engine notes

## Run (customer install)

```bash
cp backend/.env.example backend/.env                 # fill DATABASE_URL, LLM_*, storage
docker compose build
docker compose --profile tools run --rm models       # one-time model download (needs internet)
docker compose --profile tools run --rm migrate      # create the database schema
docker compose up -d                                 # api (127.0.0.1:8000), worker, redis
docker compose exec api python scripts/create_api_key.py --name "Install admin" --admin
```

API reference: `docs/API.md` (interactive docs at `/api/docs`).

## Run (development on the shared Manthan server)

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build redis api worker
curl 127.0.0.1:8100/api/v1/health
```

The dev image adds only this repo's code on top of the platform's RAG image (same packages), and
mounts the platform's model volumes read-only. Memory is capped (api 2.5 GB, worker 3.5 GB) so these
containers cannot push the production containers into the OOM killer.

## Status
Phase 1 (extraction) in progress. See `docs/` for the build plan.
