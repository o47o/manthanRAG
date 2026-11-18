"""Apply the install / upgrade SQL in backend/db/schema to this service's schema.

Usage (from backend/):
    python scripts/migrate.py              # apply every file not yet applied
    python scripts/migrate.py --dry-run    # run everything inside one transaction, then roll back
    python scripts/migrate.py --status     # list applied files

Safety:
- Refuses to run unless DB_SCHEMA is this service's schema (settings guard + check below).
- Every SQL file must only reference the target schema (checked before running).
- Each file runs in its own transaction and is recorded in <schema>.schema_migrations.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402  (also enforces the forbidden-schema guard)

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "db" / "schema"
SCHEMA = settings.SUPABASE_SCHEMA
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

# Schemas a migration may mention besides its own (extension types/functions live there).
_ALLOWED_OTHER = {"public", "extensions", "pg_catalog"}


def _check_file(sql: str, name: str) -> None:
    """Fail if a file qualifies any object with a schema other than ours."""
    for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", sql.lower()):
        qual = m.group(1)
        if qual == SCHEMA or qual in _ALLOWED_OTHER:
            continue
        # Column refs like t.col / excluded.col are fine; only flag known platform schemas.
        if qual.startswith("tracopp"):
            raise SystemExit(f"{name}: references platform schema {qual!r} — refusing to run.")
    if re.search(r"\bdrop\s+schema\b", sql, re.I):
        raise SystemExit(f"{name}: contains DROP SCHEMA — refusing to run.")


def _connect():
    return psycopg2.connect(
        host=settings.SUPABASE_HOST, port=settings.SUPABASE_PORT, dbname=settings.SUPABASE_DB,
        user=settings.SUPABASE_USER, password=settings.SUPABASE_PASSWORD, sslmode=settings.DB_SSLMODE,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if not _IDENT.match(SCHEMA):
        raise SystemExit(f"invalid schema name {SCHEMA!r}")

    files = sorted(SCHEMA_DIR.glob("*.sql"))
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("select 1 from information_schema.tables where table_schema=%s and table_name='schema_migrations'", (SCHEMA,))
        has_table = cur.fetchone() is not None
        applied = {}
        if has_table:
            cur.execute(f"select filename, checksum from {SCHEMA}.schema_migrations")
            applied = dict(cur.fetchall())
        if args.status:
            for f in files:
                print(f"{'applied' if f.name in applied else 'pending'}  {f.name}")
            return

        pending = [f for f in files if f.name not in applied]
        for f in files:
            if f.name in applied and applied[f.name] != hashlib.sha256(f.read_bytes()).hexdigest():
                print(f"WARNING: {f.name} changed after it was applied (not re-run).")
        if not pending:
            print("Nothing to apply.")
            return

        for f in pending:
            sql = f.read_text()
            _check_file(sql, f.name)
        for f in pending:
            sql = f.read_text()
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            cur.execute(f"SET search_path = {SCHEMA}, public, extensions")
            cur.execute(sql)
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations ("
                "filename text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur.execute(
                f"INSERT INTO {SCHEMA}.schema_migrations (filename, checksum) VALUES (%s, %s)",
                (f.name, hashlib.sha256(f.read_bytes()).hexdigest()),
            )
            print(f"{'checked' if args.dry_run else 'applied'}  {f.name}")
            if not args.dry_run:
                conn.commit()
        if args.dry_run:
            conn.rollback()
            print("Dry run: rolled back, nothing kept.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
