"""
Document scope — the hard filter in front of document search (Nov 2026).

Search ranks passages by how similar they sound; with many near-identical reports the one the
question names loses to its neighbours (shipment evaluation: "SHP-2025-81218" answered from
SHP-2026-83341, "Q2 2026" from the Q1 2024 table). When the question names something precise,
only matching documents are searched:

  1. report / document id   SHP-2025-81218, INV-2026-57326, ARV-SAL-0083   (file name or title)
  1b. document name         "the Global Price Book", "the sell-in versus sell-through analysis"
                            (11 Nov 2026: most of a document's name words appear in the question)
  2. period                 Q2 2026, FY2025, May 2026, H1 2026            (doc_metadata.periods,
                                                                          tagged by the RAG engine)
  3. document type          SOP / procedure, policy, exception report, performance report,
                            invoice, knowledge base, report                (file name or title)

Each filter narrows the previous result; a filter that matches NO document is dropped (fallback)
and recorded, so the answer can say "no document for Q3 2027 — searched all your documents".
Everything happens inside the caller's allowed document list (the API key's collections), never
outside it. The period normaliser mirrors app/services/document_tags.py.

Ported from the Manthan platform (backend/app/services/doc_scope.py, 12 Nov 2026). Only the
database access changed (engine connection pool instead of SQLAlchemy); the matching rules are
kept identical so retrieval behaves as in the platform.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── periods (same rules as the engine's document_tags.py) ─────────────────────
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_YEAR = r"((?:19|20)\d{2})"


def _month(name: str, year: str) -> list:
    n = _MONTHS.get(name.lower()[:3])
    return [f"{year}-{n:02d}"] if n else []


# Question side: a month means that month only (the document side also tags its quarter, so a
# "Q2 2026" question still finds a "May 2026" report).
_PATTERNS = [
    (re.compile(rf"\bq([1-4])[\s_\-/]*(?:fy[\s_\-]?)?{_YEAR}\b", re.I), lambda m: [f"{m[2]}-Q{m[1]}"]),
    (re.compile(rf"\b{_YEAR}[\s_\-/]*q([1-4])\b", re.I), lambda m: [f"{m[1]}-Q{m[2]}"]),
    (re.compile(rf"\bh([12])[\s_\-/]*{_YEAR}\b", re.I), lambda m: [f"{m[2]}-H{m[1]}"]),
    (re.compile(rf"\bfy[\s_\-]?{_YEAR}\b", re.I), lambda m: [f"FY{m[1]}"]),
    (re.compile(r"\bfy[\s_\-]?(\d{2})\b", re.I), lambda m: [f"FY20{m[1]}"]),
    (re.compile(rf"\b{_MONTH_RE}[\s_\-,]*{_YEAR}\b", re.I), lambda m: _month(m[1], m[2])),
]

# A "May" that is the verb ("may 2025 targets") is rare enough with a year next to it to accept.
_ID_RE = re.compile(r"\b[A-Z]{2,6}-(?:[A-Z]{2,6}-)?\d{2,6}(?:-\d{2,6})?\b")
_TYPES = [
    ("sop", re.compile(r"\b(sops?|standard operating procedures?|procedures?)\b", re.I)),
    ("policy", re.compile(r"\bpolic(?:y|ies)\b", re.I)),
    ("exception_report", re.compile(r"\bexception reports?\b", re.I)),
    ("performance_report", re.compile(r"\bperformance reports?\b", re.I)),
    ("invoice", re.compile(r"\binvoices?\b", re.I)),
    # 12 Nov 2026 (advanced Sales eval Q33): "an invoice … conflicts with the signed agreement" kept
    # invoices only; with this type the agreements stay in the search beside the invoices.
    ("agreement", re.compile(r"\b(agreements?|contracts?)\b", re.I)),
    ("knowledge_base", re.compile(r"\bknowledge ?bases?\b", re.I)),
    ("report", re.compile(r"\breports?\b", re.I)),
]
# words that make "report" mean the document type rather than a verb or a generic noun
_REPORT_CUE = re.compile(r"\b(the|this|that|these|those|supplied|attached|uploaded|provided|exception|performance|from|in)\s+(?:\w+\s+){0,2}reports?\b", re.I)
_CUE_WORDS = r"\b(the|this|that|these|those|supplied|attached|uploaded|provided|signed|from|in|under)\s+(?:[\w'’-]+\s+){0,3}"
_DOC_CUE = {"invoice": re.compile(_CUE_WORDS + r"invoices?\b", re.I),
            "agreement": re.compile(_CUE_WORDS + r"(agreements?|contracts?)\b", re.I)}


def periods_in(text: str) -> list:
    out = []
    t = (text or "").replace("_", " ")
    for rx, fn in _PATTERNS:
        for m in rx.finditer(t):
            out += fn(m)
    return list(dict.fromkeys(out))


def ids_in(text: str) -> list:
    # period-looking tokens like "FY-2025" are not ids; ids carry letters + a long number part
    return [i for i in dict.fromkeys(_ID_RE.findall(text or "")) if not re.fullmatch(r"(FY|Q[1-4]|H[12])-\d+", i)]


def types_in(text: str) -> list:
    t = text or ""
    out = []
    for name, rx in _TYPES:
        if not rx.search(t):
            continue
        if name == "report" and (not _REPORT_CUE.search(t) or any(x in out for x in ("exception_report", "performance_report"))):
            continue
        # 12 Nov 2026 (advanced eval Q14): "a customer disputes an invoice amount" is about invoices as a
        # subject, not "the invoice" document — filtering on it hid the Billing Dispute procedure.
        if name in ("invoice", "agreement") and not _DOC_CUE[name].search(t):
            continue
        out.append(name)
    return out


def _doc_types(name: str) -> set:
    n = (name or "").replace("_", " ")
    return {t for t, rx in _TYPES if rx.search(n)}


def parse_question(prompt: str) -> dict:
    return {"ids": ids_in(prompt), "periods": periods_in(prompt), "types": types_in(prompt),
            "text": prompt or ""}


# ── document name (11 Nov 2026, Sales evaluation) ─────────────────────────────
# The test corpus repeats the same sentences with different numbers in every document, so a
# question naming "the Channel Partner Scorecard" or "the Design Win Summary" was answered from
# whichever document ranked best. A document whose NAME the question spells out is now the scope.
_NAME_STOP = {"the", "and", "of", "for", "a", "an", "in", "on", "to", "by", "with", "sales", "edition",
              "doc", "document", "final", "draft", "copy", "pdf", "docx", "txt"}
_PERIOD_WORD = re.compile(
    r"(?:19|20)\d{2}|q[1-4]|fy\d*|h[12]|\d+|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
    r"july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|v\d+|week")
_NAME_MIN_SHARE = 0.75   # share of a document's name words the question must contain
_NAME_MIN_WORDS = 2      # one-word names ("Sales_Returns.pdf" → "returns") are too generic


def _words(s: str) -> list:
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s or "", flags=re.I)
    return re.findall(r"[a-z0-9]+", s.replace("_", " ").replace("-", " ").lower())


def _name_words(filename: str) -> list:
    """Distinctive words of a document's name: no file type, 'Sales_' prefix, version / copy
    number or period (the period filter handles that), no filler words."""
    return [w for w in dict.fromkeys(_words(filename))
            if w not in _NAME_STOP and not _PERIOD_WORD.fullmatch(w)]


def _has_word(word: str, qwords: set) -> bool:
    return word in qwords or (word.endswith("s") and word[:-1] in qwords) or (word + "s") in qwords


def named_documents(prompt: str, docs: list) -> list:
    """Documents whose name the question spells out: at least _NAME_MIN_SHARE of the name words
    (and _NAME_MIN_WORDS of them) appear in the question. Only the best-scoring documents are kept
    (several years / copies of one report all stay; the period filter then picks the year)."""
    qwords = set(_words(prompt))
    if not qwords:
        return []
    scored = []
    for d in docs:
        name = _name_words(d.get("original_filename") or "")
        if len(name) < _NAME_MIN_WORDS:
            continue
        hits = sum(1 for w in name if _has_word(w, qwords))
        share = hits / len(name)
        if hits >= _NAME_MIN_WORDS and share >= _NAME_MIN_SHARE:
            scored.append((share, hits, d))
    if not scored:
        return _named_by_proper_name(prompt, docs)
    best = max((s, h) for s, h, _ in scored)
    return [d for s, h, d in scored if (s, h) == best]


# One-word names (12 Nov 2026, advanced Sales eval Q35/Q36): "the Zhengda agreement", "Anchorline's
# agreement" name ONE company, but the two-word rule above needs 2 name words. A word the user wrote
# with a capital letter inside the sentence, that appears in the names of only ONE kind of document
# (one report family), counts as naming it. Generic words never count (stop words, periods, report
# kinds, document types, and words found in several families' names).
_TYPE_WORDS = {"sop", "sops", "procedure", "procedures", "policy", "policies", "report", "reports", "invoice",
               "invoices", "agreement", "agreements", "contract", "contracts", "knowledge", "base"}
_PROPER_MIN_LEN = 4


def _proper_words(prompt: str) -> list:
    """Capitalised words that are not the first word of a sentence: 'What does the Zhengda …' → zhengda."""
    out = []
    for m in re.finditer(r"(?<![.!?:]\s)(?<!^)\b([A-Z][a-z][A-Za-z0-9]+)", prompt or ""):
        w = m.group(1).lower()
        if (len(w) >= _PROPER_MIN_LEN and w not in _NAME_STOP and w not in _TYPE_WORDS
                and w not in _GENERIC_KIND and w not in _REPORT_KIND and not _PERIOD_WORD.fullmatch(w)):
            out.append(w)
    return list(dict.fromkeys(out))


_ORG_SUFFIX = re.compile(r"(?:^|[_\s.\-])(co|ltd|pvt|inc|llc|gmbh|kaisha|plc|corp|ag|bv|sa|limited)(?:$|[_\s.\-])", re.I)


def _entity_document(d: dict) -> bool:
    """A document named after a company or carrying a document id (agreements, customer profiles,
    quotations) — the only names a single capitalised word may pick. 'Customer Service' must not
    pick the Customer Service Call Notes."""
    fn = re.sub(r"\.[a-z0-9]{2,5}$", "", d.get("original_filename") or "", flags=re.I)
    return bool(ids_in(fn.replace("_", " ")) or _ORG_SUFFIX.search(fn))


def _named_by_proper_name(prompt: str, docs: list) -> list:
    words = _proper_words(prompt)
    if not words:
        return []
    for w in words:
        hit = [d for d in docs if _has_word(w, set(_name_words(d.get("original_filename") or "")))]
        if hit and all(_entity_document(d) for d in hit) and len({family_of(d) for d in hit}) == 1:
            return hit
    return []


# ── matching ──────────────────────────────────────────────────────────────────

def _load_docs(doc_ids: list) -> list:
    from app.db.connection import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id::text, original_filename, coalesce(doc_title, ''),
                       coalesce(doc_metadata -> 'periods', '[]'::jsonb)
                FROM document_registry
                WHERE id = ANY(CAST(%s AS uuid[])) AND is_active AND status = 'completed'""",
                (list(doc_ids),))
            rows = cur.fetchall()
    return [{"id": r[0], "original_filename": r[1] or "", "title": r[2] or "", "periods": list(r[3] or [])}
            for r in rows]


