"""Local in-memory vector search -- the chat fallback used when Neo4j (the
primary GraphRAG store) is unreachable.

Chunk embeddings are computed by chunk_and_embed() for every file
unconditionally, regardless of whether Neo4j is reachable -- the only thing
Neo4j adds is the vector *index* itself and the entity/relation graph
expansion. So when Neo4j is down, chat doesn't need to fail outright: it can
fall back to plain cosine-similarity search over the same Chunk objects the
orchestrator already holds in memory (job_manager's per-job DomainResult
list), at the cost of losing the graph-facts / cross-document relationship
reasoning GraphRAG normally adds.

This is a brute-force O(n) scan, not an index -- fine at the scale of a
single job's chunks (typically low thousands at most), and avoids standing
up a second real vector store just for a degraded-mode fallback. Like the
job_manager state it reads from, it only lives in memory for the lifetime
of the backend process that ran the job.
"""
from app.models.schemas import Chunk, FileCategory


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(
    chunks: list[Chunk], query_vector: list[float], top_k: int = 8,
    categories: list[FileCategory] | None = None,
) -> list[dict]:
    """categories: same optional pre-filter as Neo4jStore.vector_search --
    None/empty searches every chunk. If a filter is given but matches
    nothing in this job's chunks, it's dropped rather than returning zero
    results, for the same reason: a category guess should narrow the
    search when it helps, never make an already-degraded fallback path
    even worse."""
    candidates = chunks
    if categories:
        narrowed = [c for c in chunks if c.category in categories]
        if narrowed:
            candidates = narrowed

    scored = [
        {
            "chunk_id": c.chunk_id, "text": c.text,
            "source_file": c.source_file, "page": c.page,
            "score": _cosine(c.embedding, query_vector),
        }
        for c in candidates if c.embedding
    ]
    scored.sort(key=lambda h: h["score"], reverse=True)
    return scored[:top_k]
