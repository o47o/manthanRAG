"""Passage post-processing after ranked retrieval.

Ported from the Manthan platform's RAVEN node (backend/app/agents/raven_node.py, 12 Nov 2026).
Passages here are plain dicts with at least:
    document_id, document_name, chunk_text, store_type, page_number
All database reads stay inside the document ids they are given; callers pass ids that are
already inside the API key's scope.

- full_document_passages(): a short named document read whole, in page order.
- repair_table_passages(): table JSON cut off by chunking is replaced with the complete table.
- companion_tables(): tables the found passages refer to but the search did not return.
- insert_after_callers(): puts each added table right after the passage that asked for it.
"""
from __future__ import annotations

import json
import logging
import os
import re

from app.db.connection import get_db

logger = logging.getLogger(__name__)

FULL_DOC_MAX_DOCS = int(os.getenv("RETRIEVE_FULL_DOC_MAX_DOCS", "2"))
FULL_DOC_MAX_CHARS = int(os.getenv("RETRIEVE_FULL_DOC_MAX_CHARS", "22000"))
COMPANION_MAX_TABLES = int(os.getenv("RETRIEVE_COMPANION_MAX_TABLES", "3"))
COMPANION_MAX_CHARS = int(os.getenv("RETRIEVE_COMPANION_MAX_CHARS", "9000"))

_SECTION_RE = re.compile(r"^\s*Context:\s*([^\n]+)")
_TITLE_IN_JSON = re.compile(r'"title"\s*:\s*"([^"]+)"')


# ── table text ─────────────────────────────────────────────────────────────

def table_passage(structured, table_title) -> str:
    """A table as JSON with the real section heading as its title."""
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except Exception:
            return (f"{table_title}\n" if table_title else "") + structured
    # Some tables are stored wrapped: {"detected_store": …, "structured_content": {title, headers, rows}}.
    if isinstance(structured, dict) and isinstance(structured.get("structured_content"), dict) \
            and "rows" not in structured:
        structured = structured["structured_content"]
    if isinstance(structured, dict):
        if table_title and structured.get("title") and structured.get("title") != table_title:
            structured = {**structured, "summary_title": structured.get("title"), "title": table_title}
        elif table_title and not structured.get("title"):
            structured = {**structured, "title": table_title}
        return json.dumps(structured, ensure_ascii=False)
    return ""


def unwrap_table_text(text):
    """Engine table passages can carry the stored wrapper too — unwrap them the same way."""
    s = (text or "").strip() if isinstance(text, str) else text
    if not isinstance(s, str) or '"structured_content"' not in s[:200]:
        return text
    head, _, rest = s.partition("{")
    try:
        obj = json.loads("{" + rest)
    except Exception:
        return text
    return table_passage(obj, head.strip() or None)


def _parses(text: str) -> bool:
    s = (text or "").strip()
    if "\n{" in s[:300] and not s.startswith("{"):
        s = s[s.index("\n{") + 1:]
    if not s.startswith("{"):
        return True
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def _base_title(title: str) -> str:
    return re.split(r"\s+[-–—]\s+", (title or "").strip().lower())[0].strip()


# ── whole document ─────────────────────────────────────────────────────────

