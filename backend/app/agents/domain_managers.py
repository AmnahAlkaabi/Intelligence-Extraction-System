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
from app.models.schemas import DomainResult, FileCategory, Job
from app.parsers.router import classify, parse_file
from app.pipeline.agent_tracker import finish_activity, start_activity

logger = logging.getLogger(__name__)

# Functional agents that only make sense on text-bearing categories.
_TEXT_CATEGORIES = {
    FileCategory.PDF, FileCategory.IMAGE, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.CODE, FileCategory.WEB, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_, FileCategory.DATABASE,
}

# Display names for the agent status panel, keyed by the category the L1
# router dispatches to -- kept here rather than in router.py since this is
# purely a UI-facing label, not routing logic.
_SPECIALIST_NAMES = {
    FileCategory.PDF: "PDF Specialist",
    FileCategory.IMAGE: "Image/OCR Specialist",
    FileCategory.CSV: "CSV Specialist",
    FileCategory.EXCEL: "Excel Specialist",
    FileCategory.JSON_: "JSON Specialist",
    FileCategory.OFFICE: "Office Specialist",
    FileCategory.EMAIL: "Email Specialist",
    FileCategory.DATABASE: "Database Specialist",
    FileCategory.CODE: "Code & Log Specialist",
    FileCategory.ARCHIVE: "Archive Specialist",
    FileCategory.MEDIA: "Media Specialist",
    FileCategory.WEB: "Web/XML Specialist",
    FileCategory.UNKNOWN: "Format Specialist",
}


async def process_file(
    file_path: str, unreachable_backends: set[str] | None = None, job: Job | None = None
) -> DomainResult:
    """unreachable_backends: backends already confirmed down by the job's
    preflight check (see job_manager._run). Extraction is skipped outright
    rather than attempted-and-retried when its backend is already known
    unreachable — this is what keeps a dead LLM endpoint from silently
    stalling a file for several minutes with no visible cause.

    job: optional -- when passed, every agent step reports its start/finish
    onto job.agent_activity for the live agent status panel. None (e.g. in
    isolated tests) simply skips tracking.
    """
    unreachable_backends = unreachable_backends or set()

    specialist_name = _SPECIALIST_NAMES.get(classify(file_path), "Format Specialist")
    activity = start_activity(job, specialist_name, file_path)
    try:
        doc = await parse_file(file_path)
        finish_activity(activity, "completed")
    except Exception:
        finish_activity(activity, "failed")
        raise

    activity = start_activity(job, "Translator", file_path)
    doc = await translate_document(doc, unreachable_backends)
    finish_activity(activity, "completed")

    result = DomainResult(
        domain=doc.category.value, source_file=file_path, tables=doc.tables,
        detected_language=doc.detected_language, translated=doc.translated,
    )

    if doc.warnings:
        result.errors.extend(doc.warnings)

    text = doc.full_text()
    if not text.strip() or doc.category not in _TEXT_CATEGORIES:
        activity = start_activity(job, "Chunk/Embed Extractor", file_path)
        result.chunks = await chunk_and_embed(doc)
        finish_activity(activity, "completed")
        result.quality = assess_quality(doc, result)
        return result

    activity = start_activity(job, "Chunk/Embed Extractor", file_path)
    try:
        result.chunks = await chunk_and_embed(doc)
        finish_activity(activity, "completed")
    except Exception:
        logger.exception("Chunk+embed failed for %s", file_path)
        result.errors.append("Chunk+embed step failed")
        finish_activity(activity, "failed")

    extraction_backend = get_llm_client().backend_for_role("extraction")
    if extraction_backend in unreachable_backends:
        msg = (f"NER/PII/Financial/Relation extraction skipped: "
               f"'{extraction_backend}' model endpoint is unreachable")
        result.errors.append(msg)
        for name in ("Entity Extractor", "PII Extractor", "Financial Extractor", "Relation Extractor"):
            finish_activity(start_activity(job, name, file_path), "skipped")
        result.quality = assess_quality(doc, result)
        return result

    activity = start_activity(job, "Entity Extractor", file_path)
    try:
        result.entities = await run_ner(text, file_path)
        finish_activity(activity, "completed")
    except Exception:
        logger.exception("NER failed for %s", file_path)
        result.errors.append("NER step failed")
        finish_activity(activity, "failed")

    activity = start_activity(job, "PII Extractor", file_path)
    try:
        result.pii_findings = await run_pii(text, file_path)
        finish_activity(activity, "completed")
    except Exception:
        logger.exception("PII detection failed for %s", file_path)
        result.errors.append("PII step failed")
        finish_activity(activity, "failed")

    activity = start_activity(job, "Financial Extractor", file_path)
    try:
        result.financial_facts = await run_financial(text, file_path)
        finish_activity(activity, "completed")
    except Exception:
        logger.exception("Financial extraction failed for %s", file_path)
        result.errors.append("Financial step failed")
        finish_activity(activity, "failed")

    activity = start_activity(job, "Relation Extractor", file_path)
    try:
        result.relations = await run_relations(text, result.entities, file_path)
        finish_activity(activity, "completed")
    except Exception:
        logger.exception("Relation extraction failed for %s", file_path)
        result.errors.append("Relation step failed")
        finish_activity(activity, "failed")

    try:
        result.summary = await run_summary(text)
    except Exception:
        logger.exception("Summary failed for %s", file_path)

    activity = start_activity(job, "Data Quality Validator", file_path)
    result.quality = assess_quality(doc, result)
    finish_activity(activity, "completed")
    return result
