"""End-to-end check that a JSON file's table actually lands in and can be
queried from the structured-query SQLite store -- domain_managers.py's
_STRUCTURED_CATEGORIES gate (see test_domain_managers.py) is only half of
issue #1 ("JSON files can't be queried by chat's SQL feature at all");
this confirms the table json_parser.py now produces is actually usable by
structured_store.write_tables/read_manifest, the same as a CSV/Excel
table would be.
"""
import json

import pytest

from app.models.schemas import FileCategory
from app.parsers.json_parser import JSONParser
from app.storage import structured_store


@pytest.fixture(autouse=True)
def _job_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(structured_store, "job_output_dir", lambda job_id: tmp_path)


@pytest.mark.asyncio
async def test_json_table_is_written_and_queryable(tmp_path):
    records = [{"name": "Alice", "amount": 100}, {"name": "Bob", "amount": 250}]
    src = tmp_path / "orders.json"
    src.write_text(json.dumps(records))

    doc = await JSONParser().parse(str(src))
    created = structured_store.write_tables("job1", str(src), doc.tables, FileCategory.JSON_)

    assert created
    manifest = structured_store.read_manifest("job1")
    assert manifest[0]["category"] == "json"

    conn = structured_store.open_readonly("job1")
    try:
        total = conn.execute(f'SELECT SUM(amount) FROM "{created[0]}"').fetchone()[0]
    finally:
        conn.close()
    assert total == 350
