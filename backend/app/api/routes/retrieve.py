"""Customer retrieval API: question in, ranked passages out. No answer writing.

The customer's own application (and LLM) builds the answer from the hits.

Flow (ported from the Manthan platform's RAVEN node):
  1. Allowed documents = the API key's collections (+ the caller's filters). Fail closed.
  2. Question scope (services/scope/doc_scope.py): a report id, a document name, a period or a
     document type in the question narrows the search to matching documents inside (1).
  3. A short document the question names is returned WHOLE, in page order ("full_document" mode).
     Otherwise the engine runs ranked retrieval (retrieve-only, synthesize=False) inside (2).
  4. Cut-off table JSON is repaired and tables the passages refer to are added next to them.
  5. When a period still leaves several kinds of report, ``scope.choices`` lists them, so the
     calling app can ask its user and send the pick back as ``report_family``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import Principal, allowed_document_ids, effective_collections, require_key
from app.api.routes.query import QueryRequest, query_documents
from app.config import settings
from app.db.connection import get_db
from app.services.scope import doc_scope, passages as P

logger = logging.getLogger(__name__)
router = APIRouter()


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, description="Passages to return in ranked mode (capped by RETRIEVE_MAX_TOP_K).")
    collection_ids: Optional[list[int]] = Field(
        default=None, description="Search only these collections (must be inside the key's scope).")
    document_ids: Optional[list[str]] = Field(
        default=None, description="Search only these documents (must be inside the key's scope).")
    document_types: Optional[list[str]] = Field(
        default=None, description="Engine document types to search (e.g. report, contract).")
    auto_scope: bool = Field(
        default=True, description="Narrow the search to the report id / document name / period / "
                                  "document type the question names.")
    full_document: bool = Field(
        default=True, description="When the question names a short document, return all of it in page order.")
    report_family: Optional[str] = Field(
        default=None, description="The report kind the user picked from an earlier scope.choices list.")
    add_related_tables: bool = Field(
        default=True, description="Add tables the found passages refer to but the search did not return.")
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
    score: Optional[float] = Field(None, description="Relevance score; null for whole-document and added passages.")
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
    added: Optional[str] = Field(None, description="'companion_table' when added next to the passage that refers to it.")


class ScopeChoice(BaseModel):
    label: str
    documents: int


class ScopeInfo(BaseModel):
    applied: list[str] = Field(default_factory=list, description="Filters that narrowed the search.")
    fallbacks: list[str] = Field(default_factory=list, description="Filters that matched no document and were dropped.")
    note: Optional[str] = None
    documents: list[str] = Field(default_factory=list, description="Documents searched after narrowing (max 20).")
    report_family: Optional[str] = None
    choices: list[ScopeChoice] = Field(
        default_factory=list, description="Several kinds of report matched: ask the user, then resend with report_family.")


class RetrieveResponse(BaseModel):
    query: str
    mode: Literal["ranked", "full_document", "empty"]
    hits: list[Hit]
    scope: ScopeInfo = Field(default_factory=ScopeInfo)
    total_candidates: int = 0
    stores_searched: list[str] = Field(default_factory=list)
    structured_result: Optional[dict] = None
    timings: Optional[dict] = None
    took_ms: int


def _all_active_ids() -> list[str]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text FROM document_registry WHERE is_active AND status = 'completed'")
            return [r[0] for r in cur.fetchall()]


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


def _passage_from_citation(c) -> dict:
    return {
        "document_id": c.document_id, "document_name": c.filename,
        "chunk_text": P.unwrap_table_text(c.chunk_text), "store_type": c.store_type,
        "score": c.relevance_score, "page_number": c.page_number, "page_end": c.page_number_end,
        "section_title": c.section_title, "table_markdown": c.table_markdown,
        "clause_type": c.clause_type, "risk_level": c.risk_level,
        "image_url": c.image_url, "caption": c.caption, "ocr_text": c.ocr_text, "file_url": c.pdf_url,
    }


def _hits(passages: list[dict], include_urls: bool) -> list[Hit]:
    meta = _doc_meta(list({p["document_id"] for p in passages if p.get("document_id")}))
    out = []
    for i, p in enumerate(passages, start=1):
        is_graph = p.get("store_type") == "graph"
        m = {} if is_graph else meta.get(p.get("document_id") or "", {})
        image = None
        if p.get("image_url") or p.get("caption") or p.get("ocr_text"):
            image = HitImage(url=p.get("image_url") if include_urls else None,
                             caption=p.get("caption"), ocr_text=p.get("ocr_text"))
        out.append(Hit(
            rank=i, score=p.get("score"), text=p.get("chunk_text") or "", store=p.get("store_type") or "vector",
            document=HitDocument(
                id=None if is_graph else p.get("document_id"),
                name=m.get("name") or p.get("document_name"),
                collection_id=m.get("collection_id"), version_no=m.get("version_no"),
                document_type=m.get("document_type"), periods=m.get("periods", []),
            ),
            page=p.get("page_number"), page_end=p.get("page_end"), section_title=p.get("section_title"),
            table_markdown=p.get("table_markdown"), clause_type=p.get("clause_type"),
            risk_level=p.get("risk_level"), image=image,
            file_url=p.get("file_url") if include_urls else None, added=p.get("added"),
        ))
    return out


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest, principal: Principal = Depends(require_key)):
    start = time.time()

    def _took() -> int:
        return int((time.time() - start) * 1000)

    # 1. Allowed documents (fail closed).
    collections = effective_collections(principal, req.collection_ids)
    allowed = allowed_document_ids(collections, req.document_ids)
    if allowed is None:
        allowed = _all_active_ids()
    if not allowed:
        return RetrieveResponse(query=req.query, mode="empty", hits=[], took_ms=_took())

    # 2. Question scope, always inside the allowed list.
    scope_info = ScopeInfo()
    search_ids = allowed
    named_document = False
    if req.auto_scope:
        res = await asyncio.to_thread(doc_scope.resolve, req.query, allowed, req.report_family)
        if res:
            allowed_set = set(allowed)
            narrowed = [d for d in (res.get("doc_ids") or []) if d in allowed_set]
            scope_info = ScopeInfo(
                applied=list(res.get("applied") or []) if narrowed else [],
                fallbacks=list(res.get("fallbacks") or []),
                note=doc_scope.fallback_note(res) or None,
                documents=list(res.get("documents") or []) if narrowed else [],
                report_family=res.get("family"),
                choices=[ScopeChoice(**c) for c in (res.get("choices") or [])],
            )
            if narrowed:
                search_ids = narrowed
                named_document = any(str(a).startswith("document ") for a in scope_info.applied)

    # 3a. Whole named short document.
    if req.full_document and named_document:
        full = await asyncio.to_thread(P.full_document_passages, search_ids)
        if full:
            return RetrieveResponse(query=req.query, mode="full_document",
                                    hits=_hits(full, req.include_file_urls), scope=scope_info,
                                    total_candidates=len(full),
                                    stores_searched=sorted({p["store_type"] for p in full}), took_ms=_took())

    # 3b. Ranked retrieval inside the scope.
    top_k = min(req.top_k, settings.RETRIEVE_MAX_TOP_K)
    qres = await query_documents(QueryRequest(
        query=req.query, top_k=top_k, use_reranker=req.use_reranker,
        document_types=req.document_types, synthesize=False, allowed_doc_ids=search_ids,
    ))
    passages = [_passage_from_citation(c) for c in qres.citations[:top_k]]

    # 4. Table repair + related tables (same documents only).
    try:
        if passages:
            await asyncio.to_thread(P.repair_table_passages, passages)
            if req.add_related_tables:
                extra = await asyncio.to_thread(P.companion_tables, passages)
                if extra:
                    passages = P.insert_after_callers(passages, extra)
    except Exception as exc:  # post-processing must never fail the request
        logger.warning("passage post-processing failed: %s", exc)

    stats = qres.retrieval_stats or {}
    return RetrieveResponse(
        query=req.query,
        mode="ranked",
        hits=_hits(passages, req.include_file_urls),
        scope=scope_info,
        total_candidates=int(stats.get("total_retrieved") or 0),
        stores_searched=sorted(stats.get("stores_searched") or []),
        structured_result=qres.structured_result,
        timings=qres.timings,
        took_ms=_took(),
    )
