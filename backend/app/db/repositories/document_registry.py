import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.connection import get_db

logger = logging.getLogger(__name__)


def update_status(document_id: str, status: str, **kwargs) -> None:
    """Update document_registry status and any optional stage timestamp."""
    allowed_ts_fields = {"parsed_at", "chunked_at", "embedded_at", "stored_at", "completed_at"}
    sets = ["status = %s", "updated_at = NOW()"]
    params: list = [status]

    for field in allowed_ts_fields:
        if field in kwargs and kwargs[field] is True:
            sets.append(f"{field} = NOW()")

    for field in ("error_stage", "error_message", "page_count", "word_count",
                  "has_tables", "has_images", "document_type", "document_subtype",
                  "router_confidence", "router_reasoning", "doc_title", "doc_author",
                  "doc_date", "doc_summary", "doc_metadata", "storage_path",
                  "embedding_model", "embedding_dimension"):
        if field in kwargs:
            sets.append(f"{field} = %s")
            params.append(kwargs[field])

    params.append(document_id)
    sql = f"UPDATE document_registry SET {', '.join(sets)} WHERE id = %s"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def activate_version(document_id: str) -> Optional[str]:
    """Document versioning: called ONLY when a document's ingestion reaches 'completed'.

    If this row is a new version (`supersedes` set), retire the previous version in the
    same transaction: old row -> is_active = false, superseded_by = this id. Old chunks,
    graph links and the stored file are kept (audit + rollback by flipping the flag).
    Returns the superseded id, or None when this document is not a new version.

    A reprocess (rebuild of the same id) never sets `supersedes`, so it never flips
    anything — versioning is decided at upload time, not here.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT supersedes FROM document_registry WHERE id = %s", (document_id,))
            row = cur.fetchone()
            old_id = str(row[0]) if row and row[0] else None
            if not old_id:
                return None
            cur.execute(
                """UPDATE document_registry
                   SET is_active = false, superseded_by = %s, updated_at = NOW()
                   WHERE id = %s AND (superseded_by IS NULL OR superseded_by = %s)""",
                (document_id, old_id, document_id),
            )
            cur.execute(
                "UPDATE document_registry SET is_active = true WHERE id = %s",
                (document_id,),
            )
    logger.info("[%s] version activated — superseded %s", document_id, old_id)
    return old_id
