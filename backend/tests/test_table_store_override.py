"""6 Nov 2026: a table-shaped question adds the table store — it must not drop the document text."""
from app.services.agents.agentic_pipeline import _with_table_store


def test_text_stores_are_kept_and_table_added():
    hint = {"stores": ["vector", "clause"], "doc_types": ["policy"], "confidence": 0.9}
    out = _with_table_store(hint, "filter")
    assert out["stores"] == ["vector", "clause", "table"] and out["doc_types"] is None


def test_table_only_hint_gains_document_text():
    out = _with_table_store({"stores": ["table"], "doc_types": ["financial"]}, "mixed")
    assert set(out["stores"]) == {"table", "vector"} and out["doc_types"] is None


def test_no_hint_means_every_store_and_stays_that_way():
    assert _with_table_store(None, "exact_lookup") is None


def test_plain_questions_are_untouched():
    hint = {"stores": ["vector"], "doc_types": ["policy"]}
    assert _with_table_store(hint, "semantic_qa") is hint


def test_plain_question_with_a_tables_only_hint_also_searches_text():
    # 9 Nov 2026 (shipment evaluation Q15): RAVEN said "tables only", the figures were in the text.
    out = _with_table_store({"stores": ["table"], "doc_types": ["financial"]}, "semantic_qa")
    assert out["stores"] == ["table", "vector"] and out["doc_types"] is None
