"""
Document picker — the model chooses which document a question is about (13 Nov 2026, "fix once" step 2).

Before this, a stack of rules decided the documents (report id → name words → period → report kind →
type → report family → company name). Every new phrasing slipped past one rule and the fix was another
rule (12 Nov: "the sell-through report" named only 2 of 4 name words → the wrong documents answered).

Now:
  1. Candidates (≤ DOC_PICKER_MAX_CANDIDATES): documents the search ranks highest for the question,
     documents whose name shares words with it, whatever the rules narrowed to, the files ARIA named and
     the report the chat chose earlier — all inside the user's allowed documents.
  2. A short card per candidate: file name, title, type, periods, and the section / table headings
     (what is inside).
  3. One model call picks the document(s), asks the user when two different documents fit equally, or
     says the question is general (search everything).
A report / document id the question spells out still wins without a model call (rules fast path), and
a report the user picked on the "Which report?" card is used as is. Any failure keeps the rules' result.

Ported from the Manthan platform (backend/app/services/doc_picker.py, 13 Nov 2026). Changes: the
candidate search runs in-process (retrieve-only engine query), the model call uses the service's own
LLM client (the customer's endpoint), and the database access uses the engine's connection pool.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from app.db.connection import get_db
from app.services.scope.doc_scope import _has_word, _load_docs, _name_words, _topic_overlap, _words, \
    family_of, periods_in

logger = logging.getLogger(__name__)

from app.config import settings  # noqa: E402

ENABLED = settings.DOC_PICKER_ENABLED
TIMEOUT_SECONDS = settings.DOC_PICKER_TIMEOUT_SECONDS
MAX_CANDIDATES = settings.DOC_PICKER_MAX_CANDIDATES
_SEARCH_K = min(50, settings.DOC_PICKER_SEARCH_K)
_HEADINGS_PER_DOC = 14


# ── candidates ────────────────────────────────────────────────────────────────

def _copy_stem(filename: str) -> str:
    """'Sales_X_Policy.pdf', 'Sales_X_Policy.docx', 'Sales_X_Policy_02.pdf' → one stem (copies of one document)."""
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", (filename or "").lower())
    return re.sub(r"[_\s-]0\d$", "", s)


def name_matches(prompt: str, docs: list, limit: int = 8) -> list:
    """Documents whose name shares distinctive words with the question, best share first."""
    import math
    qwords = set(_words(prompt))
    names = [(d, _name_words(d.get("original_filename") or "")) for d in docs]
    # rare name words weigh more: "through" (few file names) beats "report" / "activity" (many)
    df: dict = {}
    for _d, ws in names:
        for w in set(ws):
            df[w] = df.get(w, 0) + 1
    n = max(1, len(names))
    idf = {w: math.log(1 + n / c) for w, c in df.items()}
    scored = []
    for d, name in names:
        if not name:
            continue
        hit = [w for w in name if _has_word(w, qwords)]
        if hit:
            share = sum(idf[w] for w in hit) / sum(idf[w] for w in name)
            scored.append((share, len(hit), d))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [d for share, hits, d in scored if share >= 0.34][:limit]


def _load_cards(doc_ids: list) -> dict:
    """{doc_id: card fields} for the candidate documents (one query per store)."""
    out: dict = {}
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT id::text, original_filename, coalesce(doc_title, ''), coalesce(document_type, ''),
                       coalesce(doc_metadata -> 'periods', '[]'::jsonb)::text, coalesce(left(doc_summary, 220), '')
                FROM document_registry
                WHERE id = ANY(CAST(%s AS uuid[])) AND is_active AND status = 'completed'""", (doc_ids,))
            for did, fn, title, dtype, periods, summary in cur.fetchall():
                try:
                    per = json.loads(periods)
                except Exception:
                    per = []
                out[did] = {"id": did, "original_filename": fn, "title": title, "type": dtype,
                            "periods": per, "summary": summary, "headings": []}
            cur.execute(f"""
                SELECT document_id::text, table_title, min(page_number) AS p
                FROM table_store
                WHERE document_id = ANY(CAST(%s AS uuid[])) AND coalesce(is_active, true) AND table_title IS NOT NULL
                GROUP BY document_id, table_title ORDER BY p NULLS LAST""", (doc_ids,))
            heads: dict = {}
            for did, title, _p in cur.fetchall():
                heads.setdefault(did, []).append(title)
            cur.execute(f"""
                SELECT document_id::text, substring(chunk_text from '^\\s*Context:\\s*([^\\n]+)') AS h, min(chunk_index) AS i
                FROM vector_store
                WHERE document_id = ANY(CAST(%s AS uuid[]))
                GROUP BY document_id, h ORDER BY i""", (doc_ids,))
            for did, h, _i in cur.fetchall():
                if h and h.strip() and h.strip() not in heads.get(did, []):
                    heads.setdefault(did, []).append(h.strip())
            for did, hs in heads.items():
                if did in out:
                    out[did]["headings"] = [h for h in hs if len(h) <= 90][:_HEADINGS_PER_DOC]
    return out


