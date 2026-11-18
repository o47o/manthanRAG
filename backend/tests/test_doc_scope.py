"""Question scope rules (app/services/scope/doc_scope.py).

Ported from the Manthan platform's tests/test_doc_scope.py (12 Nov 2026); tests that exercised the
platform's planner / answer writer are left out. No database: documents are plain dicts.
"""
from app.services.scope import doc_scope as ds


def _fake_db(monkeypatch, docs):
    """resolve() reads the documents from the database — give it plain dicts instead."""
    monkeypatch.setattr(ds, "_load_docs", lambda ids: [d for d in docs if d["id"] in ids])


"""Hard filter for document search (services/doc_scope.py) + both-sources routing (Nov 2026).
No database: documents are given as plain dicts."""


DOCS = [
    {"id": "a", "original_filename": "Sales_Shipment_Exception_Report_SHP-2025-81218.pdf",
     "title": "Exception Report: RMA-2025-4817", "periods": ["FY2025"]},
    {"id": "b", "original_filename": "Sales_Shipment_Exception_Report_SHP-2026-83341.txt",
     "title": "Shipment Exception Report SHP-2026-83341", "periods": ["2026-Q3"]},
    {"id": "c", "original_filename": "Sales_Shipment_Performance_Report_May_2026.pdf",
     "title": "Monthly shipment performance - Q2 2026", "periods": ["2026-05", "2026-Q2"]},
    {"id": "d", "original_filename": "Sales_Shipment_Confirmation_and_Proof_of_Delivery_SOP.docx",
     "title": "Shipment Confirmation and Proof of Delivery Procedure", "periods": []},
]


def test_parse_question():
    q = ds.parse_question("What FY2025 delivery performance is stated in SHP-2025-81218?")
    assert q["ids"] == ["SHP-2025-81218"] and q["periods"] == ["FY2025"]
    assert ds.parse_question("Q2 2026 revenue versus plan")["periods"] == ["2026-Q2"]
    assert ds.parse_question("bookings in May 2026")["periods"] == ["2026-05"]
    assert ds.parse_question("H1 2025 and 2026 Q3")["periods"] == ["2025-H1", "2026-Q3"] or \
        set(ds.parse_question("H1 2025 and 2026 Q3")["periods"]) == {"2025-H1", "2026-Q3"}
    assert ds.parse_question("What does the SOP require?")["types"] == ["sop"]
    assert ds.parse_question("records in the supplied reports")["types"] == ["report"]
    q0 = ds.parse_question("Top 10 customers by revenue")
    assert (q0["ids"], q0["periods"], q0["types"]) == ([], [], [])
    assert ds.parse_question("please report the top customers")["types"] == []   # verb, not a document


def test_id_wins_and_period_and_type_narrow():
    r = ds.match({"ids": ["SHP-2025-81218"], "periods": ["FY2025"], "types": ["report"]}, DOCS)
    assert r["doc_ids"] == ["a"] and r["applied"] == ["report SHP-2025-81218"]
    r = ds.match({"ids": [], "periods": ["2026-Q2"], "types": []}, DOCS)
    assert r["doc_ids"] == ["c"]
    r = ds.match({"ids": [], "periods": [], "types": ["sop"]}, DOCS)
    assert r["doc_ids"] == ["d"]
    r = ds.match({"ids": [], "periods": ["2026-Q2", "FY2025"], "types": ["exception_report"]}, DOCS)
    assert r["doc_ids"] == ["a"]                       # period narrows to a + c, type keeps a


def test_fallback_when_a_filter_matches_nothing():
    r = ds.match({"ids": ["INV-2030-00001"], "periods": ["2026-Q2"], "types": []}, DOCS)
    assert r["fallbacks"] == ["report INV-2030-00001"] and r["doc_ids"] == ["c"]
    r = ds.match({"ids": [], "periods": ["2027-Q4"], "types": []}, DOCS)
    assert r["doc_ids"] is None and r["fallbacks"] == ["period 2027-Q4"]
    assert "searched across all documents in scope" in ds.fallback_note(r)
    assert ds.fallback_note({"fallbacks": []}) == ""


def test_resolve_skips_when_nothing_named(monkeypatch):
    _fake_db(monkeypatch, DOCS)
    r = ds.resolve("Top 10 customers by revenue", ["a", "b", "c", "d"])
    assert not (r or {}).get("doc_ids")
    assert ds.resolve("Q2 2026 revenue", []) is None