def match(scope: dict, docs: list) -> dict:
    """Apply id → period → type in turn. Returns {"doc_ids": [...] | None (no filter applied),
    "applied": [...], "fallbacks": [...], "periods": [...], "documents": [...]}."""
    applied, fallbacks = [], []
    cand = list(docs)

    def _narrow(label: str, keep):
        nonlocal cand
        hit = [d for d in cand if keep(d)]
        if hit:
            cand = hit
            applied.append(label)
        else:
            fallbacks.append(label)

    ids = scope.get("ids") or []
    if ids:
        idl = [i.lower() for i in ids]
        _narrow("report " + ", ".join(ids),
                lambda d: any(i in (d["original_filename"] + " " + d["title"]).lower() for i in idl))
    by_id = bool(ids and applied)
    # document name — never recorded as a fallback: most questions name no document
    by_name = False
    if not by_id and scope.get("text"):
        named = named_documents(scope["text"], cand)
        if named:
            keep = {d["id"] for d in named}
            cand = [d for d in cand if d["id"] in keep]
            labels = list(dict.fromkeys(re.sub(r"\.[a-z0-9]{2,5}$", "", d["original_filename"], flags=re.I)
                                        for d in named))
            applied.append("document " + ", ".join(labels[:3]) + (" …" if len(labels) > 3 else ""))
            by_name = True
    # A distinctive report kind the question names ("the scorecard's basis …", "the forecast") when only
    # ONE family of the user's documents is of that kind — with or without a period (12 Nov 2026,
    # medium eval Q8: no period, so the scorecard was never picked and an invoice answered).
    by_kind = False
    if not by_id and not by_name and scope.get("text"):
        kind_fam = _named_kind_family(scope["text"], cand)
        if kind_fam:
            cand = [d for d in cand if family_of(d) == kind_fam]
            applied.append("report family " + kind_fam)
            by_kind = True
    if scope.get("periods") and not by_id:
        want = set(scope["periods"])
        _narrow("period " + ", ".join(scope["periods"]), lambda d: bool(want & set(d.get("periods") or [])))
        if (by_name or by_kind) and fallbacks and fallbacks[-1].startswith("period "):
            # the named document exists but is not tagged with that period (or is for another
            # year): keep the named document — it is what the question asked about
            fallbacks.pop()
    # Report family (11 Nov 2026): a period / type filter that still leaves several KINDS of report
    # (Shipment Performance Report, Sales Revenue Forecast, Order Cancellation Analysis …) cannot tell
    # which one the user means. In order (12 Nov 2026, medium Sales eval Q2/Q4–Q7):
    #   1. a report kind the question names that only ONE family has ("the scorecard", "the forecast")
    #      — it wins over the chat's earlier pick, so a remembered report gives way;
    #   2. the user's pick (hint) or the chat's earlier pick (pref);
    #   3. a document ARIA named that is among the candidates;
    # all BEFORE the document-type filter: "what does the scorecard REPORT for …" used to filter on
    # type "report", which dropped the scorecard the chat had chosen and forced a wrong pick.
    family, choices = None, []
    fixed = False

    def _pick_family() -> bool:
        nonlocal cand, family
        if len(families_of(cand)) < 2:
            return False
        named_kind = _named_kind_family(scope.get("text") or "", cand)
        for label, how in ((named_kind, "named"), (scope.get("family_hint"), "picked"),
                           (scope.get("family_pref"), "picked")):
            kept = [d for d in cand if label and _topic_overlap(label, d)] if how == "picked" else \
                   [d for d in cand if label and family_of(d) == label]
            if kept:
                cand, family = kept, label
                applied.append("report family " + label)
                return True
        aria = {f.lower() for f in (scope.get("aria_files") or [])}
        kept = [d for d in cand if d["original_filename"].lower() in aria]
        if kept and len({family_of(d) for d in kept}) == 1:
            cand, family = kept, family_of(kept[0])
            applied.append("report family " + family)
            return True
        return False

    if by_kind:
        fixed = True
        family = family_of(cand[0]) if cand else None
    if applied and not by_id and not by_name and not fixed:
        fixed = _pick_family()
    if scope.get("types") and not by_id and not by_name and not fixed:
        want_t = set(scope["types"])
        _narrow("document type " + ", ".join(scope["types"]),
                lambda d: bool(want_t & _doc_types(d["original_filename"] + " " + d["title"])))
        if applied and not fixed:
            fixed = _pick_family()
    if applied and not by_id and not by_name and not fixed:
        fams = families_of(cand)
        # Ask only when a PERIOD left several kinds of report ("Q3 2026 bookings"). A document type
        # alone ("what does the SOP / invoice say") is left to ranking — asking there interrupted
        # questions that named the document in other words (advanced eval Q14 / Q29 / Q33).
        if len(fams) >= 2 and any(a.startswith("period ") for a in applied):
            choices = [{"label": f, "documents": n} for f, n in fams[:_MAX_CHOICES]]
    elif (by_name or by_id) and cand:
        family = family_of(cand[0])   # a named document / report id also sets the chat's report family
    return {"doc_ids": [d["id"] for d in cand] if applied else None,
            "applied": applied, "fallbacks": fallbacks,
            "documents": [d["original_filename"] for d in cand][:20] if applied else [],
            "family": family, "choices": choices}


