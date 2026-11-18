"""Document picker (app/services/scope/doc_picker.py): the model chooses the document. No database,
no model — the store look-ups and the model call are replaced by fakes; documents have made-up names.

Ported from the Manthan platform's tests/test_doc_picker.py (13 Nov 2026)."""
import asyncio

import pytest

from app.services.scope import doc_picker as dp
from app.services.scope import doc_scope as ds

DOCS = [
    {"id": "a", "original_filename": "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf", "title": "", "periods": ["2031-Q1"]},
    {"id": "b", "original_filename": "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2030.pdf", "title": "", "periods": ["2030-Q1"]},
    {"id": "c", "original_filename": "Sales_Discount_Policy.docx", "title": "", "periods": []},
    {"id": "d", "original_filename": "Sales_Discount_Policy.pdf", "title": "", "periods": []},
    {"id": "e", "original_filename": "Sales_Partner_Scorecard_Q1_2031.pdf", "title": "", "periods": ["2031-Q1"]},
]


@pytest.fixture
def fake(monkeypatch):
    calls = {}
    monkeypatch.setattr(dp, "_load_docs", lambda ids: [d for d in DOCS if d["id"] in ids])

    async def search(prompt, allowed):
        return ["e", "c", "a"]
    monkeypatch.setattr(dp, "_search_ranked_docs", search)
    monkeypatch.setattr(dp, "_load_cards", lambda ids: {i: {"id": i, "original_filename": next(
        d["original_filename"] for d in DOCS if d["id"] == i), "headings": []} for i in ids})

    def set_answer(ans):
        async def ask(prompt, cards, pref):
            calls["cards"] = [c["original_filename"] for c in cards]
            calls["pref"] = pref
            if ans is None:
                return None
            # answers refer to card numbers by file name, so tests read naturally
            num = {c["original_filename"]: n + 1 for n, c in enumerate(cards)}
            return {"pick": [num[f] for f in ans.get("pick", [])], "ask": [num[f] for f in ans.get("ask", [])],
                    "reason": "test"}
        monkeypatch.setattr(dp, "_ask_model", ask)
    return calls, set_answer


def _run(prompt, rules=None, pref=None):
    return asyncio.run(dp.pick(prompt, [d["id"] for d in DOCS], rules, [], pref))


def test_partial_name_is_in_the_candidates_and_the_pick_scopes_the_search(fake):
    calls, answer = fake
    answer({"pick": ["Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf"]})
    r = _run("What does the widget-out report say about exception routing?")
    assert "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf" in calls["cards"]
    assert sorted(r["doc_ids"]) == ["a", "b"] and r["applied"][0].startswith("document ")   # no period: every edition
    assert r["family"] == ds.family_of(DOCS[0])


def test_copies_of_one_document_come_together(fake):
    _, answer = fake
    answer({"pick": ["Sales_Discount_Policy.docx"]})
    r = _run("Who owns the discount policy?")
    assert sorted(r["doc_ids"]) == ["c", "d"]


def test_two_different_documents_fitting_equally_ask_the_user(fake):
    _, answer = fake
    answer({"ask": ["Sales_Partner_Scorecard_Q1_2031.pdf", "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf"]})
    r = _run("How did Q1 2031 bookings compare with target?")
    assert r["doc_ids"] is None and len(r["choices"]) == 2


def test_general_question_searches_everything(fake):
    _, answer = fake
    answer({})
    r = _run("What is a design win?")
    assert r["doc_ids"] is None and r["choices"] == [] and r["applied"] == []


def test_model_failure_keeps_the_rules(fake):
    _, answer = fake
    answer(None)
    assert _run("anything") is None


def test_chat_preference_is_passed_and_its_documents_are_candidates(fake):
    calls, answer = fake
    answer({"pick": ["Sales_Partner_Scorecard_Q1_2031.pdf"]})
    _run("What were the regional bookings?", pref="Partner Scorecard")
    assert calls["pref"] == "Partner Scorecard" and calls["cards"][0] == "Sales_Partner_Scorecard_Q1_2031.pdf"


def test_copy_stem_and_name_matches():
    assert dp._copy_stem("Sales_X_Policy.pdf") == dp._copy_stem("Sales_X_Policy_02.docx")
    names = [d["original_filename"] for d in dp.name_matches("the widget out analysis", DOCS)]
    assert names[:2] == ["Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf",
                         "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2030.pdf"]


def test_no_period_brings_every_edition_of_the_picked_report(fake):
    _, answer = fake
    answer({"pick": ["Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2030.pdf"]})
    r = _run("What does the widget-out report say about exception routing?")
    assert sorted(r["doc_ids"]) == ["a", "b"]                     # both editions searched
    answer({"pick": ["Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2030.pdf"]})
    r = _run("What does the Q1 2030 widget-out report say about exception routing?")
    assert r["doc_ids"] == ["b"]                                   # a period keeps one edition


def test_several_different_reports_picked_becomes_a_question(fake):
    _, answer = fake
    answer({"pick": ["Sales_Partner_Scorecard_Q1_2031.pdf", "Sales_Widget_In_versus_Widget_Out_Analysis_Q1_2031.pdf"]})
    r = _run("For Q1 2031, compare bookings, revenue and backlog with their targets.")
    assert r["doc_ids"] is None and len(r["choices"]) == 2


def test_question_across_reports_keeps_several():
    from app.services.scope.doc_picker import across_documents
    assert across_documents("Compare the targets across the Q1 2031 reports")
    assert across_documents("What do both agreements say about traceability?")
    assert across_documents("Where do the two policies disagree?")
    assert not across_documents("For Q1 2031, compare bookings, revenue and backlog with their targets.")
    assert not across_documents("Which region had the highest bookings?")
