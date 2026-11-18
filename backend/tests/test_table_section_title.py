"""10 Nov 2026: table passages carry the report's real section heading, not the generated name."""
import json

from app.services.retriever_service import _with_section_title


def test_json_title_replaced_and_generated_name_kept():
    raw = json.dumps({"title": "Financial Performance Variance Analysis", "rows": [["Revenue", "76,312"]]})
    out = json.loads(_with_section_title(raw, "Variance against plan - Shipments by Distribution Centre"))
    assert out["title"] == "Variance against plan - Shipments by Distribution Centre"
    assert out["summary_title"] == "Financial Performance Variance Analysis" and out["rows"]


def test_plain_text_gets_a_title_line_once():
    assert _with_section_title("Line item | Actual", "KPIs - Q2 2026") == "KPIs - Q2 2026\nLine item | Actual"
    assert _with_section_title("KPIs - Q2 2026 — 7 rows", "KPIs - Q2 2026") == "KPIs - Q2 2026 — 7 rows"
    assert _with_section_title("text", None) == "text"