SALES = [
    {"id": n, "original_filename": f, "title": "", "periods": p} for n, (f, p) in enumerate([
        ("Sales_Global_Price_Book_2024_Edition.pdf", ["FY2024"]),
        ("Sales_Global_Order_Booking_Policy.pdf", []),
        ("Sales_Global_Order_Booking_Policy.docx", []),
        ("Sales_Order_Booking_Standard_Operating_Procedure.pdf", []),
        ("Sales_Booking_Approval_Authority_Matrix.docx", []),
        ("Sales_Order_Cancellation_and_Reschedule_Policy_v10.pdf", []),
        ("Sales_Order_Cancellation_and_Reschedule_Policy_v11.docx", []),
        ("Sales_Policy_Page_Order_Acceptance_and_Confirmation.pdf", []),
        ("Sales_Sales_Revenue_Forecast_Q1_2024.pdf", ["2024-Q1", "FY2024"]),
        ("Sales_Sales_Revenue_Forecast_Q2_2026.pdf", ["2026-Q2", "FY2026"]),
        ("Sales_Sales_KPI_Dashboard_Q2_2026.jpg", ["2026-Q2"]),
        ("Sales_Sell-In_versus_Sell-Through_Analysis_Q1_2025.pdf", ["2025-Q1"]),
        ("Sales_Sell-In_versus_Sell-Through_Analysis_Q1_2026.pdf", ["2026-Q1"]),
        ("Sales_Distributor_Inventory_Position_May_2024.pdf", ["2024-05", "2024-Q2"]),
        ("Sales_Distributor_Inventory_Position_March_2026.pdf", ["2026-03", "2026-Q1"]),
        ("Sales_Return_Material_Authorisation_Policy.docx", []),
        ("Sales_Returns_Policy.pdf", []),
        ("Sales_Design_Win_Summary_Q1_2026.pdf", ["2026-Q1"]),
        ("Sales_Channel_Partner_Scorecard_Q3_2026.pdf", ["2026-Q3"]),
        ("Sales_Channel_Partner_Program_Manual_2026.docx", ["FY2026"]),
    ], 1)
]


def _names(prompt):
    r = ds.match(ds.parse_question(prompt), SALES)
    return sorted(d["original_filename"] for d in SALES if d["id"] in (r["doc_ids"] or []))


def test_document_named_by_title_is_the_scope():
    assert _names("Who owns the current Order Cancellation and Reschedule Policy?") == [
        "Sales_Order_Cancellation_and_Reschedule_Policy_v10.pdf", "Sales_Order_Cancellation_and_Reschedule_Policy_v11.docx"]
    assert _names("What is the definition of book-to-bill in the Global Price Book?") == [
        "Sales_Global_Price_Book_2024_Edition.pdf"]
    assert _names("Which region contributed the largest bookings in Q3 2026 according to the Channel Partner Scorecard?") == [
        "Sales_Channel_Partner_Scorecard_Q3_2026.pdf"]
    # the more specific name wins over a shorter one it contains
    assert _names("What does the Return Material Authorisation Policy govern?") == [
        "Sales_Return_Material_Authorisation_Policy.docx"]


def test_period_then_picks_the_year_of_a_named_report():
    assert _names("What was FY2026 revenue in the Sales Revenue Forecast Q2 2026, and how did it compare with plan?") == [
        "Sales_Sales_Revenue_Forecast_Q2_2026.pdf"]
    assert _names("What was distributor channel inventory at the end of Q1 2026 in the sell-in versus sell-through analysis?") == [
        "Sales_Sell-In_versus_Sell-Through_Analysis_Q1_2026.pdf"]
    assert _names("What was the total backlog at the close of March 2026 in the Distributor Inventory Position report?") == [
        "Sales_Distributor_Inventory_Position_March_2026.pdf"]
    # named report for a period it does not carry: keep the named report, no fallback note
    r = ds.match(ds.parse_question("Design Win Summary figures for Q4 2027"), SALES)
    assert [d["original_filename"] for d in SALES if d["id"] in r["doc_ids"]] == ["Sales_Design_Win_Summary_Q1_2026.pdf"]
    assert r["fallbacks"] == []


