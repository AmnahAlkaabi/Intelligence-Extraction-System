"""Neo4j client: schema setup, entity/relation/chunk ingest, vector index.

Neo4j doubles as both the knowledge graph store AND the vector index for
RAG chunks (via its native vector index, Neo4j 5.11+), so no separate
vector DB is required in the air-gapped stack.
"""
import asyncio
import logging

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import get_settings
from app.models.schemas import Chunk, Entity, Job, Relation

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

            if chunks:
                await session.run(
                    """
                    UNWIND $chunks AS c
                    MERGE (ch:Chunk {chunk_id: c.chunk_id})
                    SET ch.job_id = $job_id, ch.source_file = c.source_file,
                        ch.text = c.text, ch.page = c.page, ch.embedding = c.embedding
                    """,
                    job_id=job_id,
                    chunks=[
                        {
                            "chunk_id": c.chunk_id, "source_file": c.source_file,
                            "text": c.text, "page": c.page, "embedding": c.embedding,
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

    async def vector_search(self, job_id: str, query_vector: list[float], top_k: int = 8) -> list[dict]:
        settings = self._settings
        async with self._driver.session(database=settings.neo4j_database) as session:
            result = await session.run(
                f"""
                CALL db.index.vector.queryNodes('{settings.vector_index_name}', $top_k, $vector)
                YIELD node, score
                WHERE node.job_id = $job_id
                RETURN node.chunk_id AS chunk_id, node.text AS text,
                       node.source_file AS source_file, node.page AS page, score
                ORDER BY score DESC
                """,
                top_k=top_k, vector=query_vector, job_id=job_id,
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
