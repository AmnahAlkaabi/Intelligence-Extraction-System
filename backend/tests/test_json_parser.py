"""Tests for parsers/json_parser.py -- previously zero coverage for this
parser (or most others). Covers the truncation/warning behavior mirrored
from CSV/Excel, the BOM/deep-nesting robustness fixes, and the
scalar/list top-level-array table gap.
"""
import json

import pytest

from app.models.schemas import FileCategory
from app.parsers import json_parser
from app.parsers.json_parser import JSONParser


def _write(tmp_path, name: str, content: str, encoding: str = "utf-8") -> str:
    path = tmp_path / name
    path.write_bytes(content.encode(encoding))
    return str(path)


async def _parse(path: str):
    return await JSONParser().parse(path)


@pytest.mark.asyncio
async def test_array_of_dicts_basic(tmp_path):
    records = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
    path = _write(tmp_path, "people.json", json.dumps(records))

    doc = await _parse(path)

    assert not doc.warnings
    assert doc.metadata["record_count"] == 2
    assert doc.tables
    table = doc.tables[0]
    assert set(table.headers) >= {"name", "age"}
    assert len(table.rows) == 2
    assert not doc.full_tables  # under MAX_PREVIEW_RECORDS -- no separate full copy needed


@pytest.mark.asyncio
async def test_single_top_level_object(tmp_path):
    path = _write(tmp_path, "one.json", json.dumps({"name": "Alice", "age": 30}))

    doc = await _parse(path)

    assert not doc.warnings
    assert doc.metadata["record_count"] == 1
    assert doc.tables[0].rows == [["Alice", "30"]] or len(doc.tables[0].rows) == 1


@pytest.mark.asyncio
async def test_jsonl_skips_bad_lines_without_losing_good_ones(tmp_path):
    content = '{"a": 1}\n' + "not json\n" + '{"a": 2}\n'
    path = _write(tmp_path, "data.jsonl", content)

    doc = await _parse(path)

    assert doc.metadata["record_count"] == 2
    assert any("line 2" in w for w in doc.warnings)


@pytest.mark.asyncio
async def test_utf8_bom_is_tolerated(tmp_path):
    """Issue: hardcoded strict utf-8 decoding rejected common
    Windows-exported JSON with a leading BOM."""
    records = [{"name": "Alice"}]
    # utf-8-sig encoding prepends the BOM bytes (EF BB BF) automatically.
    path = _write(tmp_path, "bom.json", json.dumps(records), encoding="utf-8-sig")

    doc = await _parse(path)

    assert not doc.warnings
    assert doc.metadata["record_count"] == 1


@pytest.mark.asyncio
async def test_jsonl_utf8_bom_is_tolerated(tmp_path):
    path = _write(tmp_path, "bom.jsonl", '{"a": 1}\n{"a": 2}\n', encoding="utf-8-sig")

    doc = await _parse(path)

    assert not doc.warnings
    assert doc.metadata["record_count"] == 2


@pytest.mark.asyncio
async def test_deeply_nested_small_file_does_not_crash(tmp_path):
    """Issue: a few-KB deeply-nested file blew Python's recursion limit
    inside the flattener and surfaced as a confusing raw error."""
    depth = 2000
    nested = "[" * depth + "1" + "]" * depth
    path = _write(tmp_path, "deep.json", f"[{nested}]")

    doc = await _parse(path)

    # Must not raise RecursionError out of parse(); either it parses
    # (with a bounded flattened representation) or reports a clear,
    # specific warning -- never a bare "maximum recursion depth exceeded".
    for w in doc.warnings:
        assert "maximum recursion depth" not in w.lower()


@pytest.mark.asyncio
async def test_extremely_deep_single_object_reports_clear_warning(tmp_path):
    depth = 60_000  # past json.load's own C-level recursion ceiling
    nested = "[" * depth + "1" + "]" * depth
    path = _write(tmp_path, "toodeep.json", nested)  # top-level value itself, not wrapped in an array

    doc = await _parse(path)

    assert doc.warnings
    assert "nested too deeply" in doc.warnings[0].lower()
    assert "recursion depth exceeded" not in doc.warnings[0].lower()


@pytest.mark.asyncio
async def test_top_level_array_of_scalars_gets_a_table(tmp_path):
    """Issue: json_parser only built a table when records were dicts --
    ["a", "b", "c"] produced zero Data Dump table."""
    path = _write(tmp_path, "scalars.json", json.dumps(["a", "b", "c"]))

    doc = await _parse(path)

    assert doc.tables
    assert doc.tables[0].rows == [["a"], ["b"], ["c"]]


@pytest.mark.asyncio
async def test_top_level_array_of_lists_gets_a_table(tmp_path):
    path = _write(tmp_path, "lists.json", json.dumps([[1, 2], [3, 4]]))

    doc = await _parse(path)

    assert doc.tables
    assert len(doc.tables[0].rows) == 2


