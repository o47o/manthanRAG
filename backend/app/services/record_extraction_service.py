"""
Document records (Nov 2026, migration 013) — the facts written inside documents.

After a document finishes loading, this reads its stored passages (vector_store text +
table_store tables), keeps the ones that look like they describe a business record (an
ID-like token plus a date or an amount), and asks Gemma to list the records they describe:

    {"p": 3, "record_type": "Sales order", "record_id": "SO-2025-11134",
     "fields": {"booked_date": "2025-06-20", "requested_ship_date": "2025-02-16",
                "customer": "Prabhat Systems Electronics Pvt. Ltd."},
     "source_text": "Order SO-2025-11134 from … was booked on 2025-06-20 …", "confidence": 0.9}

Record types and field names are DYNAMIC: a core dictionary is suggested, the names already
used in the same domain are offered for reuse, anything else is kept (snake_case).

Grounding (no invented facts): a record is kept only when its record_id appears in the
passage, and a field only when its value (or its ISO date) appears in the passage. Values
are stored as written plus a normalised date / number / unit for comparisons:

    fields = {"booked_date": {"value": "20 June 2025", "date": "2025-06-20"},
              "amount": {"value": "USD 1,250.00", "number": 1250.0, "unit": "USD"}}

The backend's check_records capability compares these values with plain SQL — the step
semantic search cannot do ("requested ship date earlier than the booking date").

Writes are delete-then-insert per document (reprocess replaces records). If every Gemma
call fails, existing records are left alone. Never raises into the ingestion pipeline —
call `run_after_completion()`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# ── Candidate filter ──────────────────────────────────────────────────────────

# ID-like token: letters + digits, e.g. SO-2025-11134, INV-2026-57326, PO 450012, CASE-0042
_ID_RE = re.compile(r"\b[A-Z]{2,6}[-_/ ]?\d{3,}(?:[-_/][A-Z0-9]{2,})*\b")
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE_RE = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"                          # 2025-06-20
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"              # 20/06/2025, 06-20-25
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS},?\s+\d{{4}}\b"   # 20 June 2025
    rf"|\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",  # June 20, 2025
    re.IGNORECASE,
)
_CUR = r"(?:USD|EUR|INR|GBP|AUD|CAD|SGD|JPY|AED|CHF|Rs\.?|\$|€|£|₹)"
_AMOUNT_RE = re.compile(rf"{_CUR}\s?-?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|INR|GBP)\b")
_PLACEHOLDER_RE = re.compile(r"X{3,}|#{3,}|0{6,}|N{3,}", re.IGNORECASE)   # SO-XXXX template ids


def is_candidate(text: str) -> bool:
    """A passage worth reading: an ID-like token plus a date or an amount."""
    t = text or ""
    return bool(_ID_RE.search(t)) and bool(_DATE_RE.search(t) or _AMOUNT_RE.search(t))


# ── Normalisation ─────────────────────────────────────────────────────────────

def norm_key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return k[:60]


def norm_type_key(name: str) -> str:
    k = norm_key(name)
    if k.endswith("ies") and len(k) > 4:
        k = k[:-3] + "y"
    elif k.endswith("s") and not k.endswith("ss") and len(k) > 3:
        k = k[:-1]
    return k


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


_CUR_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "rs": "INR", "rs.": "INR"}
_NUM_VALUE_RE = re.compile(
    r"^\s*(?P<pre>[A-Z]{3}|[$€£₹]|Rs\.?)?\s*(?P<num>-?\d[\d,]*(?:\.\d+)?)\s*(?P<post>%|[A-Za-z]{1,12})?\s*$"
)


def parse_date(value: str) -> Optional[str]:
    """ISO date for a value that looks like a date, else None. Day-first for d/m/y."""
    v = (value or "").strip()
    if not v or not _DATE_RE.search(v):
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    try:
        from dateutil import parser as _dp
        return _dp.parse(_DATE_RE.search(v).group(0), dayfirst=True, fuzzy=True).date().isoformat()
    except Exception:
        return None


def parse_number(value: str) -> tuple[Optional[float], Optional[str]]:
    m = _NUM_VALUE_RE.match(value or "")
    if not m:
        return None, None
    try:
        n = float(m["num"].replace(",", ""))
    except ValueError:
        return None, None
    unit = m["pre"] or m["post"]
    if unit:
        unit = _CUR_CODE.get(unit.lower(), unit.upper() if len(unit) == 3 and unit.isalpha() else unit)
    return n, unit


def normalise_value(value) -> dict:
    raw = str(value).strip() if value is not None else ""
    out: dict = {"value": raw[:300]}
    iso = parse_date(raw)
    if iso:
        out["date"] = iso
        return out
    n, unit = parse_number(raw)
    if n is not None:
        out["number"] = n
        if unit:
            out["unit"] = unit
    return out


def _in_text(raw: str, iso: Optional[str], passage_sq: str) -> bool:
    if raw and _squash(raw) in passage_sq:
        return True
    if iso and iso in passage_sq:
        return True
    return False


# ── Prompt ────────────────────────────────────────────────────────────────────

CORE_FIELDS = (
    "booked_date (order / booking date), requested_ship_date, promised_date, ship_date, "
    "delivery_date, due_date, invoice_date, payment_date, approved_date, start_date, end_date, "
    "amount, currency, quantity, unit_price, status, customer, supplier, owner, location"
)

_SYSTEM = (
    "You read passages from business documents and list the RECORDS they describe. A record is "
    "one identifiable business item — an order, shipment, invoice, purchase order, payment, "
    "case, ticket, contract, batch, delivery — that has an ID written in the text AND at least "
    "one stated value (a date, amount, quantity, status, party).\n"
    "Rules:\n"
    "1. record_id is copied EXACTLY from the text. No ID in the text = not a record.\n"
    "2. Every field value is copied EXACTLY as written (do not reformat dates or numbers).\n"
    "3. Field names: use these when the meaning fits: " + CORE_FIELDS + ". Otherwise reuse a "
    "name from the KNOWN FIELDS list, otherwise invent a short snake_case name.\n"
    "4. record_type: a short noun such as 'Sales order', 'Shipment', 'Invoice'; reuse a KNOWN "
    "TYPE when it fits.\n"
    "5. Skip totals, KPIs, averages, targets, policy statements and template placeholders "
    "(e.g. SO-XXXX). Skip report / document ids that only name the document itself.\n"
    "6. source_text = the sentence(s) or table row the record was read from, copied verbatim.\n"
    "7. confidence 0-1 = how sure you are the values belong to that record.\n"
    "Answer ONLY with a JSON array (empty array when there are no records):\n"
    '[{"p": <passage number>, "record_type": "...", "record_id": "...", '
    '"fields": {"<name>": "<value as written>"}, "source_text": "...", "confidence": 0.9}]'
)


def _user_msg(batch: list[dict], catalogue: dict) -> str:
    parts = []
    if catalogue.get("types"):
        parts.append("KNOWN TYPES: " + ", ".join(catalogue["types"][:30]))
    if catalogue.get("fields"):
        parts.append("KNOWN FIELDS: " + ", ".join(catalogue["fields"][:60]))
    for i, p in enumerate(batch, 1):
        where = f" (page {p['page']})" if p.get("page") else ""
        parts.append(f"[P{i}]{where}\n{p['text']}")
    return "\n\n".join(parts)


def _parse_json_array(raw: str) -> list:
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        v = json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, re.DOTALL)
        if not m:
            return []
        try:
            v = json.loads(m.group(0))
        except Exception:
            return []
    if isinstance(v, dict):
        v = v.get("records") or []
    return v if isinstance(v, list) else []


def ground_records(items: list, batch: list[dict]) -> list[dict]:
    """Keep only what the passage actually says (record_id and each value present)."""
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            idx = int(str(it.get("p", "")).lstrip("Pp")) - 1
        except ValueError:
            continue
        if not 0 <= idx < len(batch):
            continue
        p = batch[idx]
        psq = p.get("_sq") or _squash(p["text"])
        rid = str(it.get("record_id") or "").strip()
        rtype = str(it.get("record_type") or "").strip()
        if not rid or not rtype or _PLACEHOLDER_RE.search(rid) or _squash(rid) not in psq:
            continue
        fields = {}
        for name, val in (it.get("fields") or {}).items():
            key = norm_key(name)
            if not key or val is None or isinstance(val, (dict, list)):
                continue
            nv = normalise_value(val)
            if not nv["value"] or not _in_text(nv["value"], nv.get("date"), psq):
                continue
            fields.setdefault(key, nv)
        if not fields:
            continue
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence", 0.7))))
        except (TypeError, ValueError):
            conf = 0.7
        out.append({
            "chunk_id": p.get("chunk_id"), "page_number": p.get("page"),
            "record_type": rtype[:80], "record_type_key": norm_type_key(rtype) or "record",
            "record_id": rid[:120], "fields": fields,
            "source_text": str(it.get("source_text") or "")[:1500] or None,
            "confidence": conf,
        })
    return out


def dedupe(records: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in records:
        k = (r["record_type_key"], r["record_id"].lower(),
             json.dumps(r["fields"], sort_keys=True, default=str))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def batches(passages: list[dict], max_chars: int) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for p in passages:
        n = len(p["text"])
        if cur and size + n > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        out.append(cur)
    return out


def _split_table(md: str, limit: int = 3000) -> list[str]:
    """Large markdown tables → row windows that each keep the header lines."""
    lines = (md or "").splitlines()
    if len(md or "") <= limit or len(lines) < 4:
        return [md or ""]
    head, body = lines[:2], lines[2:]
    out, cur = [], []
    for ln in body:
        if cur and sum(len(x) for x in head + cur + [ln]) > limit:
            out.append("\n".join(head + cur))
            cur = []
        cur.append(ln)
    if cur:
        out.append("\n".join(head + cur))
    return out


# ── DB ────────────────────────────────────────────────────────────────────────

def _load_passages(document_id: str) -> list[dict]:
    from app.db.connection import get_db
    out = []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id::text, page_number, chunk_text FROM vector_store "
                        "WHERE document_id = %s ORDER BY chunk_index NULLS LAST", (document_id,))
            for cid, page, text in cur.fetchall():
                if text:
                    out.append({"chunk_id": cid, "page": page, "text": text[:4000]})
            cur.execute("SELECT id::text, page_number, coalesce(markdown_text, raw_text) FROM table_store "
                        "WHERE document_id = %s AND coalesce(is_active, true) ORDER BY table_index", (document_id,))
            for tid, page, md in cur.fetchall():
                for part in _split_table(md or ""):
                    if part.strip():
                        out.append({"chunk_id": tid, "page": page, "text": part})
    return out


def _catalogue_for(document_id: str) -> dict:
    """Record types + field names already used in this document's domain (for reuse)."""
    from app.db.connection import get_db
    types, fields = [], []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT min(r.record_type), count(*) FROM document_records r "
                    "JOIN document_registry d ON d.id = r.document_id "
                    "WHERE d.is_active AND d.domain_id IS NOT DISTINCT FROM "
                    "  (SELECT domain_id FROM document_registry WHERE id = %s) "
                    "GROUP BY r.record_type_key ORDER BY 2 DESC LIMIT 30", (document_id,))
                types = [r[0] for r in cur.fetchall()]
                cur.execute(
                    "SELECT k, count(*) FROM document_records r "
                    "JOIN document_registry d ON d.id = r.document_id "
                    "CROSS JOIN LATERAL jsonb_object_keys(r.fields) k "
                    "WHERE d.is_active AND d.domain_id IS NOT DISTINCT FROM "
                    "  (SELECT domain_id FROM document_registry WHERE id = %s) "
                    "GROUP BY k ORDER BY 2 DESC LIMIT 60", (document_id,))
                fields = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        logger.debug("record catalogue unavailable: %s", exc)
    return {"types": types, "fields": fields}


