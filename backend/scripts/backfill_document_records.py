"""Backfill document_records (migration 013) for documents loaded before the feature.

Reads the passages already stored for each active, completed document (vector_store +
table_store — the original file is not downloaded or re-parsed) and asks Gemma for the
records they describe, exactly as ingestion now does after a document completes.

Run from Agents/RAG/backend with the engine's environment:

    python scripts/backfill_document_records.py --dry-run        # counts only, no Gemma call
    python scripts/backfill_document_records.py                  # every active document
    python scripts/backfill_document_records.py --doc <uuid>     # one document
    python scripts/backfill_document_records.py --missing        # only documents with no records yet

Writes to the SHARED schema (local and production read the same table). Safe to re-run:
each document's records are replaced, never duplicated.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # Agents/RAG/backend → `app`

from app.db.connection import get_db
from app.services.record_extraction_service import extract_for_document

logging.basicConfig(level=logging.WARNING)


def _documents(doc: str | None, missing: bool) -> list[tuple[str, str]]:
    with get_db() as conn:
        with conn.cursor() as cur:
            sql = ("SELECT d.id::text, d.original_filename FROM document_registry d "
                   "WHERE d.is_active AND d.status = 'completed'")
            args: list = []
            if doc:
                sql += " AND d.id = %s"
                args.append(doc)
            if missing:
                sql += " AND NOT EXISTS (SELECT 1 FROM document_records r WHERE r.document_id = d.id)"
            cur.execute(sql + " ORDER BY d.original_filename", args)
            return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--missing", action="store_true")
    a = ap.parse_args()
    docs = _documents(a.doc, a.missing)
    total = 0
    for i, (did, name) in enumerate(docs, 1):
        try:
            res = extract_for_document(did, dry_run=a.dry_run)
        except Exception as exc:
            print(f"[{i}/{len(docs)}] {name}: ERROR {exc}")
            continue
        total += res.get("records", 0)
        print(f"[{i}/{len(docs)}] {name}: {res}")
    print(f"documents: {len(docs)}  records written: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
