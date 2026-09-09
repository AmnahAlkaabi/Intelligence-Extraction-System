"""End-to-end coverage for a JSON upload through the real per-file
pipeline (agents/domain_managers.process_file), not just the parser in
isolation (test_json_parser.py) or the parser+store wiring called
directly (test_structured_store_json.py).

test_domain_managers.py's existing process_file() tests all pass
job=None, which skips the job-scoped side effects entirely (structured
store mirror, dataset library save, job.agent_activity tracking -- see
domain_managers.py:159-187). Nobody had confirmed those branches actually
fire for a real JSON upload going through the real pipeline end to end;
this file does, with only the network-bound pieces (LLM calls, the real
embedding model) faked out.
"""
import json

import pytest

from app.agents import domain_managers
from app.agents.domain_managers import process_file
from app.models.schemas import Job
from app.storage import dataset_library, structured_store


@pytest.fixture(autouse=True)
def _job_scoped_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(structured_store, "job_output_dir", lambda job_id: tmp_path)
    monkeypatch.setattr(dataset_library, "library_db_path", lambda: tmp_path / "library.db")


@pytest.fixture(autouse=True)
def _skip_translate(monkeypatch):
    async def _fake_translate(doc, unreachable_backends):
        return doc

    monkeypatch.setattr(domain_managers, "translate_document", _fake_translate)


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    """Real chunk_and_embed / chunking logic runs as-is -- only the actual
    BGE model call (multi-hundred-MB, GPU/CPU-bound) is faked, so this
    still exercises the real chunker + the JSON/table double-embedding
    exclusion (see chunking.py) rather than skipping the step outright."""
    from app.agents import chunking

    class _FakeEmbedder:
        async def embed_passages(self, texts):
            return [[0.0, 0.0, 0.0, 0.0] for _ in texts]

    async def _fake_get_embedder():
        return _FakeEmbedder()

    monkeypatch.setattr(chunking, "get_embedder", _fake_get_embedder)


def _mock_llm_agents(monkeypatch):
    async def _fake_run_ner(text, source_file):
        return [], None

    async def _fake_run_pii(text, source_file, entities=None):
        return [], None

    async def _fake_run_financial(text, source_file):
        return [], None

    async def _fake_run_relations(text, entities, source_file):
        return [], None

    async def _fake_run_summary(text):
        return "summary"

    monkeypatch.setattr(domain_managers, "run_ner", _fake_run_ner)
    monkeypatch.setattr(domain_managers, "run_pii", _fake_run_pii)
    monkeypatch.setattr(domain_managers, "run_financial", _fake_run_financial)
    monkeypatch.setattr(domain_managers, "run_relations", _fake_run_relations)
    monkeypatch.setattr(domain_managers, "run_summary", _fake_run_summary)


@pytest.mark.asyncio
async def test_json_upload_lands_in_structured_store_via_process_file(monkeypatch, tmp_path):
    _mock_llm_agents(monkeypatch)
    path = tmp_path / "orders.json"
    path.write_text(json.dumps([
        {"customer": "Alice", "amount": 100},
        {"customer": "Bob", "amount": 250},
    ]))
    job = Job()

    result = await process_file(str(path), unreachable_backends=set(), job=job)

    assert result.quality is not None
    manifest = structured_store.read_manifest(job.job_id)
    assert manifest and manifest[0]["category"] == "json"
    conn = structured_store.open_readonly(job.job_id)
    try:
        total = conn.execute(f'SELECT SUM(amount) FROM "{manifest[0]["table_name"]}"').fetchone()[0]
    finally:
        conn.close()
    assert total == 350


@pytest.mark.asyncio
async def test_json_upload_saved_to_dataset_library(monkeypatch, tmp_path):
    _mock_llm_agents(monkeypatch)
    path = tmp_path / "orders.json"
    path.write_text(json.dumps([{"customer": "Alice", "amount": 100}]))
    job = Job()

    await process_file(str(path), unreachable_backends=set(), job=job)

    datasets = dataset_library.list_datasets(job_id=job.job_id)
    assert datasets
    assert datasets[0].category == "json"


@pytest.mark.asyncio
async def test_json_specialist_activity_tracked_on_job(monkeypatch, tmp_path):
    _mock_llm_agents(monkeypatch)
    path = tmp_path / "orders.json"
    path.write_text(json.dumps([{"customer": "Alice", "amount": 100}]))
    job = Job()

    await process_file(str(path), unreachable_backends=set(), job=job)

    parser_activities = [a for a in job.agent_activity if a.agent == "JSON Specialist"]
    assert parser_activities
    assert parser_activities[0].status == "completed"


@pytest.mark.asyncio
async def test_json_upload_produces_embedded_chunks_from_records(monkeypatch, tmp_path):
    """Confirms the real chunker (not a stub) actually turns json_parser's
    per-record text_blocks into embedded Chunks -- and, per chunking.py's
    JSON exclusion, does NOT also embed the table preview a second time."""
    _mock_llm_agents(monkeypatch)
    path = tmp_path / "orders.json"
    path.write_text(json.dumps([{"customer": "Alice", "amount": 100}]))
    job = Job()

    result = await process_file(str(path), unreachable_backends=set(), job=job)

    assert result.chunks
    assert all(c.embedding for c in result.chunks)
    # One text_block for the summary + one per record (see json_parser.py)
    # -- none derived from doc.tables, so no chunk should contain the
    # table's own "headers ... | rows" preview formatting.
    assert not any("customer, amount" in c.text for c in result.chunks)


@pytest.mark.asyncio
async def test_large_json_upload_structured_index_warning_propagates_to_result(monkeypatch, tmp_path):
    """json_parser's MAX_STRUCTURED_RECORDS-exceeded warning (already unit
    tested against the parser directly in test_json_parser.py) must also
    surface on the DomainResult a real caller actually sees."""
    _mock_llm_agents(monkeypatch)
    from app.parsers import json_parser
    # Patched ceiling stays above MAX_PREVIEW_RECORDS (200) -- below it,
    # the warning is unreachable regardless of record_count (see
    # json_parser.py: the MAX_STRUCTURED_RECORDS check is nested inside
    # `if record_count > MAX_PREVIEW_RECORDS`), a scenario production
    # values (200_000 vs. 200) never allow. Same invariant
    # test_json_parser.py's own equivalent test observes.
    monkeypatch.setattr(json_parser, "MAX_STRUCTURED_RECORDS", 250)
    records = [{"id": i, "amount": i} for i in range(300)]
    path = tmp_path / "big.json"
    path.write_text(json.dumps(records))
    job = Job()

    result = await process_file(str(path), unreachable_backends=set(), job=job)

    assert any("only indexed the first 250 of 300" in e for e in result.errors)


@pytest.mark.asyncio
async def test_malformed_json_upload_does_not_crash_the_pipeline(monkeypatch, tmp_path):
    """A file that fails to parse at all (invalid JSON) must still
    produce a DomainResult with a warning, not raise out of process_file
    and abort the whole job for every other file alongside it."""
    _mock_llm_agents(monkeypatch)
    path = tmp_path / "broken.json"
    path.write_text("{not valid json,,,")
    job = Job()

    result = await process_file(str(path), unreachable_backends=set(), job=job)

    assert any("json parse error" in e.lower() for e in result.errors)
    assert result.quality is not None
    # An unparseable file has no tables to mirror into the structured
    # store -- confirm that's a clean no-op, not a silent crash swallowed
    # somewhere upstream.
    assert structured_store.read_manifest(job.job_id) == []
