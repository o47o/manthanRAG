"""Admin API: collections and API keys. Every route needs an admin key."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import KEY_PREFIX, Principal, clear_cache, generate_key, hash_key, require_admin
from app.db.connection import get_db

router = APIRouter(dependencies=[Depends(require_admin)])


# ── Collections ────────────────────────────────────────────────────────────

class CollectionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class CollectionPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class Collection(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool
    document_count: int = 0
    created_at: datetime


_COLL_SELECT = """
    SELECT c.id, c.name, c.description, c.is_active, c.created_at,
           (SELECT count(*) FROM document_registry d
             WHERE d.collection_id = c.id AND d.is_active AND d.status = 'completed')
    FROM collections c
"""


def _coll(row) -> Collection:
    return Collection(id=row[0], name=row[1], description=row[2], is_active=row[3],
                      created_at=row[4], document_count=row[5])


@router.get("/collections", response_model=list[Collection])
def list_collections():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(_COLL_SELECT + " ORDER BY c.id")
            return [_coll(r) for r in cur.fetchall()]


@router.post("/collections", response_model=Collection, status_code=status.HTTP_201_CREATED)
def create_collection(body: CollectionIn):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO collections (name, description) VALUES (%s, %s) RETURNING id",
                            (body.name.strip(), body.description))
                new_id = cur.fetchone()[0]
                cur.execute(_COLL_SELECT + " WHERE c.id = %s", (new_id,))
                return _coll(cur.fetchone())
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "A collection with this name already exists")


@router.patch("/collections/{collection_id}", response_model=Collection)
def update_collection(collection_id: int, body: CollectionPatch):
    sets, params = [], []
    for col in ("name", "description", "is_active"):
        val = getattr(body, col)
        if val is not None:
            sets.append(f"{col} = %s")
            params.append(val.strip() if isinstance(val, str) and col == "name" else val)
    with get_db() as conn:
        with conn.cursor() as cur:
            if sets:
                cur.execute(f"UPDATE collections SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
                            params + [collection_id])
            cur.execute(_COLL_SELECT + " WHERE c.id = %s", (collection_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Collection not found")
    return _coll(row)


# ── API keys ───────────────────────────────────────────────────────────────

class KeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    is_admin: bool = False
    collection_ids: list[int] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    is_admin: bool
    collection_ids: list[int]
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]


class KeyCreated(KeyOut):
    api_key: str = Field(..., description="Shown once. Store it now; it cannot be read again.")


_KEY_SELECT = """SELECT id, name, key_prefix, is_admin, collection_ids, created_at,
                        last_used_at, expires_at, revoked_at FROM api_keys"""


def _key(row) -> dict:
    return dict(id=row[0], name=row[1], key_prefix=row[2], is_admin=row[3],
                collection_ids=list(row[4] or []), created_at=row[5], last_used_at=row[6],
                expires_at=row[7], revoked_at=row[8])


def create_key(name: str, is_admin: bool, collection_ids: list[int],
               expires_at: Optional[datetime] = None) -> dict:
    """Shared by the API route and scripts/create_api_key.py."""
    if not is_admin and not collection_ids:
        raise ValueError("A non-admin key needs at least one collection")
    with get_db() as conn:
        with conn.cursor() as cur:
            if collection_ids:
                cur.execute("SELECT id FROM collections WHERE id = ANY(%s)", (collection_ids,))
                found = {r[0] for r in cur.fetchall()}
                missing = sorted(set(collection_ids) - found)
                if missing:
                    raise LookupError(f"Unknown collection(s) {missing}")
            raw = generate_key()
            cur.execute(
                """INSERT INTO api_keys (name, key_prefix, key_hash, is_admin, collection_ids, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (name.strip(), raw[: len(KEY_PREFIX) + 6], hash_key(raw), is_admin,
                 sorted(set(collection_ids)), expires_at),
            )
            new_id = cur.fetchone()[0]
            cur.execute(_KEY_SELECT + " WHERE id = %s", (new_id,))
            out = _key(cur.fetchone())
    out["api_key"] = raw
    return out


@router.get("/keys", response_model=list[KeyOut])
def list_keys():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(_KEY_SELECT + " ORDER BY id")
            return [KeyOut(**_key(r)) for r in cur.fetchall()]


@router.post("/keys", response_model=KeyCreated, status_code=status.HTTP_201_CREATED)
def create_key_route(body: KeyIn):
    try:
        return KeyCreated(**create_key(body.name, body.is_admin, body.collection_ids, body.expires_at))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post("/keys/{key_id}/revoke", response_model=KeyOut)
def revoke_key(key_id: int, principal: Principal = Depends(require_admin)):
    if key_id == principal.key_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot revoke the key you are using")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now()) WHERE id = %s", (key_id,))
            cur.execute(_KEY_SELECT + " WHERE id = %s", (key_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    clear_cache()
    return KeyOut(**_key(row))