def test_questions_naming_no_document_are_not_narrowed_by_name():
    for q in ("What are the key checks required before an order booking is confirmed?",
              "Top 10 customers by revenue",
              "Total booking value by sales channel in 2024",
              "Which distribution location had the highest weeks of supply in March 2026?"):
        r = ds.match(ds.parse_question(q), SALES)
        assert not any(a.startswith("document ") for a in r["applied"]), q


def _gave_up(status, scope, intents=None):
    return {"intent_status": status, "doc_scope": scope,
            "intent_result": {"intents": intents or [], "needs_clarification": status == "needs_clarification",
                              "clarifying_question": "Which one?" if status == "needs_clarification" else None}}


NAMED = {"doc_ids": ["1"], "applied": ["document Sales_Global_Price_Book_2024_Edition"],
         "fallbacks": [], "documents": ["Sales_Global_Price_Book_2024_Edition.pdf"]}


FAM = [
    {"id": "s1", "original_filename": "Sales_Shipment_Performance_Report_February_2024.pdf", "title": "", "periods": ["2024-Q1", "FY2024"]},
    {"id": "s2", "original_filename": "Sales_Shipment_Performance_Report_December_2024.pdf", "title": "", "periods": ["2024-Q1", "2024-12"]},
    {"id": "f1", "original_filename": "Sales_Sales_Revenue_Forecast_Q1_2024.pdf", "title": "", "periods": ["2024-Q1"]},
    {"id": "c1", "original_filename": "Sales_Order_Cancellation_Analysis_Q1_2024.pdf", "title": "", "periods": ["2024-Q1"]},
    {"id": "x1", "original_filename": "Sales_Shipment_Exception_Report_SHP-2025-81218.pdf", "title": "", "periods": ["FY2025"]},
    {"id": "p1", "original_filename": "Sales_Shipment_Confirmation_and_Proof_of_Delivery_SOP.docx", "title": "", "periods": []},
    {"id": "p2", "original_filename": "Sales_Order_Booking_Standard_Operating_Procedure.pdf", "title": "", "periods": []},
]


Q7 = "For Q1 2024, compare bookings, revenue, backlog and on-time delivery with their targets."


def _m(q, **kw):
    sc = ds.parse_question(q)
    sc.update(kw)
    return ds.match(sc, FAM)


def test_several_kinds_of_report_offer_a_choice():
    r = _m(Q7)
    labels = [c["label"] for c in r["choices"]]
    assert labels[0] == "Shipment Performance Report"            # 2 documents, a periodic report
    assert set(labels) == {"Shipment Performance Report", "Revenue Forecast", "Order Cancellation Analysis"}
    assert r["family"] is None


def test_a_pick_or_the_chat_preference_narrows_to_that_kind():
    for kw in ({"family_hint": "Shipment Performance Report"}, {"family_pref": "Shipment Performance Report"}):
        r = _m(Q7, **kw)
        assert sorted(r["doc_ids"]) == ["s1", "s2"] and r["choices"] == [] and r["family"]
    # the family of a document named earlier (the shipment SOP) also keeps shipment reports
    r = _m(Q7, family_pref="Shipment Confirmation Proof Delivery Sop")
    assert sorted(r["doc_ids"]) == ["s1", "s2"]
    # a preference that matches nothing here does not narrow — the choice is offered again
    r = _m(Q7, family_pref="Billing Dispute Resolution Procedure")
    assert r["choices"] and r["family"] is None


def test_named_document_sets_the_family():
    r = _m("Who owns the Shipment Confirmation and Proof of Delivery SOP?")
    assert r["family"] == "Shipment Confirmation Proof Delivery Sop" and r["choices"] == []
    r = _m("What does the SOP require?", family_pref="Shipment Confirmation Proof Delivery Sop")
    assert r["doc_ids"] == ["p1"]


