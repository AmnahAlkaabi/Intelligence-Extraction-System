"""Tests for agents/chunking.py's JSON/table double-embedding fix --
JSON records were previously embedded twice (once via json_parser.py's
per-record text_blocks, again via the flattened-preview TableBlock that
every category's table gets embedded from), unlike CSV/Excel which only
embed their row data once (their text_blocks are just a column-stats
summary, so the table-preview chunk is their only source of row content).
"""
import pytest

from app.agents import chunking
from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock


class _FakeEmbedder:
    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    async def _get_embedder():
        return _FakeEmbedder()
    monkeypatch.setattr(chunking, "get_embedder", _get_embedder)


def _doc(category: FileCategory) -> ParsedDocument:
    doc = ParsedDocument(source_file="f", category=category)
    doc.text_blocks.append(TextBlock(text="record one content", kind="paragraph"))
    doc.tables.append(TableBlock(headers=["a"], rows=[["1"]], caption="Preview"))
    return doc


@pytest.mark.asyncio
async def test_json_table_preview_is_not_separately_embedded():
    chunks = await chunking.chunk_and_embed(_doc(FileCategory.JSON_))

    # Only the text_block-derived chunk should exist -- the table preview
    # (redundant with the record dump already in text_blocks) must not
    # produce a second chunk.
    assert len(chunks) == 1
    assert chunks[0].text == "record one content"


@pytest.mark.asyncio
async def test_csv_table_preview_is_still_embedded():
    chunks = await chunking.chunk_and_embed(_doc(FileCategory.CSV))

    # CSV has no per-row text_blocks, so its table preview is the only
    # source of row-level content and must still produce its own chunk.
    assert len(chunks) == 2
    assert any("a" in c.text and "1" in c.text for c in chunks)
