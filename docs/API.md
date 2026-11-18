# Manthan RAG — API (v1)

Base path: `/api/v1`. Interactive docs: `/api/docs`.

## Authentication

Send an API key on every call except `/health`:

```
X-API-Key: mrag_...
```

`Authorization: Bearer mrag_...` also works.

There are two kinds of key:

- **Admin key**: manages collections and keys, and sees every collection.
- **Scoped key**: sees only its collections. Give one of these to the customer application.

Keys are stored only as a SHA-256 hash, so a key cannot be shown again after creation.

Create the first admin key on the server (from `backend/`):

```
python scripts/create_api_key.py --name "Install admin" --admin
```

## Collections and keys (admin key)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/collections` | List collections with document counts |
| POST | `/admin/collections` | `{"name", "description"}` |
| PATCH | `/admin/collections/{id}` | Rename or deactivate |
| GET | `/admin/keys` | List keys (prefix only, never the key) |
| POST | `/admin/keys` | `{"name", "is_admin", "collection_ids": [..], "expires_at"}`; the response holds `api_key` once |
| POST | `/admin/keys/{id}/revoke` | Revoke (takes effect within 30 s on every server process; at once on the process that handled the call) |

## Ingest (any key, collection inside its scope)

`POST /ingest/pipeline` takes multipart form data with these fields:

- `name`
- `collection_id`
- `files` (one or more)
- `description` (optional)

It returns `202` with one entry per file. Each entry has `document_id`, `job_id` and `status`. The status is one of:

- `queued`
- `already_current`: the same file name in the same collection with identical bytes. It is not stored again.
- `failed`

A changed file with the same name becomes a new version. It replaces the old version once its processing completes.

Poll each job with `GET /ingest/status/{job_id}`.

Supported types: PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, JPG/PNG. Maximum size: `MAX_UPLOAD_SIZE_MB`.

## Documents (any key, scoped)

| Method | Path |
|---|---|
| GET | `/documents?collection_id=&status=&document_type=&page=&limit=` |
| GET | `/documents/{id}` |
| GET | `/documents/{id}/chunks?store=vector\|table\|clause\|research` |
| DELETE | `/documents/{id}` |

A document outside the key's scope answers `404`.

## Retrieve (any key, scoped)

`POST /retrieve`

```json
{
  "query": "What was the Q2 2026 revenue variance for EMEA?",
  "top_k": 10,
  "collection_ids": [1],
  "document_ids": null,
  "document_types": null,
  "use_reranker": true,
  "include_file_urls": false
}
```

Response:

```json
{
  "query": "...",
  "hits": [
    {
      "rank": 1,
      "score": 0.93,
      "text": "passage text (tables as rows)",
      "store": "table",
      "document": {"id": "uuid", "name": "Q2_Report.pdf", "collection_id": 1,
                    "version_no": 2, "document_type": "report", "periods": ["2026-Q2"]},
      "page": 7,
      "page_end": null,
      "section_title": "Regional performance",
      "table_markdown": "| Region | ... |",
      "clause_type": null,
      "risk_level": null,
      "image": null,
      "file_url": null
    }
  ],
  "total_candidates": 48,
  "stores_searched": ["table", "vector"],
  "structured_result": null,
  "timings": {"retrieve": 0.41, "rank": 0.22},
  "took_ms": 812
}
```

Values of `store`:

- `vector`: document text
- `table`
- `clause`: contract clauses
- `research`
- `image`
- `graph`: knowledge-graph facts (`document.id` is null)

Retrieval is limited to the key's documents before ranking. The service never writes an answer.

`top_k` is capped at `RETRIEVE_MAX_TOP_K` (default 50).

## Health

`GET /health` needs no key.
