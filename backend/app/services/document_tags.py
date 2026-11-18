"""
Document period tags (Nov 2026) — which periods a document covers, for the backend's hard
filter ("Q2 2026 revenue versus plan" searches only documents tagged 2026-Q2).

Written to document_registry.doc_metadata as {"periods": [...], "periods_from": {...}} after a
document completes (record_extraction_service.run_after_completion) and by
scripts/backfill_document_tags.py. No LLM call.

Sources, strongest first:
  * file name and document title            (e.g. "..._Report_May_2026.pdf" -> 2026-05, 2026-Q2)
  * section headings and table titles       ("Variance against plan - Q2 2026" -> 2026-Q2)
  * the text, only periods it repeats       (count >= 2 and >= 30 % of the most repeated one),
    so "FY2025" in an exception report counts but a passing "Q2 2022" in a trend sentence does not.

Normalised forms: "2026-Q2", "2026-H1", "FY2025", "2026-05". A month also tags its quarter. Year
alone ("2025") is not a period here — too common to filter on. The backend uses the SAME
normaliser (backend/app/services/doc_scope.py) — keep the two in step.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"((?:19|20)\d{2})"
_PATTERNS = [
    (re.compile(rf"\bq([1-4])[\s_\-/]*(?:fy[\s_\-]?)?{_YEAR}\b", re.I), lambda m: [f"{m[2]}-Q{m[1]}"]),
    (re.compile(rf"\b{_YEAR}[\s_\-/]*q([1-4])\b", re.I), lambda m: [f"{m[1]}-Q{m[2]}"]),
    (re.compile(rf"\bh([12])[\s_\-/]*{_YEAR}\b", re.I), lambda m: [f"{m[2]}-H{m[1]}"]),
    (re.compile(rf"\bfy[\s_\-]?{_YEAR}\b", re.I), lambda m: [f"FY{m[1]}"]),
    (re.compile(r"\bfy[\s_\-]?(\d{2})\b", re.I), lambda m: [f"FY20{m[1]}"]),
    (re.compile(rf"\b{_MONTH_RE}[\s_\-,]*{_YEAR}\b", re.I), lambda m: _month(m[1], m[2])),
]


def _month(name: str, year: str) -> list:
    n = _MONTHS.get(name.lower()[:3])
    if not n:
        return []
    return [f"{year}-{n:02d}", f"{year}-Q{(n - 1) // 3 + 1}"]


def periods_in(text: str) -> list:
    """Every period expression in the text, normalised, in order of appearance (with repeats)."""
    out = []
    t = (text or "").replace("_", " ")
    for rx, fn in _PATTERNS:
        for m in rx.finditer(t):
            out += fn(m)
    return out


def compute_periods(names: list, headings: list, body: list) -> dict:
    strong = []
    for s in list(names) + list(headings):
        strong += periods_in(s)
    counts = Counter()
    for s in body:
        counts.update(periods_in(s))
    repeated = []
    if counts:
        top = counts.most_common(1)[0][1]
        repeated = [p for p, c in counts.items() if c >= 2 and c >= 0.3 * top]
    periods = list(dict.fromkeys(strong + sorted(repeated)))
    return {"periods": periods,
            "periods_from": {"names_headings": sorted(set(strong)), "repeated_in_text": sorted(repeated)}}


def tag_document(document_id: str) -> dict:
    from app.db.connection import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT original_filename, coalesce(doc_title,'') FROM document_registry WHERE id = %s",
                        (document_id,))
            row = cur.fetchone()
            if not row:
                return {}
            cur.execute("SELECT DISTINCT coalesce(section_title,'') FROM vector_store WHERE document_id = %s",
                        (document_id,))
            headings = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute("SELECT coalesce(table_title,'') FROM table_store WHERE document_id = %s", (document_id,))
            headings += [r[0] for r in cur.fetchall() if r[0]]
            cur.execute("SELECT chunk_text FROM vector_store WHERE document_id = %s", (document_id,))
            body = [r[0] for r in cur.fetchall() if r[0]]
            tags = compute_periods([row[0], row[1]], headings, body)
            cur.execute("UPDATE document_registry SET doc_metadata = coalesce(doc_metadata, '{}'::jsonb) || %s::jsonb "
                        "WHERE id = %s", (json.dumps(tags), document_id))
    return tags
