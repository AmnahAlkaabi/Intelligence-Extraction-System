"""Synthesiser Agent — merges every DomainResult into the final output bundle.

Runs on the "synthesis" role (Kimi2 by default) since it needs to reason
over the combined findings from every file and has the longest effective
context requirement in the pipeline.
"""
import json
import logging

from app.llm.client import get_llm_client
from app.models.schemas import (
    BIReport,
    ComplianceReport,
    DataDump,
    DomainResult,
    Entity,
    KnowledgeGraphExport,
    Relation,
    SynthesisOutput,
)

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM = """You are the Synthesiser Agent — the final stage of a multi-agent \
intelligence extraction pipeline. You are given per-file extraction summaries, entity \
counts, PII severity counts, and financial facts gathered across every uploaded \
document. Produce a single cross-document executive briefing.

Return ONLY a JSON object of this exact shape, nothing else:
{
  "executive_summary": "3-6 sentence narrative synthesis across all files",
  "key_entities": ["most important entity names, max 15"],
  "financial_highlights": ["short bullet strings", "..."],
  "risks": ["short bullet strings describing anomalies/red flags", "..."],
  "market_signals": ["short bullet strings, business-relevant observations", "..."],
  "gap_flags": ["short bullet strings: compliance gaps e.g. missing consent basis for PII found", "..."],
  "remediation": ["short bullet strings: concrete remediation actions", "..."]
}

Base every claim strictly on the provided findings. Do not invent facts.
"""

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _dedup_entities(entities: list[Entity]) -> list[Entity]:
    merged: dict[tuple[str, str], Entity] = {}
    for e in entities:
        key = (e.name.strip().lower(), e.type)
        if key in merged:
            merged[key].mentions = list(set(merged[key].mentions + e.mentions))
            merged[key].confidence = max(merged[key].confidence, e.confidence)
        else:
            merged[key] = e.model_copy()
    return list(merged.values())


def _dedup_relations(relations: list[Relation]) -> list[Relation]:
    seen: dict[tuple[str, str, str], Relation] = {}
    for r in relations:
        key = (r.source_entity.lower(), r.relation_type, r.target_entity.lower())
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def _build_context_digest(results: list[DomainResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"\n### File: {r.source_file} (domain: {r.domain})")
        if r.summary:
            lines.append(f"Summary: {r.summary}")
        if r.entities:
            top_entities = sorted(r.entities, key=lambda e: -e.confidence)[:10]
            lines.append("Top entities: " + ", ".join(f"{e.name} ({e.type})" for e in top_entities))
        if r.relations:
            lines.append(f"Relations found: {len(r.relations)}")
        if r.pii_findings:
            counts: dict[str, int] = {}
            for f in r.pii_findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            lines.append(f"PII findings by severity: {counts}")
        if r.financial_facts:
            facts_preview = [f"{f.label}={f.amount}{f.currency or ''}" for f in r.financial_facts[:8]]
            lines.append("Financial facts: " + "; ".join(facts_preview))
        if r.errors:
            lines.append(f"Processing warnings: {r.errors}")
    return "\n".join(lines)


async def synthesize(results: list[DomainResult]) -> SynthesisOutput:
    client = get_llm_client()

    all_entities = _dedup_entities([e for r in results for e in r.entities])
    all_relations = _dedup_relations([rel for r in results for rel in r.relations])
    all_pii = [f for r in results for f in r.pii_findings]
    all_tables_files = [r.source_file for r in results]
    chunk_count = sum(len(r.chunks) for r in results)

    digest = _build_context_digest(results)
    try:
        raw = await client.complete_json("synthesis", SYNTHESIS_SYSTEM, digest, max_tokens=2048)
    except Exception:
        logger.exception("Synthesis LLM call failed")
        raw = {}

    bi_report = BIReport(
        executive_summary=raw.get("executive_summary")
        or "Synthesis could not be generated (LLM unavailable). See per-file summaries below.",
        key_entities=raw.get("key_entities") or [e.name for e in all_entities[:15]],
        financial_highlights=raw.get("financial_highlights") or [],
        risks=raw.get("risks") or [],
        market_signals=raw.get("market_signals") or [],
    )

    severity_counts: dict[str, int] = {}
    for f in all_pii:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    compliance_report = ComplianceReport(
        pii_inventory=sorted(all_pii, key=lambda f: SEVERITY_ORDER.get(f.severity, 4)),
        severity_counts=severity_counts,
        gap_flags=raw.get("gap_flags") or [],
        remediation=raw.get("remediation") or [],
    )

    knowledge_graph = KnowledgeGraphExport(entities=all_entities, relations=all_relations)

    data_dump = DataDump(
        tables=[],  # full tables are available per-job via the outputs API, not duplicated here
        files_processed=all_tables_files,
        chunk_count=chunk_count,
    )

    return SynthesisOutput(
        bi_report=bi_report,
        compliance_report=compliance_report,
        knowledge_graph=knowledge_graph,
        data_dump=data_dump,
    )