async def _search_ranked_docs(prompt: str, allowed: list) -> list:
    """Document ids in the order the search ranks their best passage (one retrieve-only query)."""
    from app.api.routes.query import QueryRequest, query_documents
    res = await query_documents(QueryRequest(query=prompt, top_k=_SEARCH_K, synthesize=False,
                                             use_reranker=True, allowed_doc_ids=list(allowed)))
    seen = []
    for c in res.citations:
        d = c.document_id
        if d and d != "knowledge-graph" and d not in seen:
            seen.append(d)
    return seen


# ── the model call ────────────────────────────────────────────────────────────

_SYSTEM = """You choose which documents a question is about, from a list of candidate documents.
Each candidate shows its file name, title, type, the periods it covers and the headings of its sections and tables.

Rules:
- Pick the document(s) the question is about. Usually ONE document; pick several only when the question asks
  to compare or collect across documents, or when the same document exists as several copies (e.g. a .pdf and
  a .docx, or "_02") — then pick all copies.
- A period in the question (Q3 2026, FY2025, March 2026) must match the document's period.
- A name, report number, company or kind of report in the question ("the scorecard", "the sell-through
  report", "the Zhengda agreement") points to the document with that name — words may be shortened or reordered.
- Use the headings: the right document has the section or table the question asks about.
- If the question does not say which document and two or more DIFFERENT documents fit, do not guess: list them
  under "ask". In particular, when the question gives ONLY a period (and figures such as bookings, revenue,
  backlog, targets) and several different kinds of report cover that period, ASK — do not choose between them
  by which one seems to contain more of the figures. Only pick without asking when the question names the
  report, its kind ("scorecard", "forecast", "exception report"), a report number or a subject that only one
  of them covers.
- If the question is a general question that is not about one particular document (any policy or procedure
  could answer it), return empty "pick" and empty "ask" — everything is searched.
- If the chat already settled on a document and the question does not name another one, prefer that document.

Reply with JSON only: {"pick": [numbers], "ask": [numbers], "reason": "<one short sentence>"}"""


def _card_text(n: int, c: dict) -> str:
    parts = [f"[{n}] {c['original_filename']}"]
    if c.get("title"):
        parts.append(f"title: {c['title']}")
    if c.get("type"):
        parts.append(f"type: {c['type']}")
    if c.get("periods"):
        parts.append("periods: " + ", ".join(str(p) for p in c["periods"][:6]))
    if c.get("headings"):
        parts.append("contains: " + "; ".join(c["headings"]))
    return " | ".join(parts)