# ── report families ───────────────────────────────────────────────────────────
# Words that describe the KIND of report, not its subject — "Shipment Performance Report" and
# "Shipment Exception Report" share the subject "shipment".
_GENERIC_KIND = {"report", "reports", "performance", "analysis", "summary", "review", "position", "weekly",
                 "monthly", "quarterly", "annual", "page", "article", "knowledge", "guide", "procedure",
                 "policy", "sop", "standard", "operating", "kpi", "dashboard", "scorecard", "inv", "versus"}
_MAX_CHOICES = 6


def family_of(d: dict) -> str:
    """'Sales_Shipment_Performance_Report_May_2026.pdf' → 'Shipment Performance Report'."""
    words = [w for w in _name_words(d.get("original_filename") or "") if not re.fullmatch(r"\d+", w)]
    return " ".join(w.capitalize() for w in words) or (d.get("original_filename") or "")


_REPORT_KIND = {"report", "forecast", "review", "analysis", "scorecard", "summary", "position", "dashboard",
                "reconciliation", "minutes"}


def families_of(docs: list) -> list:
    """[(family label, number of documents)] — periodic reports first (performance reports,
    forecasts, reviews …), then other pages; most documents first within each."""
    counts: dict = {}
    for d in docs:
        f = family_of(d)
        counts[f] = counts.get(f, 0) + 1
    return sorted(counts.items(),
                  key=lambda x: (0 if set(_words(x[0])) & _REPORT_KIND else 1, -x[1], x[0]))


