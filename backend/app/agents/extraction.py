"""L2 functional extraction agents: NER, PII, Financial, Relation.

Each agent receives the raw text stream from the file-type agents (mirrors
the "ChunkBroadcast" lateral comm in the architecture diagram: every
functional agent sees the same text) and returns structured findings via
the shared Qwen/Kimi2 LLM client. Long documents are processed in
map-reduce segments and results are deduplicated on merge.
"""
import asyncio
import logging

from app.agents.prompts import FINANCIAL_SYSTEM, NER_SYSTEM, PII_SYSTEM, RELATION_SYSTEM, SUMMARY_SYSTEM
from app.llm.client import get_llm_client
from app.models.schemas import Entity, FinancialFact, PIIFinding, Relation

logger = logging.getLogger(__name__)

SEGMENT_CHARS = 8000       # ~2k tokens, safe for most local model context windows
MAX_SEGMENTS = 12          # hard cap so one huge file can't stall a job indefinitely


def _segment_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    segments = [text[i : i + SEGMENT_CHARS] for i in range(0, len(text), SEGMENT_CHARS)]
    return segments[:MAX_SEGMENTS]


async def run_ner(text: str, source_file: str) -> list[Entity]:
    client = get_llm_client()
    segments = _segment_text(text)
    if not segments:
        return []

    async def _one(seg: str) -> list[Entity]:
        try:
            data = await client.complete_json("extraction", NER_SYSTEM, seg)
        except Exception:
            logger.exception("NER extraction failed for %s", source_file)
            return []
        out = []
        for item in data.get("entities", []):
            try:
                out.append(Entity(
                    name=item["name"], type=item.get("type", "OTHER"),
                    source_file=source_file, mentions=item.get("mentions", []),
                    confidence=float(item.get("confidence", 0.7)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    results = await asyncio.gather(*(_one(s) for s in segments))
    return _dedup_entities([e for sub in results for e in sub])


def _dedup_entities(entities: list[Entity]) -> list[Entity]:
    merged: dict[tuple[str, str], Entity] = {}
    for e in entities:
        key = (e.name.strip().lower(), e.type)
        if key in merged:
            merged[key].mentions = list(set(merged[key].mentions + e.mentions))
            merged[key].confidence = max(merged[key].confidence, e.confidence)
        else:
            merged[key] = e
    return list(merged.values())


async def run_pii(text: str, source_file: str, entities: list[Entity] | None = None) -> list[PIIFinding]:
    """entities: this file's already-extracted entities (NER runs before PII
    in domain_managers.py), passed the same way run_relations already
    receives them -- lets the model attribute a finding to the specific
    person it describes (PIIFinding.subject_entity) instead of every
    finding being a disconnected fact with no owner."""
    client = get_llm_client()
    segments = _segment_text(text)
    if not segments:
        return []

    person_names = sorted({e.name for e in (entities or []) if e.type == "PERSON"})

    async def _one(seg: str) -> list[PIIFinding]:
        prompt = f"Known entities: {person_names}\n\nText:\n{seg}" if person_names else seg
        try:
            data = await client.complete_json("extraction", PII_SYSTEM, prompt)
        except Exception:
            logger.exception("PII extraction failed for %s", source_file)
            return []
        out = []
        for item in data.get("findings", []):
            try:
                # Only trust subject_entity if it's actually one of the
                # names we gave the model -- a hallucinated or malformed
                # name would otherwise become a dangling reference nothing
                # in the graph resolves to (see neo4j_client.ingest_job_graph,
                # which MATCHes on exact name and silently no-ops rather
                # than erroring on a miss, but a clean None here is better
                # than a misleading not-quite-right attribution surviving
                # into the report).
                raw_subject = item.get("subject_entity")
                subject = next(
                    (n for n in person_names if n.lower() == str(raw_subject).strip().lower()), None
                ) if raw_subject else None
                out.append(PIIFinding(
                    category=item.get("category", "OTHER_PII"),
                    value_redacted=item.get("value_redacted", "***"),
                    severity=item.get("severity", "low"),
                    source_file=source_file,
                    location=item.get("location"),
                    subject_entity=subject,
                ))
            except (KeyError, TypeError):
                continue
        return out

    results = await asyncio.gather(*(_one(s) for s in segments))
    return [f for sub in results for f in sub]


async def run_financial(text: str, source_file: str) -> list[FinancialFact]:
    client = get_llm_client()
    segments = _segment_text(text)
    if not segments:
        return []

    async def _one(seg: str) -> list[FinancialFact]:
        try:
            data = await client.complete_json("extraction", FINANCIAL_SYSTEM, seg)
        except Exception:
            logger.exception("Financial extraction failed for %s", source_file)
            return []
        out = []
        for item in data.get("facts", []):
            try:
                amount = item.get("amount")
                out.append(FinancialFact(
                    label=item.get("label", "Unlabeled fact"),
                    amount=float(amount) if amount is not None else None,
                    currency=item.get("currency"), period=item.get("period"),
                    source_file=source_file, context=item.get("context"),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    results = await asyncio.gather(*(_one(s) for s in segments))
    return [f for sub in results for f in sub]


async def run_relations(text: str, entities: list[Entity], source_file: str) -> list[Relation]:
    if not entities:
        return []
    client = get_llm_client()
    entity_names = sorted({e.name for e in entities})
    segments = _segment_text(text)
    if not segments:
        return []

    async def _one(seg: str) -> list[Relation]:
        prompt = f"Known entities: {entity_names}\n\nText:\n{seg}"
        try:
            data = await client.complete_json("extraction", RELATION_SYSTEM, prompt)
        except Exception:
            logger.exception("Relation extraction failed for %s", source_file)
            return []
        out = []
        for item in data.get("relations", []):
            try:
                out.append(Relation(
                    source_entity=item["source_entity"], target_entity=item["target_entity"],
                    relation_type=item.get("relation_type", "RELATED_TO"),
                    source_file=source_file, evidence=item.get("evidence"),
                    confidence=float(item.get("confidence", 0.6)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    results = await asyncio.gather(*(_one(s) for s in segments))
    return [r for sub in results for r in sub]


async def run_summary(text: str) -> str:
    if not text.strip():
        return ""
    client = get_llm_client()
    try:
        resp = await client.complete("extraction", SUMMARY_SYSTEM, text[:SEGMENT_CHARS], max_tokens=300)
        return resp.text.strip()
    except Exception:
        logger.exception("Summary generation failed")
        return ""