@pytest.mark.asyncio
async def test_nested_list_surfaces_more_than_five_items(tmp_path):
    """Issue: a customer record with 50 transactions only showed the
    first 5, with no way to recover the rest from the extracted view."""
    record = {"customer": "Acme", "transactions": [{"amount": i} for i in range(50)]}
    path = _write(tmp_path, "txns.json", json.dumps([record]))

    doc = await _parse(path)

    headers = doc.tables[0].headers
    shown_indices = [h for h in headers if h.startswith("transactions[")]
    assert len(shown_indices) > 5
    assert len(shown_indices) <= json_parser.MAX_NESTED_LIST_ITEMS
    count_col = headers.index("transactions")
    assert doc.tables[0].rows[0][count_col] == "[50 items, first 20 shown]"


@pytest.mark.asyncio
async def test_large_file_keeps_full_data_for_structured_store(tmp_path, monkeypatch):
    """Issue: large JSON files silently lost data past the first 200
    records everywhere downstream, with no warning. full_tables must
    carry every record (up to the structured ceiling) for chat's SQL
    store, and a warning must appear only once that much larger ceiling
    is itself exceeded."""
    # Patched ceiling stays above MAX_PREVIEW_RECORDS (200), same relative
    # ordering as production (MAX_STRUCTURED_RECORDS=200_000) -- otherwise
    # the structured cap would truncate `records` before the preview slice
    # ever runs, which isn't a scenario production values allow.
    monkeypatch.setattr(json_parser, "MAX_STRUCTURED_RECORDS", 250)
    records = [{"id": i} for i in range(300)]
    path = _write(tmp_path, "big.json", json.dumps(records))

    doc = await _parse(path)

    assert doc.metadata["record_count"] == 300
    assert len(doc.tables[0].rows) == json_parser.MAX_PREVIEW_RECORDS  # on-screen preview stays capped
    assert doc.full_tables
    assert len(doc.full_tables[0].rows) == 250  # capped at the (patched) structured ceiling
    assert any("only indexed the first 250 of 300" in w for w in doc.warnings)


@pytest.mark.asyncio
async def test_full_tables_not_populated_when_under_preview_cap(tmp_path):
    records = [{"id": i} for i in range(10)]
    path = _write(tmp_path, "small.json", json.dumps(records))

    doc = await _parse(path)

    assert not doc.full_tables
    assert not doc.warnings


@pytest.mark.asyncio
async def test_duplicate_keys_in_top_level_array_produce_a_warning(tmp_path):
    """Issue: RFC 8259 leaves duplicate object keys implementation-
    defined; Python's json module silently keeps only the last value,
    exactly like a dict literal would -- real, silent data loss from the
    uploader's point of view with zero indication anything happened."""
    raw = '[{"id": 1, "name": "Alice", "id": 101}, {"id": 2, "name": "Bob"}]'
    path = _write(tmp_path, "dupes.json", raw)

    doc = await _parse(path)

    assert any("duplicate object key" in w.lower() for w in doc.warnings)
    assert any("'id'" in w for w in doc.warnings)
    # Last-value-wins is still the correct, unavoidable parse result --
    # the fix is surfacing it, not changing which value survives.
    assert doc.tables[0].rows[0][doc.tables[0].headers.index("id")] == "101"


@pytest.mark.asyncio
async def test_duplicate_keys_in_nested_object_are_also_caught(tmp_path):
    raw = '[{"id": 1, "address": {"city": "Dubai", "city": "Abu Dhabi"}}]'
    path = _write(tmp_path, "nested_dupes.json", raw)

    doc = await _parse(path)

    assert any("duplicate object key" in w.lower() for w in doc.warnings)
    assert any("'city'" in w for w in doc.warnings)


@pytest.mark.asyncio
async def test_duplicate_keys_in_jsonl_are_also_caught(tmp_path):
    raw = '{"id": 1, "id": 2}\n{"id": 3}\n'
    path = _write(tmp_path, "dupes.jsonl", raw)

    doc = await _parse(path)

    assert any("duplicate object key" in w.lower() for w in doc.warnings)


@pytest.mark.asyncio
async def test_duplicate_keys_in_single_top_level_object_are_caught(tmp_path):
    raw = '{"id": 1, "name": "Alice", "id": 101}'
    path = _write(tmp_path, "one_dupe.json", raw)

    doc = await _parse(path)

    assert any("duplicate object key" in w.lower() for w in doc.warnings)


@pytest.mark.asyncio
async def test_no_duplicate_key_warning_when_none_present(tmp_path):
    path = _write(tmp_path, "clean.json", json.dumps([{"id": 1, "name": "Alice"}]))

    doc = await _parse(path)

    assert not any("duplicate object key" in w.lower() for w in doc.warnings)