async def _ask_model(prompt: str, cards: list, chat_pref: Optional[str]) -> Optional[dict]:
    import asyncio
    from app.services import gemma_client
    if not settings.GEMMA4_BASE_URL:
        return None
    user = (f"QUESTION:\n{prompt}\n\n"
            + (f"EARLIER IN THIS CHAT the user settled on: {chat_pref}\n\n" if chat_pref else "")
            + "CANDIDATE DOCUMENTS:\n" + "\n".join(_card_text(i + 1, c) for i, c in enumerate(cards)))
    t0 = time.time()
    txt = await asyncio.wait_for(
        gemma_client.chat_async([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
                                max_tokens=300, temperature=0.0),
        timeout=TIMEOUT_SECONDS)
    logger.info("doc_picker: model answered in %.2fs", time.time() - t0)
    txt = txt or ""
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None

    def _nums(v):
        out = []
        for x in v or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(cards) and i not in out:
                out.append(i)
        return out
    return {"pick": _nums(d.get("pick")), "ask": _nums(d.get("ask")), "reason": str(d.get("reason") or "")[:300]}


# ── entry point ───────────────────────────────────────────────────────────────

_ACROSS = re.compile(
    r"\b(across|between|each of|every|all (?:the|of the|my|our)?\s*|both|several|multiple|these|those)\s+"
    r"(?:\w+\s+){0,2}(reports?|documents?|files?|agreements?|policies|procedures|editions|versions|quarters|periods)\b"
    r"|\b(reports|documents|agreements|policies)\b.{0,40}\b(compare|differ|disagree|conflict)"
    r"|\b(compare|differ|disagree|conflict)\w*\b.{0,60}\b(reports|documents|agreements|policies|versions)\b",
    re.I)


def across_documents(prompt: str) -> bool:
    """Does the question itself ask about several documents ('across the reports', 'both agreements',
    'compare the two policies')? 'compare bookings with targets' does not."""
    return bool(_ACROSS.search(prompt or ""))


def _with_copies(picked: list, all_docs: list, prompt: str = "") -> list:
    """Copies of a picked document (.pdf / .docx / _02) always come along. When the question names NO
    period, the other editions of the same report come along too (13 Nov 2026, quick check M28: "the
    sell-through report" → the model picked the Q1 2025 edition; the answer was in Q1 2026) — the search
    inside them then finds the passage that answers."""
    stems = {_copy_stem(d["original_filename"]) for d in picked}
    fams = {family_of(d) for d in picked} if not periods_in(prompt) else set()
    ids = {d["id"] for d in picked}
    return picked + [d for d in all_docs if d["id"] not in ids
                     and (_copy_stem(d["original_filename"]) in stems or family_of(d) in fams)]


async def pick(prompt: str, allowed: Optional[list], rules: Optional[dict], aria_files: Optional[list] = None,
               chat_pref: Optional[str] = None) -> Optional[dict]:
    """A doc_scope-shaped result chosen by the model, or None (keep the rules' result)."""
    if not allowed or not (prompt or "").strip():
        return None
    import asyncio
    try:
        docs = await asyncio.to_thread(_load_docs, allowed)
        by_id = {d["id"]: d for d in docs}
        order: list = []

        def _add(ids):
            for i in ids:
                if i in by_id and i not in order:
                    order.append(i)
        _add((rules or {}).get("doc_ids") or [])
        aria = {f.lower() for f in aria_files or []}
        _add([d["id"] for d in docs if d["original_filename"].lower() in aria])
        if chat_pref:
            _add([d["id"] for d in docs if _topic_overlap(chat_pref, d)][:4])
        _add([d["id"] for d in name_matches(prompt, docs)])
        try:
            _add(await _search_ranked_docs(prompt, allowed))
        except Exception as e:
            logger.warning("doc_picker: search for candidates failed (%s)", e)
        order = order[:MAX_CANDIDATES]
        if not order:
            return None
        cards_by_id = await asyncio.to_thread(_load_cards, order)
        cards = [cards_by_id[i] for i in order if i in cards_by_id]
        if not cards:
            return None
        ans = await _ask_model(prompt, cards, chat_pref)
    except Exception as e:
        logger.warning("doc_picker: failed (%s) — keeping the rules' choice", e)
        return None
    if ans is None:
        return None

    picked = [by_id[cards[i - 1]["id"]] for i in ans["pick"] if cards[i - 1]["id"] in by_id]
    asked = [by_id[cards[i - 1]["id"]] for i in ans["ask"] if cards[i - 1]["id"] in by_id]
    logger.info("doc_picker: %d candidates → pick=%s ask=%s (%s)", len(cards),
                [d["original_filename"] for d in picked], [d["original_filename"] for d in asked], ans["reason"])
    base = {"fallbacks": [], "picker": {"candidates": [c["original_filename"] for c in cards],
                                         "reason": ans["reason"]}}
    # Several DIFFERENT reports picked for a question that does not ask across reports = the model could
    # not tell which one is meant (13 Nov 2026, quick check H7: "For Q1 2024, compare bookings … with
    # their targets" → four Q1 2024 reports picked, figures mixed). Ask instead. Copies and editions of
    # one report are one family and do not count.
    if picked and len({family_of(d) for d in picked}) >= 2 and not across_documents(prompt):
        asked, picked = picked, []
    if picked:
        picked = _with_copies(picked, docs, prompt)
        labels = list(dict.fromkeys(re.sub(r"\.[a-z0-9]{2,5}$", "", d["original_filename"], flags=re.I)
                                    for d in picked))
        return {**base, "doc_ids": [d["id"] for d in picked],
                "applied": ["document " + ", ".join(labels[:3]) + (" …" if len(labels) > 3 else "")],
                "documents": [d["original_filename"] for d in picked][:20],
                "family": family_of(picked[0]), "choices": []}
    fams = list(dict.fromkeys(family_of(d) for d in asked))
    if len(fams) >= 2:
        return {**base, "doc_ids": None, "applied": [], "documents": [], "family": None,
                "choices": [{"label": f, "documents": sum(1 for d in asked if family_of(d) == f)} for f in fams[:6]]}
    # general question: search everything
    return {**base, "doc_ids": None, "applied": [], "documents": [], "family": None, "choices": []}
