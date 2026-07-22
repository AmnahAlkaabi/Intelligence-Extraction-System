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
from app.agents.extraction import run_financial, run_ner, run_pii, run_relations, run_summary
from app.models.schemas import DomainResult, FileCategory
from app.parsers.router import parse_file

logger = logging.getLogger(__name__)

# Functional agents that only make sense on text-bearing categories.
_TEXT_CATEGORIES = {
    FileCategory.PDF, FileCategory.IMAGE, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.CODE, FileCategory.WEB, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_, FileCategory.DATABASE,
}


async def process_file(file_path: str) -> DomainResult:
    doc = await parse_file(file_path)
    result = DomainResult(domain=doc.category.value, source_file=file_path, tables=doc.tables)

    if doc.warnings:
        result.errors.extend(doc.warnings)

    text = doc.full_text()
    if not text.strip() or doc.category not in _TEXT_CATEGORIES:
        result.chunks = await chunk_and_embed(doc)
        return result

    try:
        result.chunks = await chunk_and_embed(doc)
    except Exception:
        logger.exception("Chunk+embed failed for %s", file_path)
        result.errors.append("Chunk+embed step failed")

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

    return result