# Report kinds distinctive enough that naming one in a question picks that report when only one
# family of the candidates is of that kind ("the scorecard", "the forecast"). Words like "report",
# "summary", "analysis" or "review" are too common in questions to count.
_NAMED_KINDS = {"scorecard", "forecast", "reconciliation", "minutes", "dashboard", "scorecards", "forecasts",
                "reconciliations", "dashboards"}


def _named_kind_family(text: str, docs: list) -> Optional[str]:
    kinds = {w.rstrip("s") for w in _words(text) if w in _NAMED_KINDS}
    if not kinds:
        return None
    fams = {family_of(d) for d in docs}
    hit = [f for f in fams if kinds & {w.rstrip("s") for w in _words(f)}]
    return hit[0] if len(hit) == 1 else None


def _topic(label: str) -> set:
    words = {w for w in _words(label) if w not in _NAME_STOP and not _PERIOD_WORD.fullmatch(w)}
    topic = words - _GENERIC_KIND
    return topic or words


def _topic_overlap(label: str, d: dict) -> bool:
    """Does this document belong to the chosen report family? Same subject word(s): the choice
    'Shipment Performance Report' keeps the shipment performance AND shipment exception reports."""
    want = _topic(label)
    have = set(_name_words(d.get("original_filename") or ""))
    have |= {w[:-1] for w in have if w.endswith("s")}
    return bool(want & have)


