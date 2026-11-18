"""API-key authentication and collection scope.

Every customer call carries an API key (header ``X-API-Key`` or
``Authorization: Bearer <key>``). Keys are stored only as a SHA-256 hash in
``api_keys``. A key is either:

- an admin key: may manage collections and keys and sees every collection;
- a scoped key: sees only the collections listed in ``api_keys.collection_ids``.

Scope is enforced server side on ingest, document list/read/delete and
retrieve. A scoped key with no collections sees nothing (fail closed).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.db.connection import get_db

logger = logging.getLogger(__name__)

KEY_PREFIX = "mrag_"
_CACHE_TTL_SECONDS = 30
_LAST_USED_WRITE_SECONDS = 300

# Registered as OpenAPI security schemes so /api/docs shows the "Authorize" button.
# auto_error=False: a missing key is answered by require_key with our own 401.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                               description="API key (mrag_...). Create one with scripts/create_api_key.py.")
_bearer = HTTPBearer(auto_error=False, description="Same API key as a Bearer token.")

_cache: dict[str, tuple[float, Optional["Principal"]]] = {}
_last_used_written: dict[int, float] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Principal:
    key_id: int
    name: str
    is_admin: bool
    collection_ids: tuple[int, ...] = field(default_factory=tuple)

    def can_use_collection(self, collection_id: int) -> bool:
        return self.is_admin or collection_id in self.collection_ids


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    """New random key. Shown to the caller once; only the hash is stored."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _load_principal(key_hash: str) -> Optional[Principal]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, is_admin, collection_ids
                FROM api_keys
                WHERE key_hash = %s
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                (key_hash,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return Principal(key_id=row[0], name=row[1], is_admin=bool(row[2]),
                     collection_ids=tuple(row[3] or ()))


def _touch_last_used(key_id: int) -> None:
    now = time.time()
    with _lock:
        if now - _last_used_written.get(key_id, 0) < _LAST_USED_WRITE_SECONDS:
            return
        _last_used_written[key_id] = now
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (key_id,))
    except Exception as exc:  # never fail a request over bookkeeping
        logger.warning("last_used_at update failed for key %s: %s", key_id, exc)


def _extract_key(x_api_key: Optional[str], bearer: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if bearer and bearer.credentials:
        return bearer.credentials.strip()
    return None


def require_key(
    x_api_key: Optional[str] = Depends(_api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    raw = _extract_key(x_api_key, bearer)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key",
                            headers={"WWW-Authenticate": "Bearer"})
    key_hash = hash_key(raw)
    now = time.time()
    with _lock:
        hit = _cache.get(key_hash)
    if hit and now - hit[0] < _CACHE_TTL_SECONDS:
        principal = hit[1]
    else:
        principal = _load_principal(key_hash)
        with _lock:
            _cache[key_hash] = (now, principal)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key",
                            headers={"WWW-Authenticate": "Bearer"})
    _touch_last_used(principal.key_id)
    return principal


def require_admin(principal: Principal = Depends(require_key)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin API key required")
    return principal


# ── Scope helpers ──────────────────────────────────────────────────────────

def effective_collections(principal: Principal, requested: Optional[list[int]]) -> Optional[list[int]]:
    """Collections this call may touch.

    Returns None only for an admin with no filter (= every collection).
    A scoped key asking for a collection outside its scope gets 403.
    """
    if requested:
        denied = [c for c in requested if not principal.can_use_collection(c)]
        if denied:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"API key has no access to collection(s) {denied}")
        return list(dict.fromkeys(requested))
    if principal.is_admin:
        return None
    return list(principal.collection_ids)


def allowed_document_ids(collections: Optional[list[int]],
                         document_ids: Optional[list[str]] = None) -> Optional[list[str]]:
    """Active, completed documents inside ``collections`` (None = all collections).

    Returns None only when no restriction applies (admin, no filters).
    An empty list means "nothing visible" and must fail closed downstream.
    """
    if collections is None and not document_ids:
        return None
    if collections is not None and len(collections) == 0:
        return []
    where = ["is_active", "status = 'completed'"]
    params: list = []
    if collections is not None:
        where.append("collection_id = ANY(%s)")
        params.append(collections)
    if document_ids:
        where.append("id::text = ANY(%s)")
        params.append([str(d) for d in document_ids])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id::text FROM document_registry WHERE {' AND '.join(where)}", params)
            return [r[0] for r in cur.fetchall()]


def document_collection(document_id: str) -> Optional[int]:
    """Collection of one document, or raise 404 if the document does not exist."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT collection_id FROM document_registry WHERE id::text = %s", (str(document_id),))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return row[0]


def check_document_access(principal: Principal, document_id: str) -> None:
    """404 (not 403) for documents outside scope, so ids are not confirmed to exist."""
    coll = document_collection(document_id)
    if principal.is_admin:
        return
    if coll is None or coll not in principal.collection_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
