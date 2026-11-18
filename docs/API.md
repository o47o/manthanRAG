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
  "auto_scope": true,
  "full_document": true,
  "report_family": null,
  "previous_report_family": null,
  "use_doc_picker": null,
  "add_related_tables": true,
  "use_reranker": true,
  "include_file_urls": false
}
```

### Question scope (`auto_scope`, on by default)

When the question names something precise, the search is first narrowed to the matching documents. This always happens inside the key's scope. The filters are applied in this order:

1. A report or document id, for example `SHP-2025-81218`, matched against the file name or title.
2. A document name, when most of its name words appear in the question. Example: "the Global Price Book".
3. A period: `Q2 2026`, `FY2025`, `May 2026` or `H1 2026`. These are tagged on the documents at ingestion.
4. A document type: SOP or procedure, policy, report, invoice or agreement.

A filter that matches no document is dropped. `scope.fallbacks` lists it, and `scope.note` gives one sentence for the user.

**Model pick (`DOC_PICKER_ENABLED`, on by default; `use_doc_picker` overrides it per call).** Unless the question spells out a report id, the model chooses the document(s). It sees short cards for up to 20 likely candidates: the documents the search ranks highest, the documents whose names share words with the question, and the documents the rules chose. Each card shows the name, title, type, periods and section and table headings.

The model can do one of three things:
- **Pick** the document(s) the question is about. Other copies come along, and so do other editions when the question names no period.
- **Ask**: return `scope.choices` when two different documents fit equally.
- **Search everything** when the question is general.

The pick costs one extra search plus one LLM call on your endpoint. If the model is off, unreachable or slow (`DOC_PICKER_TIMEOUT_SECONDS`), the rules above decide. `scope.picked_by` is `rules`, `model` or `report_family`. `scope.reason` holds the model's one-line reason.

`previous_report_family`: send `scope.report_family` from the previous question in the same conversation. When the new question names no other document, the model prefers that one.

**Whole document.** When the question names a short document (up to 2 documents, about 22,000 characters), `mode` is `full_document`. All of its text, tables and clauses come back in page order instead of the top-k passages.

**"Which report?"** A period can still leave several kinds of report, for example a "Q3 2026" question when there is a forecast, a scorecard and a performance report. Then `scope.choices` lists them as `[{"label", "documents"}]`. The hits still come from all of them. The calling app can ask its user which one they mean, then send the same question with `report_family` set to the chosen label.

**Related tables.** Tables cut off by chunking are replaced with the complete table. Tables that a found passage refers to but the search missed are added right after that passage, with `"added": "companion_table"` and `score: null`.

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
  "mode": "ranked",
  "scope": {"applied": ["period 2026-Q2"], "fallbacks": [], "note": null,
            "documents": ["Q2_Report.pdf"], "report_family": null, "choices": []},
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