def resolve(prompt: str, allowed_doc_ids: Optional[list], family_hint: Optional[str] = None,
            family_pref: Optional[str] = None, aria_files: Optional[list] = None) -> Optional[dict]:
    """Scope for this question within the allowed documents, or None (nothing named / error).
    family_hint: the report family the user just picked; family_pref: the one picked earlier in
    this chat (applied silently when the question again leaves several kinds of report);
    aria_files: document file names ARIA gave as the source of its document intents (used only to
    settle a choice between several kinds of report, never to widen the search)."""
    scope = parse_question(prompt)
    # a document name can only be checked against the documents, so every question with text is
    # looked at (one query over the user's own document list)
    if not (scope["ids"] or scope["periods"] or scope["types"] or scope["text"].strip()) or not allowed_doc_ids:
        return None
    try:
        docs = _load_docs(allowed_doc_ids)
    except Exception as e:
        logger.warning("doc_scope: could not load documents (%s) — no filter", e)
        return None
    scope["family_hint"], scope["family_pref"] = family_hint, family_pref
    scope["aria_files"] = list(aria_files or [])
    res = match(scope, docs)
    res["asked"] = {k: v for k, v in scope.items()
                    if k not in ("text", "family_hint", "family_pref", "aria_files")}
    logger.info("doc_scope: asked=%s applied=%s fallbacks=%s docs=%d",
                scope, res["applied"], res["fallbacks"], len(res["doc_ids"] or []))
    return res


def fallback_note(res: Optional[dict]) -> str:
    """One plain sentence for the answer when a named filter matched nothing."""
    if not res or not res.get("fallbacks"):
        return ""
    return ("No document matched " + "; ".join(res["fallbacks"])
            + " — that part was searched across all documents in scope instead.")