N56 = [
    {"id": "k1", "original_filename": "Sales_Partner_Scorecard_Q2_2031.pdf", "title": "", "periods": ["2031-Q2"]},
    {"id": "k2", "original_filename": "Sales_Freight_Exception_Report_FRX-2031-11111.txt", "title": "", "periods": ["2031-Q2"]},
    {"id": "k3", "original_filename": "Sales_Order_Ageing_Report_May_2031.pdf", "title": "", "periods": ["2031-Q2", "2031-05"]},
    {"id": "k4", "original_filename": "Sales_Widget_Forecast_Q2_2031.pdf", "title": "", "periods": ["2031-Q2"]},
    {"id": "g1", "original_filename": "Sales_Supply_Agreement_Brightwater_Holdings_Ltd._CTR-2031-00001.docx", "title": "", "periods": []},
    {"id": "g2", "original_filename": "Sales_Supply_Agreement_Northvale_Trading_CTR-2031-00002.docx", "title": "", "periods": []},
    {"id": "i1", "original_filename": "Sales_Commercial_Invoice_INV-2031-00009.pdf", "title": "", "periods": []},
    {"id": "i2", "original_filename": "Sales_Customer_Billing_and_Invoice_Issuance_SOP.docx", "title": "", "periods": []},
    {"id": "n1", "original_filename": "Sales_Customer_Service_Call_Notes_May_2031.txt", "title": "", "periods": ["2031-05"]},
]


def _n(q, **kw):
    sc = ds.parse_question(q)
    sc["text"] = q
    sc.update(kw)
    return ds.match(sc, N56)


def test_type_word_never_drops_the_remembered_report():
    # "what does the scorecard REPORT …": the chat picked the scorecard earlier; the type word
    # "report" must not filter it away and force another pick
    r = _n("What does the scorecard report for distributor weeks in Q2 2031?",
           family_pref="Partner Scorecard")
    assert r["doc_ids"] == ["k1"] and r["choices"] == []


def test_named_report_kind_wins_over_the_remembered_report():
    r = _n("For Q2 2031, which scorecard KPI was furthest below target?",
           family_pref="Freight Exception Report Frx")
    assert r["doc_ids"] == ["k1"] and r["family"] == "Partner Scorecard"
    r = _n("What is the forecast revenue for Q2 2031?")
    assert r["doc_ids"] == ["k4"]


def test_period_question_naming_no_kind_still_asks():
    r = _n("How did Q2 2031 bookings compare with target?")
    assert len(r["choices"]) >= 2 and r["family"] is None


def test_aria_named_file_settles_the_choice():
    r = _n("How did Q2 2031 bookings compare with target?",
           aria_files=["Sales_Order_Ageing_Report_May_2031.pdf"])
    assert r["doc_ids"] == ["k3"] and r["choices"] == []
    # a file ARIA named that is not among the candidates changes nothing
    r = _n("How did Q2 2031 bookings compare with target?", aria_files=["Sales_Nowhere_Report.pdf"])
    assert r["choices"]


def test_type_alone_never_asks():
    r = _n("A distributor's invoice conflicts with the signed supply agreement. Which terms govern?")
    assert r["choices"] == []
    assert {"g1", "g2"} <= set(r["doc_ids"])            # agreements stay in the search beside invoices


def test_one_capitalised_company_name_picks_its_document():
    r = _n("What rights does the buyer keep under the Brightwater agreement?")
    assert r["doc_ids"] == ["g1"]
    r = _n("Compare the traceability clause in Northvale's agreement with the general obligation.")
    assert r["doc_ids"] == ["g2"]


def test_ordinary_capitalised_words_do_not_pick_a_document():
    # 'Customer Service' is not a company name: the call notes carry no company suffix or id
    r = _n("Which exceptions are routed to the Customer Service team?")
    assert r["doc_ids"] is None
    # sentence-start and generic words never count
    assert ds._proper_words("Brightwater supplies parts. Scorecard Report Policy") == []


def test_invoice_as_a_subject_is_not_a_document_type():
    assert ds.types_in("A customer disputes an invoice amount after shipment.") == []
    assert ds.types_in("What does the invoice INV-2031-00009 state?") == ["invoice"]
    assert ds.types_in("terms printed on the supplier's invoice") == ["invoice"]


def test_report_kind_named_without_a_period_picks_its_report():
    # one scorecard family among the documents → "the scorecard's …" scopes to it, no period needed
    r = _n("What is the scorecard's stated basis for regional booking allocation?")
    assert r["doc_ids"] == ["k1"] and r["family"] == "Partner Scorecard" and r["fallbacks"] == []
    # "the scorecard report" — the type word must not then filter the scorecard away
    r = _n("What does the scorecard report about allocation?")
    assert r["doc_ids"] == ["k1"] and r["fallbacks"] == []
