"""Backfill period tags (document_registry.doc_metadata.periods) for active, completed documents.
No LLM call, reads stored passages / headings / table titles only. Shared schema (= production).

    python scripts/backfill_document_tags.py            # all active documents
    python scripts/backfill_document_tags.py --doc <uuid>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # Agents/RAG/backend → `app`

from app.db.connection import get_db
from app.services.document_tags import tag_document


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    a = ap.parse_args()
    with get_db() as conn:
        with conn.cursor() as cur:
            sql = "SELECT id::text, original_filename FROM document_registry WHERE is_active AND status = 'completed'"
            cur.execute(sql + (" AND id = %s" if a.doc else "") + " ORDER BY original_filename", [a.doc] if a.doc else [])
            docs = cur.fetchall()
    for did, name in docs:
        tags = tag_document(did)
        print(f"{name}: {tags.get('periods')}")
    print("documents:", len(docs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
