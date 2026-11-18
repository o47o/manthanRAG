"""
Agentic Pipeline — bounded RAVEN + Hybrid + SPYDER loop (Feature 1.1).

Entry point:
    run(query, document_types, document_id, top_k, use_reranker, on_stage)
        -> tuple[list[RetrievedChunk], dict]

Loop control (per plan spec):
    1. RAVEN reframes the query (if RAVEN_ENABLED).
    2. Loop {
           a. hybrid_retrieve on working_query + fan-out over sub_queries, dedup by chunk_id.
           b. graph_expanded_chunks (best-effort, wrapped in try/except).
           c. _rank_chunks → cross-encoder or RRF.
           d. If SPYDER disabled OR loops >= AGENTIC_MAX_LOOPS → break.
           e. SPYDER judges sufficiency:
              - sufficient=True → break
              - confidence >= SPYDER_MIN_CONFIDENCE → break
              - reframed_query is None → break
              - otherwise: working_query = reframed_query, loop.
       }
    3. Returns (final_chunks, agentic_stats).

on_stage(stage_name, detail_dict) is an optional async callback fired before
each phase so the SSE layer can emit progress events to the frontend.

agentic_stats shape:
    {
        "raven": {"reframed": str, "sub_queries": [...], "store_hint": ..., "used_fallback": bool},
        "loops": int,
        "hybrid": bool,
    }
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable

from app.config import settings
from app.services.agents import raven_agent, spyder_agent
from app.services import hybrid_search_service, retriever_service
from app.api.routes.query import _rank_chunks, _chunk_preview_for_stream  # reuse exactly

logger = logging.getLogger(__name__)



def _with_table_store(store_hint, table_intent: str):
    """Make sure a table-shaped question also searches the table store — without dropping the
    text stores. None = no restriction (every store is searched already), so it stays None.
    A hint that named stores keeps them and gains 'table' plus 'vector' (document text); its
    doc-type filter is cleared so the text of non-financial documents is not filtered out."""
    if store_hint is None:
        return store_hint
    named = list(store_hint.get("stores") or [])
    if table_intent == "semantic_qa":
        # 9 Nov 2026: RAVEN's own hint can also be "tables only" ("cancellations and book-to-bill
        # for FY2025" → stores=['table']). A report that states those figures in its TEXT then
        # returns nothing. Never let a hint shut out the document text.
        if named and "vector" not in named:
            return {**store_hint, "stores": named + ["vector"], "doc_types": None}
        return store_hint
    stores = list(dict.fromkeys(named + ["table", "vector"]))
    return {**store_hint, "stores": stores, "doc_types": None}


async def run(
    query: str,
    document_types: Optional[list] = None,
    document_id: Optional[str] = None,
    top_k: int = 5,
    use_reranker: bool = True,
    on_stage: Optional[Callable[[str, dict], Awaitable[None]]] = None,
    table_filters=None,
    enable_hybrid: Optional[bool] = None,
    enable_graphrag: Optional[bool] = None,
    allow_global: bool = True,
) -> tuple:
    """Run the agentic retrieve → judge → refine loop.

    Returns (final_chunks: list[RetrievedChunk], agentic_stats: dict).
    """

    async def _emit(stage: str, detail: dict = None) -> None:
        if on_stage is not None:
            try:
                await on_stage(stage, detail or {})
            except Exception as exc:
                logger.warning("on_stage callback error (%s): %s", stage, exc)

    # ── Phase 0: GraphRAG Global Check ────────────────────────────────────────
    use_graphrag = enable_graphrag if enable_graphrag is not None else settings.GRAPHRAG_ENABLED
    graph_mode = "none"

    # document_id may be a single doc id (document chat — graph stays off, as
    # before) or a security allowlist (list of ids — graph allowed, retrieval
    # and hydration are scope-filtered via _doc_filter's list handling).
    single_doc = isinstance(document_id, str) and bool(document_id)

    if use_graphrag and not single_doc:
        try:
            from app.services import graph_service
            if graph_service.is_available():
                from app.services.graphrag_retriever import route_graphrag
                graph_mode = await asyncio.to_thread(route_graphrag, query)
                if graph_mode == "global" and not allow_global:
                    # Retrieve-only callers read citations; global returns none.
                    graph_mode = "local"
                logger.info("STAGE agentic-graphrag-route: mode=%s", graph_mode)
                await _emit("graphrag_route", {"mode": graph_mode})

                if graph_mode == "global":
                    from app.services.graphrag_retriever import global_search
                    _t = asyncio.get_running_loop().time()
                    global_answer = await global_search(query, max_communities=settings.GRAPHRAG_GLOBAL_MAX_COMMUNITIES)
                    return [], {
                        "loops": 0,
                        "graph_mode": "global",
                        "global_answer": global_answer
                    }
        except Exception as exc:
            logger.warning("Agentic GraphRAG routing failed: %s", exc)
            graph_mode = "none"

    # ── Phase 1: RAVEN reframing ──────────────────────────────────────────────
    await _emit("raven", {"query": query})

    raven_result = await raven_agent.reframe(query)
    working_query = raven_result["reframed"] or query
    # Cap the fan-out: each sub-query costs a full hybrid retrieval (embed +
    # 8 threads + 8 pooled connections) — the memory-diet budget allows 2.
    sub_queries: list = (raven_result.get("sub_queries") or [])[:2]
    store_hint: Optional[dict] = raven_result.get("store_hint")
    # Deterministic table-shape detection wins over a noisy LLM store hint for
    # entity attribute questions such as "HDFC Bank is what sector?". This keeps
    # the agentic path aligned with the structured-query shortcut and ensures
    # table_store is searched even when RAVEN guessed a generic document store.
    # 6 Nov 2026: the override ADDS the table store instead of replacing every other store.
    # Replacing made questions like "records in the supplied reports where …" or "what does the
    # SOP require … and where should an exception go" search tables only, so the document TEXT
    # holding the answer was never searched. Tables are still searched (entity lookups keep
    # working); the reranker decides between table and text passages.
    try:
        from app.services.table_intent_classifier import classify_table_intent
        store_hint = _with_table_store(store_hint, classify_table_intent(query))
    except Exception as exc:
        logger.debug("Table intent override unavailable: %s", exc)
    await _emit("raven_result", {
        "query": query,
        "reframed": working_query,
        "sub_queries": sub_queries,
        "used_fallback": raven_result.get("used_fallback", False),
    })

    logger.info(
        "RAVEN: reframed=%r sub_queries=%d used_fallback=%s",
        working_query[:80], len(sub_queries), raven_result.get("used_fallback"),
    )

    use_hybrid = enable_hybrid if enable_hybrid is not None else settings.HYBRID_SEARCH_ENABLED
    agentic_stats = {
        "raven": raven_result,
        "loops": 0,
        "hybrid": use_hybrid,
        "graph_mode": graph_mode,
    }

    final_chunks: list = []
    loops = 0
    max_loops = settings.AGENTIC_MAX_LOOPS
    graph_expanded_total = 0

    # ── Bounded retrieval loop ────────────────────────────────────────────────
    while True:
        loops += 1
        await _emit("hybrid", {"loop": loops, "query": working_query})

        # Build the set of all queries to fan out: primary + sub_queries
        all_queries = [working_query]
        if sub_queries:
            all_queries += sub_queries

        # Fan out hybrid retrieve for each query (sync, offloaded to thread pool)
        seen_ids: set = set()
        merged: list = []

        for q in all_queries:
            try:
                if use_hybrid:
                    chunks = await asyncio.to_thread(
                        hybrid_search_service.hybrid_retrieve,
                        query=q,
                        document_types=document_types,
                        document_id=document_id,
                        top_k_per_store=15,
                        intent=store_hint,
                        table_filters=table_filters,
                    )
                else:
                    # Request-level hybrid override: semantic-only, ONCE. (This
                    # branch previously ran hybrid_retrieve first and threw the
                    # result away — double retrieval cost for nothing.)
                    from app.services.retriever_service import retrieve
                    chunks = await asyncio.to_thread(
                        retrieve, q, document_types, document_id, 15, True, store_hint, table_filters
                    )
                for c in chunks:
                    if c.chunk_id not in seen_ids:
                        seen_ids.add(c.chunk_id)
                        merged.append(c)
            except Exception as exc:
                logger.warning("hybrid_retrieve failed for query %r: %s", q[:60], exc)

        # Graph expansion (best-effort) — ONLY for entity-based queries. When
        # route_graphrag() classified the query as "local" it named an entity that
        # exists in the graph; only then do we walk the graph. Previously
        # graph_expanded_chunks() ran unconditionally (even for graph_mode == "none"),
        # so ordinary non-entity questions still received graph-sourced chunks — that
        # is exactly what we no longer want.
        if merged and not single_doc and graph_mode == "local":
            try:
                local_chunks = await asyncio.to_thread(
                    retriever_service.graphrag_local_chunks,
                    working_query, document_types, document_id,
                )
                for c in local_chunks:
                    if c.chunk_id not in seen_ids:
                        seen_ids.add(c.chunk_id)
                        merged.append(c)
                        graph_expanded_total += 1
            except Exception as exc:
                logger.warning("Agentic GraphRAG local expansion failed: %s", exc)

            # Legacy multi-hop document expansion: per related doc it runs Gemma
            # NER on mined chunk text plus a FULL retrieve() — the single biggest
            # thread/memory multiplier in the old loop. Config-gated off in the
            # memory diet (MULTI_HOP_MAX_RELATED_DOCS=0 skips the call outright).
            if settings.MULTI_HOP_MAX_RELATED_DOCS > 0:
                try:
                    extra = await asyncio.to_thread(
                        retriever_service.graph_expanded_chunks,
                        working_query, merged,
                        document_types=document_types,
                    )
                    for c in extra:
                        if c.chunk_id not in seen_ids:
                            seen_ids.add(c.chunk_id)
                            merged.append(c)
                            graph_expanded_total += 1
                except Exception as exc:
                    logger.warning("Graph expansion failed (non-fatal): %s", exc)

        # Cross-encoder rerank (or RRF fallback) — sync, offloaded
        await _emit("retrieved", {
            "loop": loops,
            "chunks": [_chunk_preview_for_stream(c) for c in merged[:30]],
        })
        if merged:
            try:
                final_chunks, _pool_size = await asyncio.to_thread(
                    _rank_chunks, working_query, merged, top_k, use_reranker
                )
            except Exception as exc:
                logger.warning("_rank_chunks failed: %s", exc)
                final_chunks = merged[:top_k]
        else:
            final_chunks = []

        agentic_stats["loops"] = loops
        agentic_stats["graph_expanded"] = graph_expanded_total
        await _emit("selected", {
            "loop": loops,
            "chunks": [_chunk_preview_for_stream(c) for c in final_chunks],
        })

        # ── SPYDER sufficiency check ──────────────────────────────────────────
        if not settings.SPYDER_ENABLED:
            logger.debug("SPYDER disabled — stopping after loop %d", loops)
            break

        if loops >= max_loops:
            logger.info("AGENTIC_MAX_LOOPS=%d reached — stopping", max_loops)
            break

        await _emit("spyder", {"loop": loops, "chunks": len(final_chunks)})

        spyder_result = await spyder_agent.judge(working_query, final_chunks)
        logger.info(
            "SPYDER loop=%d: sufficient=%s confidence=%.2f",
            loops, spyder_result["sufficient"], spyder_result["confidence"],
        )

        # Stop conditions (any one is sufficient)
        if spyder_result["sufficient"]:
            break
        if spyder_result["confidence"] >= settings.SPYDER_MIN_CONFIDENCE:
            break
        if not spyder_result.get("reframed_query"):
            break

        # Refine and loop
        working_query = spyder_result["reframed_query"]
        sub_queries = []  # sub_queries only used on the first pass (RAVEN output)
        logger.info("SPYDER refinement: new query=%r", working_query[:80])

    return final_chunks, agentic_stats
