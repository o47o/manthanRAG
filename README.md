# Manthan RAG (standalone)

Self-hosted retrieval service. A customer application sends a question and gets the top-N
passages back (text, tables, clauses, images, knowledge graph) with document, page and score.
The customer's own LLM writes the answer. This service never writes answers.

Extracted from the Manthan platform RAG engine (`Agents/RAG`). Plan: `docs/manthan_standalone_rag_plan.md`.

## Layout
- `backend/` — FastAPI API + Celery worker (ingestion, retrieval)
- `admin-ui/` — Next.js admin screens (upload, documents, pipelines, query)
- `docs/` — plans and engine notes

## Status
Phase 1 (extraction) in progress. See `docs/` for the build plan.