def full_document_passages(doc_ids: list) -> list | None:
    """Every text passage, clause, research chunk and table of the documents, by page.
    None when there are too many documents, the text is too long, or on error."""
    if not doc_ids or len(doc_ids) > FULL_DOC_MAX_DOCS:
        return None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT d.id::text, d.original_filename, v.page_number, coalesce(v.chunk_index, 0), 0,
                           v.chunk_text, NULL, NULL
                    FROM vector_store v JOIN document_registry d ON d.id = v.document_id
                    WHERE d.id = ANY(CAST(%s AS uuid[])) AND d.is_active AND d.status = 'completed'
                    UNION ALL
                    SELECT d.id::text, d.original_filename, t.page_number, coalesce(t.table_index, 0), 1, NULL,
                           coalesce(t.json_data::text, t.structured_content::text), t.table_title
                    FROM table_store t JOIN document_registry d ON d.id = t.document_id
                    WHERE d.id = ANY(CAST(%s AS uuid[])) AND d.is_active AND d.status = 'completed'
                      AND coalesce(t.is_active, true)
                    UNION ALL
                    SELECT d.id::text, d.original_filename, c.page_number, coalesce(c.clause_index, 0), 2,
                           concat_ws(E'\\n', nullif(concat_ws(' ', c.clause_number, c.clause_title), ''), c.clause_text),
                           NULL, NULL
                    FROM clause_store c JOIN document_registry d ON d.id = c.document_id
                    WHERE d.id = ANY(CAST(%s AS uuid[])) AND d.is_active AND d.status = 'completed'
                      AND coalesce(c.is_active, true)
                    UNION ALL
                    SELECT d.id::text, d.original_filename, s.page_number, coalesce(s.chunk_index, 0), 3,
                           s.chunk_text, NULL, NULL
                    FROM document_store s JOIN document_registry d ON d.id = s.document_id
                    WHERE d.id = ANY(CAST(%s AS uuid[])) AND d.is_active AND d.status = 'completed'
                      AND coalesce(s.is_active, true)""", (list(doc_ids),) * 4)
                rows = cur.fetchall()
    except Exception as e:
        logger.warning("full-document read failed (%s) — ranked retrieval instead", e)
        return None
    rows.sort(key=lambda r: (r[1] or "", r[2] if r[2] is not None else 10_000, r[4], r[3]))
    out, total = [], 0
    for did, name, page, _idx, kind, text, structured, title in rows:
        body = table_passage(structured, title) if kind == 1 else (text or "")
        if not body.strip():
            continue
        total += len(body)
        if total > FULL_DOC_MAX_CHARS:
            return None
        out.append({"document_id": did, "document_name": name, "page_number": page, "chunk_text": body,
                    "score": None, "section_title": title if kind == 1 else None,
                    "store_type": {1: "table", 2: "clause", 3: "research"}.get(kind, "vector")})
    return out or None


# ── repair cut-off tables ──────────────────────────────────────────────────

def repair_table_passages(passages: list) -> int:
    """Replace cut-off table JSON with the complete table of the same document and title.
    Changes passages in place; returns how many were replaced."""
    broken = []
    for i, p in enumerate(passages):
        txt = str(p.get("chunk_text") or "")
        if (txt.lstrip().startswith("{") or "\n{" in txt[:300]) and not _parses(txt):
            m = re.match(r"([^\n{]+)\n\{", txt) or _TITLE_IN_JSON.search(txt)
            if m and p.get("document_id"):
                broken.append((i, p["document_id"], m.group(1).strip().lower()))
    if not broken:
        return 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.document_id::text, t.table_title, t.json_data::text
                FROM table_store t
                WHERE t.document_id = ANY(CAST(%s AS uuid[])) AND t.json_data IS NOT NULL
                  AND coalesce(t.is_active, true)""", (list({b[1] for b in broken}),))
            rows = cur.fetchall()
    by_key, titles = {}, {}
    for did, title, jd in rows:
        key = (did, (title or "").strip().lower())
        by_key.setdefault(key, jd)
        titles.setdefault(key, title)
    n = 0
    for i, did, low in broken:
        jd = by_key.get((did, low))
        if jd:
            passages[i]["chunk_text"] = table_passage(jd, titles.get((did, low)) or low)
            n += 1
    return n


# ── companion tables ───────────────────────────────────────────────────────

def companion_tables(passages: list) -> list:
    """Tables the found passages need that the search did not return, from the same documents only:
    the table of a section whose TEXT was found ('Context: Product detail - Q2 2026'), and other
    tables with the same name as a table that was found."""
    want: dict = {}
    for rank, p in enumerate(passages):
        did = p.get("document_id")
        if not did or p.get("store_type") == "graph":
            continue
        txt = str(p.get("chunk_text") or "")
        if p.get("store_type") == "table":
            m = re.match(r"([^\n{]+)\n\{", txt) or _TITLE_IN_JSON.search(txt)
            if m:
                want.setdefault((did, "base:" + _base_title(m.group(1))), rank)
            continue
        m = _SECTION_RE.match(txt)
        if m:
            want.setdefault((did, m.group(1).strip().lower()), rank)
    if not want:
        return []
    have = " ".join(str(p.get("chunk_text") or "")[:300].lower() for p in passages
                    if p.get("store_type") == "table" or str(p.get("chunk_text") or "").lstrip().startswith("{"))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.original_filename, t.page_number, t.table_title,
                       coalesce(t.json_data::text, t.structured_content::text), t.document_id::text
                FROM table_store t JOIN document_registry d ON d.id = t.document_id
                WHERE t.document_id = ANY(CAST(%s AS uuid[])) AND d.is_active AND d.status = 'completed'
                  AND coalesce(t.is_active, true)
                ORDER BY t.page_number NULLS LAST, t.table_index""", (list({k[0] for k in want}),))
            rows = cur.fetchall()
    picks = []
    for name, page, title, structured, did in rows:
        t = (title or "").strip().lower()
        if not t or t in have:
            continue
        rank = want.get((did, t))
        if rank is None:
            rank = want.get((did, "base:" + _base_title(t)))
        if rank is None:
            continue
        picks.append((rank, page if page is not None else 10_000, did, name, page, title, structured))
    picks.sort(key=lambda x: (x[0], x[1]))
    out, total = [], 0
    for rank, _p, did, name, page, title, structured in picks:
        body = table_passage(structured, title)
        if not body or total + len(body) > COMPANION_MAX_CHARS:
            continue
        total += len(body)
        out.append({"document_id": did, "document_name": name, "page_number": page, "chunk_text": body,
                    "score": None, "store_type": "table", "section_title": title,
                    "added": "companion_table", "_after": rank})
        if len(out) >= COMPANION_MAX_TABLES:
            break
    return out


def insert_after_callers(passages: list, extra: list) -> list:
    """Each added table goes right after the passage that called for it."""
    out = list(passages)
    for e in sorted(extra, key=lambda x: x.get("_after", len(out)), reverse=True):
        pos = min(int(e.pop("_after", len(out))) + 1, len(out))
        out.insert(pos, e)
    return out
