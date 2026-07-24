"""L1 Domain Manager — per-file pipeline: parse -> chunk+embed -> functional \
extraction (NER/PII/Financial/Relation) -> DomainResult.

The diagram splits this into five separate L1 managers (Unstructured Doc,
Structured Data, Database, PII/Compliance, Business Intel) that subscribe
to each other's output. In this implementation every file flows through
the same domain pipeline function — file-type routing already happened at
L2 (the parser), and PII/BI extraction subscribes to the resulting text
stream exactly as the diagram specifies (lateral "raw text stream" /
"schema + tabular data" comms), just expressed as sequential awaits
instead of separate message-passing actors.
"""
import logging

from app.agents.chunking import chunk_and_embed
from app.agents.data_quality import assess_quality
from app.agents.extraction import run_financial, run_ner, run_pii, run_relations, run_summary
from app.agents.translation import translate_document
from app.llm.client import get_llm_client
from app.models.schemas import DomainResult, FileCategory
from app.parsers.router import parse_file

logger = logging.getLogger(__name__)

# Functional agents that only make sense on text-bearing categories.
_TEXT_CATEGORIES = {
    FileCategory.PDF, FileCategory.IMAGE, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.CODE, FileCategory.WEB, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_, FileCategory.DATABASE,
}


async def process_file(file_path: str, unreachable_backends: set[str] | None = None) -> DomainResult:
    """unreachable_backends: backends already confirmed down by the job's
    preflight check (see job_manager._run). Extraction is skipped outright
    rather than attempted-and-retried when its backend is already known
    unreachable — this is what keeps a dead LLM endpoint from silently
    stalling a file for several minutes with no visible cause.
    """
    unreachable_backends = unreachable_backends or set()
    doc = await parse_file(file_path)
    doc = await translate_document(doc, unreachable_backends)
    result = DomainResult(
        domain=doc.category.value, source_file=file_path, tables=doc.tables,
        detected_language=doc.detected_language, translated=doc.translated,
    )

    if doc.warnings:
        result.errors.extend(doc.warnings)

    text = doc.full_text()
    if not text.strip() or doc.category not in _TEXT_CATEGORIES:
        result.chunks = await chunk_and_embed(doc)
        result.quality = assess_quality(doc, result)
        return result

    try:
        result.chunks = await chunk_and_embed(doc)
    except Exception:
        logger.exception("Chunk+embed failed for %s", file_path)
        result.errors.append("Chunk+embed step failed")

    extraction_backend = get_llm_client().backend_for_role("extraction")
    if extraction_backend in unreachable_backends:
        msg = (f"NER/PII/Financial/Relation extraction skipped: "
               f"'{extraction_backend}' model endpoint is unreachable")
        result.errors.append(msg)
        result.quality = assess_quality(doc, result)
        return result

    try:
        result.entities = await run_ner(text, file_path)
    except Exception:
        logger.exception("NER failed for %s", file_path)
        result.errors.append("NER step failed")

    try:
        result.pii_findings = await run_pii(text, file_path)
    except Exception:
        logger.exception("PII detection failed for %s", file_path)
        result.errors.append("PII step failed")

    try:
        result.financial_facts = await run_financial(text, file_path)
    except Exception:
        logger.exception("Financial extraction failed for %s", file_path)
        result.errors.append("Financial step failed")

    try:
        result.relations = await run_relations(text, result.entities, file_path)
    except Exception:
        logger.exception("Relation extraction failed for %s", file_path)
        result.errors.append("Relation step failed")

    try:
        result.summary = await run_summary(text)
    except Exception:
        logger.exception("Summary failed for %s", file_path)

    result.quality = assess_quality(doc, result)
    return result
