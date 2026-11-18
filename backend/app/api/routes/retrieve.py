"""Customer retrieval API: question in, ranked passages out. No answer writing.

The customer's own application (and LLM) builds the answer from the hits.
This wraps the engine's query pipeline in retrieve-only mode
(``synthesize=False``) and always passes an explicit document allow-list
derived from the API key's collections, so retrieval is confined to what
the key may see BEFORE ranking.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import Principal, allowed_document_ids, effective_collections, require_key
from app.api.routes.query import QueryRequest, query_documents
from app.config import settings
from app.db.connection import get_db

router = APIRouter()


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, description="Number of passages to return (capped by RETRIEVE_MAX_TOP_K).")
    collection_ids: Optional[list[int]] = Field(
        default=None, description="Search only these collections (must be inside the key's scope).")
    document_ids: Optional[list[str]] = Field(
        default=None, description="Search only these documents (must be inside the key's scope).")
    document_types: Optional[list[str]] = None
    use_reranker: bool = True
    include_file_urls: bool = Field(
        default=False, description="Add short-lived signed URLs to the source file and images.")


class HitDocument(BaseModel):
    id: Optional[str]
    name: Optional[str]
    collection_id: Optional[int] = None
    version_no: Optional[int] = None
    document_type: Optional[str] = None
    periods: list[str] = Field(default_factory=list)


class HitImage(BaseModel):
    url: Optional[str] = None
    caption: Optional[str] = None
    ocr_text: Optional[str] = None


class Hit(BaseModel):
    rank: int
    score: float
    text: str
    store: str = Field(..., description="vector | table | clause | research | image | graph")
    document: HitDocument
    page: Optional[int] = None
    page_end: Optional[int] = None
    section_title: Optional[str] = None
    table_markdown: Optional[str] = None
    clause_type: Optional[str] = None
    risk_level: Optional[str] = None
    image: Optional[HitImage] = None
    file_url: Optional[str] = None


class RetrieveResponse(BaseModel):
    query: str
    hits: list[Hit]
    total_candidates: int = 0
    stores_searched: list[str] = Field(default_factory=list)
    structured_result: Optional[dict] = None
    timings: Optional[dict] = None
    took_ms: int


def _doc_meta(doc_ids: list[str]) -> dict[str, dict]:
    if not doc_ids:
        return {}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text, original_filename, collection_id, version_no, document_type,
                          COALESCE(doc_metadata->'periods', '[]'::jsonb)
                   FROM document_registry WHERE id::text = ANY(%s)""",
                (doc_ids,),
            )
            return {r[0]: {"name": r[1], "collection_id": r[2], "version_no": r[3],
                           "document_type": r[4], "periods": list(r[5] or [])}
                    for r in cur.fetchall()}


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, principal: Principal = Depends(require_key)):
    start = time.time()
    collections = effective_collections(principal, req.collection_ids)
    allowed = allowed_document_ids(collections, req.document_ids)
    if allowed is None:
        # Admin key with no filter: still pass an explicit list so the engine's
        # scoped path (no unscoped table shortcut) is always used.
        allowed = _all_active_ids()

    if not allowed:
        return RetrieveResponse(query=req.query, hits=[], took_ms=int((time.time() - start) * 1000))

    top_k = min(req.top_k, settings.RETRIEVE_MAX_TOP_K)
    qreq = QueryRequest(
        query=req.query,
        top_k=top_k,
        use_reranker=req.use_reranker,
        document_types=req.document_types,
        synthesize=False,
        allowed_doc_ids=allowed,
    )
    try:
        qres = await query_documents(qreq)
    except HTTPException:
        raise

    meta = _doc_meta(list({c.document_id for c in qres.citations}))
    hits: list[Hit] = []
    for i, c in enumerate(qres.citations[:top_k], start=1):
        m = meta.get(c.document_id, {})
        is_graph = c.store_type == "graph"
        image = None
        if c.image_url or c.caption or c.ocr_text:
            image = HitImage(url=c.image_url if req.include_file_urls else None,
                             caption=c.caption, ocr_text=c.ocr_text)
        hits.append(Hit(
            rank=i,
            score=c.relevance_score,
            text=c.chunk_text,
            store=c.store_type,
            document=HitDocument(
                id=None if is_graph else c.document_id,
                name=m.get("name") or c.filename,
                collection_id=m.get("collection_id"),
                version_no=m.get("version_no"),
                document_type=m.get("document_type"),
                periods=m.get("periods", []),
            ),
            page=c.page_number,
            page_end=c.page_number_end,
            section_title=c.section_title,
            table_markdown=c.table_markdown,
            clause_type=c.clause_type,
            risk_level=c.risk_level,
            image=image,
            file_url=c.pdf_url if req.include_file_urls else None,
        ))

    stats = qres.retrieval_stats or {}
    return RetrieveResponse(
        query=req.query,
        hits=hits,
        total_candidates=int(stats.get("total_retrieved") or 0),
        stores_searched=sorted(stats.get("stores_searched") or []),
        structured_result=qres.structured_result,
        timings=qres.timings,
        took_ms=int((time.time() - start) * 1000),
    )


def _all_active_ids() -> list[str]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM document_registry WHERE is_active AND status = 'completed'")
            return [r[0] for r in cur.fetchall()]