def _replace_records(document_id: str, records: list[dict], model: str) -> None:
    from app.db.connection import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_records WHERE document_id = %s", (document_id,))
            for r in records:
                cur.execute(
                    "INSERT INTO document_records (document_id, chunk_id, page_number, record_type, "
                    "record_type_key, record_id, fields, source_text, confidence, extracted_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)",
                    (document_id, r.get("chunk_id"), r.get("page_number"), r["record_type"],
                     r["record_type_key"], r["record_id"], json.dumps(r["fields"]),
                     r.get("source_text"), r.get("confidence"), model))


# ── Entry points ──────────────────────────────────────────────────────────────

def extract_for_document(document_id: str, dry_run: bool = False) -> dict:
    """Read the document's stored passages and (re)write its records. Returns counts."""
    if not settings.RECORD_EXTRACTION_ENABLED:
        return {"status": "disabled"}
    t0 = time.monotonic()
    passages = _load_passages(document_id)
    cands = [p for p in passages if is_candidate(p["text"])][: settings.RECORD_EXTRACTION_MAX_PASSAGES]
    for p in cands:
        p["_sq"] = _squash(p["text"])
    result = {"status": "ok", "passages": len(passages), "candidates": len(cands),
              "records": 0, "batches": 0, "failed_batches": 0}
    if dry_run:
        result["status"] = "dry_run"
        return result
    if not cands:
        _replace_records(document_id, [], settings.GEMMA4_MODEL_NAME)
        return result

    from app.services import gemma_client
    catalogue = _catalogue_for(document_id)
    found: list[dict] = []
    for batch in batches(cands, settings.RECORD_EXTRACTION_BATCH_CHARS):
        result["batches"] += 1
        try:
            raw = gemma_client.chat(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": _user_msg(batch, catalogue)}],
                max_tokens=2500, temperature=0.0,
                timeout=settings.RECORD_EXTRACTION_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            result["failed_batches"] += 1
            logger.warning("[%s] record extraction batch failed: %s", document_id, exc)
            continue
        found += ground_records(_parse_json_array(raw), batch)

    if result["failed_batches"] and result["failed_batches"] == result["batches"]:
        result["status"] = "failed"          # keep whatever records the document had before
        return result
    records = dedupe(found)
    _replace_records(document_id, records, settings.GEMMA4_MODEL_NAME)
    result["records"] = len(records)
    result["seconds"] = round(time.monotonic() - t0, 1)
    return result


def run_after_completion(document_id: str) -> None:
    """Ingestion hook — never raises (a finished document must stay finished).
    Period tags first (cheap, no LLM — the backend's hard filter reads them), then records."""
    try:
        from app.services.document_tags import tag_document
        logger.info("[%s] period tags: %s", document_id, (tag_document(document_id) or {}).get("periods"))
    except Exception as exc:
        logger.warning("[%s] period tagging failed (non-fatal): %s", document_id, exc)
    try:
        res = extract_for_document(document_id)
        logger.info("[%s] document records: %s", document_id, res)
    except Exception as exc:
        logger.warning("[%s] document record extraction failed (non-fatal): %s", document_id, exc)
