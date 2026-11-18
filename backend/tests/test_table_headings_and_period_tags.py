"""Table titles from section headings + document period tags (Nov 2026). No Docling, no DB."""
from types import SimpleNamespace

from app.models.document import ExtractedTable
from app.services import document_parser as dp
from app.services import document_tags as dt


class _Item:
    def __init__(self, kind, text="", ref=None):
        self.__class__ = type(kind, (), {})
        self.text, self.self_ref = text, ref


class _Doc:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return [(i, 0) for i in self._items]


def test_heading_map_follows_reading_order():
    doc = _Doc([_Item("SectionHeaderItem", "Variance against plan - Q2 2026 (USD 000)"),
                _Item("TextItem", "Variance is actual less plan."),
                _Item("TableItem", ref="#/tables/0"),
                _Item("SectionHeaderItem", "Key performance indicators - Q2 2026"),
                _Item("TableItem", ref="#/tables/1")])
    m = dp._table_section_headings(doc)
    assert m == {"#/tables/0": "Variance against plan - Q2 2026 (USD 000)",
                 "#/tables/1": "Key performance indicators - Q2 2026"}


def _t(caption=None, heading=None):
    return ExtractedTable(table_index=0, page_number=1, headers=["Line item", "Actual"], rows=[["Revenue", "1"]],
                          caption=caption, table_metadata={"section_heading": heading} if heading else {})


def test_heading_becomes_title_and_prefixes_a_caption():
    a, b, c, d = _t(heading="Variance against plan - Q2 2026"), _t("Table 3", "Variance against plan - Q2 2026"), \
        _t("Variance against plan - Q2 2026 (USD 000)", "Variance against plan - Q2 2026"), _t("Table 9")
    dp._apply_table_headings([a, b, c, d])
    assert a.caption == "Variance against plan - Q2 2026"
    assert b.caption == "Variance against plan - Q2 2026 — Table 3"
    assert c.caption == "Variance against plan - Q2 2026 (USD 000)"          # already contains it
    assert d.caption == "Table 9"                                             # no heading: unchanged
    assert a.rows == [["Revenue", "1"]] and a.headers == ["Line item", "Actual"]


def test_period_normaliser():
    assert dt.periods_in("Variance against plan - Q2 2026") == ["2026-Q2"]
    assert dt.periods_in("Sales_Shipment_Performance_Report_May_2026.pdf") == ["2026-05", "2026-Q2"]
    assert dt.periods_in("Bookings for FY2025 reached") == ["FY2025"]
    assert set(dt.periods_in("2026 Q3 and H1 2025")) == {"2026-Q3", "2025-H1"}


def test_compute_periods_keeps_names_headings_and_repeated_text_periods():
    tags = dt.compute_periods(
        ["Sales_Shipment_Exception_Report_SHP-2025-81218.pdf", "Exception Report"],
        ["Customer Impact"],
        ["Bookings for FY2025 reached USD 89.0m", "On-time delivery was 94.0% in FY2025",
         "a trend from Q2 2022 shown once"])
    assert tags["periods"] == ["FY2025"]
    assert "2022-Q2" not in tags["periods"]
