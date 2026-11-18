"""Passage post-processing (app/services/scope/passages.py). No database: get_db is faked.

Partly ported from the Manthan platform's tests (test_doc_scope.py, test_answer_quality_round2.py).
"""
import json
from contextlib import contextmanager

from app.services.scope import passages as P


def _fake_db(monkeypatch, rows):
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return list(rows)

    class _Conn:
        def cursor(self):
            return _Cur()

    @contextmanager
    def _get_db():
        yield _Conn()

    monkeypatch.setattr(P, "get_db", _get_db)


def test_stored_wrapper_is_unwrapped():
    w = json.dumps({"detected_store": "Table Store",
                    "structured_content": {"title": "x", "headers": ["A"], "rows": [["1"]]}})
    out = json.loads(P.table_passage(w, "Product detail - Q1 2031"))
    assert out["rows"] == [["1"]] and out["title"] == "Product detail - Q1 2031"
    assert json.loads(P.unwrap_table_text("Product detail\n" + w))["rows"] == [["1"]]
    assert P.unwrap_table_text("plain text") == "plain text"


def test_added_tables_sit_right_after_the_passage_that_called_for_them():
    pf = [{"chunk_text": f"p{i}"} for i in range(10)]
    out = P.insert_after_callers(pf, [{"chunk_text": "T0", "_after": 0}, {"chunk_text": "T5", "_after": 5}])
    texts = [x["chunk_text"] for x in out]
    assert texts[:3] == ["p0", "T0", "p1"] and texts[texts.index("p5") + 1] == "T5"
    assert all("_after" not in x for x in out)


def test_whole_document_in_page_order_with_tables(monkeypatch):
    rows = [  # (doc id, name, page, index, kind, text, structured, title)
        ("d1", "Report.pdf", 2, 0, 0, "page two text", None, None),
        ("d1", "Report.pdf", 1, 0, 1, None, json.dumps({"headers": ["A"], "rows": [["1"]]}), "Totals"),
        ("d1", "Report.pdf", 1, 0, 0, "page one text", None, None),
        ("d1", "Report.pdf", 3, 0, 2, "4.1 Term\nThe agreement runs two years.", None, None),
    ]
    _fake_db(monkeypatch, rows)
    out = P.full_document_passages(["d1"])
    assert [p["page_number"] for p in out] == [1, 1, 2, 3]
    assert out[0]["chunk_text"] == "page one text" and out[1]["store_type"] == "table"
    assert json.loads(out[1]["chunk_text"])["title"] == "Totals"
    assert out[3]["store_type"] == "clause" and all(p["document_id"] == "d1" for p in out)


def test_long_or_too_many_documents_fall_back(monkeypatch):
    _fake_db(monkeypatch, [("d1", "R.pdf", 1, 0, 0, "x" * (P.FULL_DOC_MAX_CHARS + 1), None, None)])
    assert P.full_document_passages(["d1"]) is None
    assert P.full_document_passages(["a", "b", "c"][: P.FULL_DOC_MAX_DOCS + 1]) is None
    assert P.full_document_passages([]) is None


def test_cut_off_table_is_repaired(monkeypatch):
    full = json.dumps({"title": "Aged balances", "headers": ["A"], "rows": [["1"], ["2"]]})
    _fake_db(monkeypatch, [("d1", "Aged balances", full)])
    ps = [{"document_id": "d1", "store_type": "table", "chunk_text": 'Aged balances\n{"title": "Aged balances", "rows": [["1"'}]
    assert P.repair_table_passages(ps) == 1
    assert json.loads(ps[0]["chunk_text"])["rows"] == [["1"], ["2"]]


def test_companion_table_for_a_section_found_as_text(monkeypatch):
    table = json.dumps({"headers": ["Product", "Units"], "rows": [["X", "5"]]})
    _fake_db(monkeypatch, [("Report.pdf", 4, "Product detail - Q2 2026", table, "d1"),
                           ("Report.pdf", 9, "Unrelated table", table, "d1")])
    ps = [{"document_id": "d1", "store_type": "vector", "chunk_text": "Context: Product detail - Q2 2026\nUnits rose."}]
    extra = P.companion_tables(ps)
    assert len(extra) == 1 and extra[0]["section_title"] == "Product detail - Q2 2026"
    assert extra[0]["added"] == "companion_table" and extra[0]["document_id"] == "d1"
    assert P.companion_tables([{"document_id": "d1", "store_type": "vector", "chunk_text": "no section"}]) == []
