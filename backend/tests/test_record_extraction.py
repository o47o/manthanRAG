"""Document records (migration 013): candidate filter, normalisation and grounding.
Pure functions only — no database, no Gemma call."""
from app.services import record_extraction_service as rx

PASSAGE = ("Order  SO-2025-11134  from  Prabhat  Systems Electronics Pvt. Ltd. was booked on "
           "2025-06-20 with a requested ship date of 2025-02-16. Distributor channel inventory "
           "closed the period at 8.5 weeks of supply.")


def _batch():
    return [{"chunk_id": "c1", "page": 1, "text": PASSAGE, "_sq": rx._squash(PASSAGE)}]


def test_candidate_needs_id_and_date_or_amount():
    assert rx.is_candidate(PASSAGE)
    assert rx.is_candidate("Invoice INV-2026-57326 total USD 12,400.50")
    assert not rx.is_candidate("Orders must be booked before the requested ship date.")
    assert not rx.is_candidate("Order SO-2025-11134 is pending review.")   # no date / amount


def test_normalise_dates_and_numbers():
    assert rx.normalise_value("2025-06-20") == {"value": "2025-06-20", "date": "2025-06-20"}
    assert rx.normalise_value("20 June 2025")["date"] == "2025-06-20"
    assert rx.normalise_value("05/03/2025")["date"] == "2025-03-05"          # day first
    assert rx.normalise_value("USD 1,250.00") == {"value": "USD 1,250.00", "number": 1250.0, "unit": "USD"}
    assert rx.normalise_value("$99")["unit"] == "USD"
    assert rx.normalise_value("Completed") == {"value": "Completed"}


def test_type_and_field_keys():
    assert rx.norm_type_key("Sales Orders") == "sales_order"
    assert rx.norm_type_key("Deliveries") == "delivery"
    assert rx.norm_key("Requested Ship Date") == "requested_ship_date"


def test_grounding_keeps_only_what_the_text_says():
    items = [
        {"p": 1, "record_type": "Sales order", "record_id": "SO-2025-11134",
         "fields": {"booked_date": "2025-06-20", "requested_ship_date": "2025-02-16",
                    "customer": "Prabhat Systems Electronics Pvt. Ltd.",
                    "amount": "USD 5,000"},                  # not in the text → dropped
         "source_text": "Order SO-2025-11134 …", "confidence": 0.9},
        {"p": 1, "record_type": "Sales order", "record_id": "SO-2025-99999",   # invented id
         "fields": {"booked_date": "2025-06-20"}},
        {"p": 1, "record_type": "Sales order", "record_id": "SO-XXXX",          # placeholder
         "fields": {"booked_date": "2025-06-20"}},
        {"p": 7, "record_type": "Sales order", "record_id": "SO-2025-11134",   # bad passage ref
         "fields": {"booked_date": "2025-06-20"}},
    ]
    out = rx.ground_records(items, _batch())
    assert len(out) == 1
    r = out[0]
    assert r["record_type_key"] == "sales_order" and r["record_id"] == "SO-2025-11134"
    assert set(r["fields"]) == {"booked_date", "requested_ship_date", "customer"}
    assert r["fields"]["requested_ship_date"]["date"] == "2025-02-16"
    assert r["chunk_id"] == "c1" and r["page_number"] == 1


def test_parse_json_array_tolerates_fences_and_wrappers():
    assert rx._parse_json_array('```json\n[{"p": 1}]\n```') == [{"p": 1}]
    assert rx._parse_json_array('Here: [{"p": 2}] done') == [{"p": 2}]
    assert rx._parse_json_array('{"records": [{"p": 3}]}') == [{"p": 3}]
    assert rx._parse_json_array("no json") == []


def test_dedupe_and_batches():
    r = {"record_type_key": "shipment", "record_id": "SHP-1001", "fields": {"a": {"value": "1"}}}
    assert len(rx.dedupe([r, dict(r)])) == 1
    ps = [{"text": "x" * 4000}, {"text": "y" * 3000}, {"text": "z" * 1000}]
    assert [len(b) for b in rx.batches(ps, 6000)] == [1, 2]


def test_large_tables_split_with_header():
    md = "| id | date |\n|---|---|\n" + "\n".join(f"| SO-{i:05d} | 2025-01-01 |" for i in range(400))
    parts = rx._split_table(md, limit=1000)
    assert len(parts) > 1 and all(p.startswith("| id | date |") for p in parts)
