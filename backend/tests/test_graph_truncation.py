"""Graph extraction replies cut off by the output-token limit are salvaged, not thrown away."""
import json

from app.services import graph_extraction_service as g

FULL = {
    "entities": [
        {"name": "Prabhat Systems", "type": "org", "description": 'Customer "PSE".', "confidence": 1.0},
        {"name": "INV-2025-55735", "type": "concept", "description": "Invoice {draft}.", "confidence": 0.9},
    ],
    "relationships": [
        {"source": "INV-2025-55735", "target": "Prabhat Systems", "type": "ISSUED_TO", "description": "a", "confidence": 1.0},
        {"source": "Prabhat Systems", "target": "Channel Sales", "type": "SERVED_BY", "description": "b", "confidence": 1.0},
    ],
}
TEXT = json.dumps(FULL, indent=2)


def test_complete_reply_unchanged():
    r = g._parse_graph_response(TEXT)
    assert len(r["entities"]) == 2 and len(r["relationships"]) == 2


def test_cut_inside_relationships_keeps_complete_items():
    cut = TEXT[: TEXT.index('"Channel Sales"') + 5]        # cut mid-way through the 2nd relationship
    r = g._parse_graph_response(cut)
    assert [e["name"] for e in r["entities"]] == ["Prabhat Systems", "INV-2025-55735"]
    assert len(r["relationships"]) == 1 and r["relationships"][0]["type"] == "ISSUED_TO"


def test_cut_inside_entities_keeps_first_entity():
    cut = TEXT[: TEXT.index('"INV-2025-55735"') + 4]
    r = g._parse_graph_response(cut)
    assert [e["name"] for e in r["entities"]] == ["Prabhat Systems"] and r["relationships"] == []


def test_braces_and_quotes_inside_strings_do_not_confuse_the_scan():
    cut = TEXT[: TEXT.index("{draft}") + 3]                # cut inside a string holding a brace
    r = g._parse_graph_response(cut)
    assert [e["name"] for e in r["entities"]] == ["Prabhat Systems"]


def test_fenced_and_cut_reply():
    r = g._parse_graph_response("```json\n" + TEXT[: TEXT.index('"SERVED_BY"')])
    assert len(r["relationships"]) == 1


def test_nothing_salvageable():
    assert g._parse_graph_response('{"entities": [ {"name": "X", "ty') is None
    assert g._parse_graph_response("not json at all") is None
