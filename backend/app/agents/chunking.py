"""Chunk + Embed Agent (L2) — semantic chunking + local BGE embedding.

Simple sliding-window chunker over whitespace-tokenized text. Good enough
for RAG-quality retrieval without pulling in a heavyweight tokenizer
dependency; token counts here are word-count approximations.
"""
from app.config import get_settings
from app.embeddings.bge import get_embedder
from app.models.schemas import Chunk, FileCategory, ParsedDocument


def _split_into_chunks(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(size - overlap, 1)
    for start in range(0, len(words), step):
        window = words[start : start + size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + size >= len(words):
            break
    return chunks


async def chunk_and_embed(doc: ParsedDocument) -> list[Chunk]:
    settings = get_settings()
    embedder = await get_embedder()

    raw_chunks: list[Chunk] = []
    for block in doc.text_blocks:
        if not block.text.strip():
            continue
        for piece in _split_into_chunks(block.text, settings.chunk_size_tokens, settings.chunk_overlap_tokens):
            raw_chunks.append(Chunk(source_file=doc.source_file, text=piece, page=block.page, category=doc.category))

    # JSON's text_blocks above already carry each record's full raw
    # content (json_parser.py dumps one text_block per record, which is
    # also what NER/PII/Financial extraction reads via doc.full_text()) --
    # embedding doc.tables' flattened preview here too would re-embed the
    # same records a second time in a second, lossier format. CSV/Excel
    # have no such overlap (their text_blocks are just a column-stats
    # summary, never per-row content), so this table-preview chunk is
    # their only source of row-level embeddings and stays as-is.
    if doc.category != FileCategory.JSON_:
        for table in doc.tables:
            if table.rows:
                preview = ", ".join(table.headers) + "\n" + "\n".join(
                    " | ".join(row) for row in table.rows[:20]
                )
                raw_chunks.append(
                    Chunk(source_file=doc.source_file, text=preview, page=table.page, category=doc.category)
                )

    if not raw_chunks:
        return []

    vectors = await embedder.embed_passages([c.text for c in raw_chunks])
    for chunk, vec in zip(raw_chunks, vectors):
        chunk.embedding = vec
    return raw_chunks
