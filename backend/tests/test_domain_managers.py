"""JSON was previously excluded from _STRUCTURED_CATEGORIES -- an
uploaded JSON file got zero support from chat's text-to-SQL branch even
though json_parser.py already produces the same TableBlock shape
CSV/Excel do.
"""
import json

import pytest

from app.agents import domain_managers
from app.agents.domain_managers import _STRUCTURED_CATEGORIES, process_file
from app.models.schemas import FileCategory


def test_json_is_a_structured_category():
    assert FileCategory.JSON_ in _STRUCTURED_CATEGORIES


# ---------------------------------------- non-LLM PII when backend is down --
# Issue: detect_standard_ids() (the local, deterministic, checksum-
# verified detector for national IDs/cards/IBANs) ran only inside the PII
# Extractor step, which sat behind an early-return that skipped the
# entire NER/PII/Financial/Relation block outright whenever the
# extraction backend was unreachable. A deployment with a temporarily-
# down LLM lost ALL PII detection, including the part that never
# depended on it.

@pytest.fixture(autouse=True)
def _skip_chunk_and_translate(monkeypatch):
    """Neither is relevant to this fix -- stub both out so the test only
    depends on the real JSON parser and the real (non-LLM) ID detector."""
    async def _fake_chunk_and_embed(doc):
        return []

    async def _fake_translate(doc, unreachable_backends):
        return doc

    monkeypatch.setattr(domain_managers, "chunk_and_embed", _fake_chunk_and_embed)
    monkeypatch.setattr(domain_managers, "translate_document", _fake_translate)


@pytest.mark.asyncio
async def test_non_llm_pii_detection_still_runs_when_backend_unreachable(tmp_path):
    path = tmp_path / "employee.json"
    path.write_text(json.dumps([{"name": "Alice", "national_id": "784-1990-1234567-1"}]))

    result = await process_file(str(path), unreachable_backends={"kimi"}, job=None)

    assert len(result.pii_findings) >= 1
    assert any(f.category == "EMIRATES_ID" for f in result.pii_findings)
    # NER/Financial/Relation genuinely have no non-LLM fallback -- these
    # must still be empty/skipped.
    assert result.entities == []
    assert result.relations == []
    assert result.financial_facts == []
    assert any("skipped" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_pii_detection_is_not_duplicated_when_backend_is_reachable(monkeypatch, tmp_path):
    """The non-LLM detector's findings and the LLM's findings must both
    land in pii_findings when the backend IS reachable -- confirms the
    restructuring didn't accidentally drop or double-count either half."""
    async def _fake_run_ner(text, source_file):
        return [], None

    async def _fake_run_pii(text, source_file, entities=None):
        from app.models.schemas import PIIFinding
        return [PIIFinding(category="OTHER_PII", value_redacted="***", severity="low",
                            source_file=source_file)], None

    async def _fake_run_financial(text, source_file):
        return [], None

    async def _fake_run_relations(text, entities, source_file):
        return [], None

    monkeypatch.setattr(domain_managers, "run_ner", _fake_run_ner)
    monkeypatch.setattr(domain_managers, "run_pii", _fake_run_pii)
    monkeypatch.setattr(domain_managers, "run_financial", _fake_run_financial)
    monkeypatch.setattr(domain_managers, "run_relations", _fake_run_relations)

    path = tmp_path / "employee.json"
    path.write_text(json.dumps([{"name": "Alice", "national_id": "784-1990-1234567-1"}]))

    result = await process_file(str(path), unreachable_backends=set(), job=None)

    categories = [f.category for f in result.pii_findings]
    assert "EMIRATES_ID" in categories  # non-LLM detector's finding
    assert "OTHER_PII" in categories    # the (fake) LLM's finding
    assert len(result.pii_findings) == 2  # neither dropped nor duplicated
