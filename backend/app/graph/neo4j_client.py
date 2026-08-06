"""Neo4j client: schema setup, entity/relation/chunk ingest, vector index.

Neo4j doubles as both the knowledge graph store AND the vector index for
RAG chunks (via its native vector index, Neo4j 5.11+), so no separate
vector DB is required in the air-gapped stack.
"""
import asyncio
import logging

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import get_settings
from app.models.schemas import Chunk, Entity, FileCategory, Job, PIIFinding, Relation

logger = logging.getLogger(__name__)


class Neo4jStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )

    async def close(self) -> None:
        await self._driver.close()

    async def check_reachable(self, timeout_s: float = 8.0) -> tuple[bool, str | None]:
        try:
            async def _ping() -> None:
                async with self._driver.session(database=self._settings.neo4j_database) as session:
                    await session.run("RETURN 1")
            await asyncio.wait_for(_ping(), timeout=timeout_s)
            return True, None
        except Exception as exc:  # noqa: BLE001
            detail = f"Cannot reach Neo4j at {self._settings.neo4j_uri} ({exc.__class__.__name__}: {exc})"
            logger.warning(detail)
            return False, detail

    async def ensure_schema(self) -> None:
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            await session.run(
                "CREATE CONSTRAINT entity_key IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.job_id, e.name, e.type) IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
                "FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT pii_finding_id IF NOT EXISTS "
                "FOR (p:PIIFinding) REQUIRE p.finding_id IS UNIQUE"
            )
            try:
                await session.run(
                    f"""
                    CREATE VECTOR INDEX {settings.vector_index_name} IF NOT EXISTS
                    FOR (c:Chunk) ON (c.embedding)
                    OPTIONS {{indexConfig: {{
                        `vector.dimensions`: $dim,
                        `vector.similarity_function`: 'cosine'
                    }}}}
                    """,
                    dim=settings.embedding_dim,
                )
            except Exception:
                logger.exception(
                    "Vector index creation failed — confirm Neo4j >= 5.11 with vector index support."
                )

    async def ingest_job_graph(
        self,
        job_id: str,
        entities: list[Entity],
        relations: list[Relation],
        chunks: list[Chunk],
        pii_findings: list[PIIFinding] | None = None,
    ) -> None:
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            if entities:
                await session.run(
                    """
                    UNWIND $entities AS e
                    MERGE (n:Entity {job_id: $job_id, name: e.name, type: e.type})
                    ON CREATE SET n.confidence = e.confidence, n.source_files = [e.source_file]
                    ON MATCH SET n.confidence = CASE WHEN e.confidence > n.confidence
                                                 THEN e.confidence ELSE n.confidence END,
                                 n.source_files = CASE WHEN NOT e.source_file IN n.source_files
                                                  THEN n.source_files + e.source_file ELSE n.source_files END
                    """,
                    job_id=job_id,
                    entities=[e.model_dump(include={"name", "type", "confidence", "source_file"}) for e in entities],
                )

            if relations:
                await session.run(
                    """
                    UNWIND $relations AS r
                    MATCH (s:Entity {job_id: $job_id, name: r.source_entity})
                    MATCH (t:Entity {job_id: $job_id, name: r.target_entity})
                    MERGE (s)-[rel:RELATION {type: r.relation_type, job_id: $job_id}]->(t)
                    ON CREATE SET rel.evidence = r.evidence, rel.confidence = r.confidence,
                                  rel.source_file = r.source_file
                    """,
                    job_id=job_id,
                    relations=[
                        r.model_dump(include={
                            "source_entity", "target_entity", "relation_type",
                            "evidence", "confidence", "source_file",
                        })
                        for r in relations
                    ],
                )

            if pii_findings:
                await session.run(
                    """
                    UNWIND $findings AS f
                    MERGE (p:PIIFinding {finding_id: f.finding_id})
                    SET p.job_id = $job_id, p.category = f.category, p.value_redacted = f.value_redacted,
                        p.severity = f.severity, p.source_file = f.source_file, p.location = f.location
                    """,
                    job_id=job_id,
                    findings=[
                        f.model_dump(include={
                            "finding_id", "category", "value_redacted", "severity", "source_file", "location",
                        })
                        for f in pii_findings
                    ],
                )

                # Only findings the extraction step (or its single-subject
                # fallback -- see domain_managers.py) attributed to a
                # specific person become graph edges. Matched against
                # PERSON-typed entities specifically, not by name alone --
                # a same-named ORG/LOCATION entity should never pick up
                # someone else's PII by accident.
                attributed = [f for f in pii_findings if f.subject_entity]
                if attributed:
                    await session.run(
                        """
                        UNWIND $findings AS f
                        MATCH (e:Entity {job_id: $job_id, name: f.subject_entity, type: 'PERSON'})
                        MATCH (p:PIIFinding {finding_id: f.finding_id})
                        MERGE (e)-[:HAS_PII]->(p)
                        """,
                        job_id=job_id,
                        findings=[{"finding_id": f.finding_id, "subject_entity": f.subject_entity} for f in attributed],
                    )

            if chunks:
                await session.run(
                    """
                    UNWIND $chunks AS c
                    MERGE (ch:Chunk {chunk_id: c.chunk_id})
                    SET ch.job_id = $job_id, ch.source_file = c.source_file,
                        ch.text = c.text, ch.page = c.page, ch.embedding = c.embedding,
                        ch.category = c.category
                    """,
                    job_id=job_id,
                    chunks=[
                        {
                            "chunk_id": c.chunk_id, "source_file": c.source_file,
                            "text": c.text, "page": c.page, "embedding": c.embedding,
                            "category": c.category.value,
                        }
                        for c in chunks
                    ],
                )

                # Link chunks to entities they mention (cheap substring match — good
                # enough to give GraphRAG traversal edges between text and graph).
                await session.run(
                    """
                    MATCH (ch:Chunk {job_id: $job_id})
                    MATCH (e:Entity {job_id: $job_id})
                    WHERE ch.text CONTAINS e.name
                    MERGE (ch)-[:MENTIONS]->(e)
                    """,
                    job_id=job_id,
                )

    async def vector_search(
        self, job_id: str, query_vector: list[float], top_k: int = 8,
        categories: list[FileCategory] | None = None,
    ) -> list[dict]:
        """Neo4j's native vector index ranks globally across every job
        sharing it, and the job_id filter only applies *after* that ranking
        -- so a plain `queryNodes(index, top_k, vector)` can return zero
        rows for this job even when it has plenty of relevant chunks, once
        enough other jobs' chunks are competing for the same top_k slots.
        Over-fetch a much larger candidate set before filtering so this
        job's chunks have room to surface; not a hard guarantee at
        unbounded scale, but it removes the failure mode for the realistic
        range of concurrently-indexed jobs this deployment expects.

        categories: optional pre-filter from graph/query_router.py's
        keyword-based guess at which file types a chat question is
        probably about. Empty/None means no opinion -- search every
        category, same as before this filter existed. Callers should
        retry unfiltered if a category-filtered call comes back empty,
        since a wrong or overly-narrow guess must never make retrieval
        worse than not guessing at all.
        """
        settings = self._settings
        candidate_k = min(max(top_k * 25, 200), 5000)
        category_values = [c.value for c in categories] if categories else []
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                f"""
                CALL db.index.vector.queryNodes('{settings.vector_index_name}', $candidate_k, $vector)
                YIELD node, score
                WHERE node.job_id = $job_id
                  AND (size($categories) = 0 OR node.category IN $categories)
                RETURN node.chunk_id AS chunk_id, node.text AS text,
                       node.source_file AS source_file, node.page AS page, score
                ORDER BY score DESC
                LIMIT $top_k
                """,
                candidate_k=candidate_k, top_k=top_k, vector=query_vector, job_id=job_id,
                categories=category_values,
            )
            return [record.data() async for record in result]

    async def expand_entities_for_chunks(self, job_id: str, chunk_ids: list[str]) -> list[dict]:
        """1-hop graph expansion from retrieved chunks — gives GraphRAG its edge over plain vector RAG."""
        if not chunk_ids:
            return []
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                """
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE c.chunk_id IN $chunk_ids AND e.job_id = $job_id
                OPTIONAL MATCH (e)-[r:RELATION]-(other:Entity {job_id: $job_id})
                RETURN DISTINCT e.name AS entity, e.type AS type,
                       collect(DISTINCT {type: r.type, other: other.name}) AS related
                LIMIT 30
                """,
                chunk_ids=chunk_ids, job_id=job_id,
            )
            return [record.data() async for record in result]

    async def find_entities_mentioned_in_text(self, job_id: str, text: str) -> list[str]:
        """Reverse of the chunk->entity MENTIONS linking done in
        ingest_job_graph (there: `WHERE ch.text CONTAINS e.name`) -- here
        applied to an arbitrary piece of text (a chat question) instead of
        a chunk, so a question like "what is John Smith's mother's name"
        resolves directly to the John Smith Entity node. See
        expand_relations_for_entities for why this matters: chunk vector
        search can easily miss the one short passage that actually
        mentions a specific person.

        Case-insensitive on purpose (toLower on both sides) -- a chat
        question is casually typed ("what's john smith's mother's name")
        far more often than a source document's own text is, so a
        case-sensitive CONTAINS silently missed real matches whenever the
        typed case didn't happen to match how NER extracted the name."""
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                """
                MATCH (e:Entity {job_id: $job_id})
                WHERE toLower($text) CONTAINS toLower(e.name)
                RETURN DISTINCT e.name AS name
                """,
                job_id=job_id, text=text,
            )
            return [record["name"] async for record in result]

    async def find_dominant_person_entity(self, job_id: str) -> str | None:
        """When a chat question doesn't name anyone explicitly ("what is
        the mother's name?" instead of "what is John Smith's mother's
        name?"), and the job has exactly one PERSON entity, that's almost
        certainly who the question implicitly means -- the single-subject
        document case (a passport, an ID card, a single-employee form).
        Mirrors domain_managers._apply_single_subject_fallback's identical
        reasoning for PII attribution. Returns None (no opinion) for zero
        or multiple PERSON entities -- guessing among several people is
        worse than not guessing at all."""
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                """
                MATCH (e:Entity {job_id: $job_id, type: 'PERSON'})
                RETURN DISTINCT e.name AS name
                LIMIT 2
                """,
                job_id=job_id,
            )
            names = [record["name"] async for record in result]
            return names[0] if len(names) == 1 else None

    async def expand_relations_for_entities(self, job_id: str, entity_names: list[str]) -> list[dict]:
        """Direct 1-hop graph expansion seeded by entity name instead of by
        retrieved chunk (compare expand_entities_for_chunks). Chat's chunk
        vector search ranks whole text passages by semantic similarity to
        the question -- for a short, information-dense passage (a
        passport's few lines of text) competing against everything else in
        the job, the one relevant chunk can easily miss the top-k cut even
        though it's an easy substring match against the question itself
        once you know the person's name. This bypasses that entirely: given
        a known entity name, just ask the graph for its relations directly.
        Same return shape as expand_entities_for_chunks so callers can
        merge the two lists."""
        if not entity_names:
            return []
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                """
                MATCH (e:Entity {job_id: $job_id})
                WHERE e.name IN $entity_names
                OPTIONAL MATCH (e)-[r:RELATION]-(other:Entity {job_id: $job_id})
                RETURN DISTINCT e.name AS entity, e.type AS type,
                       collect(DISTINCT {type: r.type, other: other.name}) AS related
                LIMIT 30
                """,
                job_id=job_id, entity_names=entity_names,
            )
            return [record.data() async for record in result]

    async def get_full_graph(self, job_id: str) -> tuple[list[dict], list[dict]]:
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            nodes_res = await session.run(
                "MATCH (e:Entity {job_id: $job_id}) RETURN e.name AS name, e.type AS type, "
                "e.confidence AS confidence, e.source_files AS source_files",
                job_id=job_id,
            )
            nodes = [r.data() async for r in nodes_res]

            edges_res = await session.run(
                "MATCH (s:Entity {job_id: $job_id})-[r:RELATION]->(t:Entity {job_id: $job_id}) "
                "RETURN s.name AS source, t.name AS target, r.type AS type, "
                "r.confidence AS confidence, r.evidence AS evidence",
                job_id=job_id,
            )
            edges = [r.data() async for r in edges_res]
        return nodes, edges

    async def delete_job(self, job_id: str) -> None:
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            await session.run("MATCH (n) WHERE n.job_id = $job_id DETACH DELETE n", job_id=job_id)


_store_singleton: Neo4jStore | None = None


def get_store() -> Neo4jStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = Neo4jStore()
    return _store_singleton
