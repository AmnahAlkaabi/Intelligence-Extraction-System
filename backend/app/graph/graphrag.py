"""GraphRAG retrieval + answer generation for the interactive chat.

Retrieval strategy: vector search over Chunk nodes (BGE embeddings, Neo4j
native vector index) for the top-k semantically relevant chunks, then a
1-hop graph expansion from those chunks to their mentioned entities and
that entity's relations. Both are folded into the Kimi2 prompt so answers
can cite specific text passages *and* reason over the structured graph
(ownership chains, cross-document links) — this is what distinguishes it
from plain vector-only RAG.
"""
import logging

from app.embeddings.bge import get_embedder
from app.graph.neo4j_client import get_store
from app.llm.client import get_llm_client
from app.models.schemas import ChatMessage, ChatResponse, Citation

logger = logging.getLogger(__name__)

CHAT_SYSTEM = """You are an intelligence analysis assistant answering questions about a \
set of documents that were already processed and indexed into a knowledge graph.

You are given:
1. Retrieved text passages (with source file + page).
2. Related graph facts (entities and their relationships) surfaced from those passages.

Rules:
- Answer ONLY using the provided context. If the context doesn't contain the answer, \
say so plainly — do not guess or use outside knowledge.
- Cite sources inline like [source_file p.X] after claims.
- If graph facts and text passages conflict, note the discrepancy.
- Be concise and factual. This is an analyst tool, not a chatbot for chit-chat.
"""


async def answer_question(job_id: str, message: str, history: list[ChatMessage]) -> ChatResponse:
    embedder = get_embedder()
    store = get_store()

    # Fast preflight, same rationale as the chat-model check below: a dead
    # or unauthorized Neo4j should surface as a clear degraded-mode answer
    # in ~8s, not bubble up as an unhandled exception -> 500 from the route.
    neo4j_reachable, neo4j_detail = await store.check_reachable(timeout_s=8.0)
    if not neo4j_reachable:
        return ChatResponse(
            answer=f"The knowledge graph is currently unreachable: {neo4j_detail} "
                   f"Verify Neo4j is up and the credentials match, then try again.",
            citations=[], uncertain=True,
        )

    try:
        query_vec = await embedder.embed_query(message)
        hits = await store.vector_search(job_id, query_vec, top_k=8)
    except Exception:
        logger.exception("Vector search failed for job %s", job_id)
        return ChatResponse(
            answer="Retrieval from the knowledge graph failed unexpectedly. "
                   "Check the backend logs for details, then try again.",
            citations=[], uncertain=True,
        )

    if not hits:
        return ChatResponse(
            answer="I don't have any indexed content to answer this from yet. "
                   "Make sure the analysis job has completed before asking questions.",
            citations=[], uncertain=True,
        )

    chunk_ids = [h["chunk_id"] for h in hits]
    try:
        graph_facts = await store.expand_entities_for_chunks(job_id, chunk_ids)
    except Exception:
        logger.exception("Graph expansion failed for job %s", job_id)
        graph_facts = []

    context_parts = ["--- Retrieved passages ---"]
    for h in hits:
        loc = f"{h['source_file']}" + (f" p.{h['page']}" if h.get("page") else "")
        context_parts.append(f"[{loc}] (score={h['score']:.2f})\n{h['text']}")

    if graph_facts:
        context_parts.append("\n--- Related graph facts ---")
        for fact in graph_facts:
            related_str = "; ".join(
                f"{r['type']} -> {r['other']}" for r in fact.get("related", []) if r.get("other")
            )
            context_parts.append(f"{fact['entity']} ({fact['type']}): {related_str or 'no known relations'}")

    context = "\n\n".join(context_parts)

    history_str = ""
    if history:
        history_str = "\n".join(f"{m.role}: {m.content}" for m in history[-6:]) + "\n"

    user_prompt = f"{history_str}Context:\n{context}\n\nQuestion: {message}"

    client = get_llm_client()
    chat_backend = client.backend_for_role("chat")

    # Fast preflight before the real (multi-retry, up-to-180s-per-attempt)
    # call -- a genuinely dead chat endpoint should fail in ~8s with a clear
    # message, not leave the user waiting minutes per message with no
    # feedback while it silently retries.
    reachable, detail = await client.check_reachable(chat_backend, timeout_s=8.0)
    if not reachable:
        return ChatResponse(
            answer=f"The chat model ('{chat_backend}') is currently unreachable: {detail} "
                   f"Verify the endpoint is up and reachable from the backend, then try again.",
            citations=[], uncertain=True,
        )

    try:
        resp = await client.complete("chat", CHAT_SYSTEM, user_prompt, temperature=0.2, max_tokens=1024)
        answer_text = resp.text.strip()
    except Exception as exc:
        logger.exception("Chat completion failed for job %s", job_id)
        return ChatResponse(
            answer=f"The chat model ('{chat_backend}') failed to respond: {exc}",
            citations=[], uncertain=True,
        )

    citations = [
        Citation(source_file=h["source_file"], chunk_text=h["text"][:300], page=h.get("page"), score=h["score"])
        for h in hits[:5]
    ]
    uncertain = any(p in answer_text.lower() for p in ["don't know", "not contain", "no information", "cannot find"])

    return ChatResponse(answer=answer_text, citations=citations, uncertain=uncertain)
