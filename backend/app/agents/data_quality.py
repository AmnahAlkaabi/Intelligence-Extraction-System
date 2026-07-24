"""Validator Agent (L2) — deterministic per-file quality assessment.

Computed entirely from signals already produced by the parser and
extraction steps (warnings, OCR confidence where available, extraction
yield) -- no LLM call, so it stays available even when both model
backends are unreachable.
"""
from app.models.schemas import DataQuality, DomainResult, FileCategory, ParsedDocument

# Categories where an empty extraction result (no entities/relations/PII/
# financial facts) on non-empty text is a meaningful quality signal rather
# than expected behaviour (e.g. MEDIA is metadata-only by design, so it's
# excluded).
_EXTRACTION_EXPECTED = {
    FileCategory.PDF, FileCategory.IMAGE, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.CODE, FileCategory.WEB, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_, FileCategory.DATABASE,
}


def assess_quality(doc: ParsedDocument, result: DomainResult) -> DataQuality:
    issues = list(result.errors)
    score = 100.0

    # Parser/extraction step failures already surfaced as errors.
    score -= min(len(issues) * 12, 60)

    # OCR confidence, when the parser reported it per-block.
    ocr_scores = [
        b.confidence for b in doc.text_blocks
        if b.kind == "ocr" and b.confidence is not None
    ]
    if ocr_scores:
        avg_conf = sum(ocr_scores) / len(ocr_scores)
        if avg_conf < 0.70:
            score -= 20
            issues.append(f"Low OCR confidence ({avg_conf:.0%} average)")
        elif avg_conf < 0.85:
            score -= 8

    text_present = bool(doc.full_text().strip())
    already_flagged_unreachable = any("unreachable" in i for i in issues)
    yielded_something = any([
        result.entities, result.relations, result.pii_findings,
        result.financial_facts, result.tables,
    ])

    if not text_present and not doc.tables:
        score -= 30
        issues.append("No extractable text or tabular content found")
    elif (
        text_present
        and doc.category in _EXTRACTION_EXPECTED
        and not yielded_something
        and not already_flagged_unreachable
    ):
        score -= 15
        issues.append("No entities, relations, PII, or financial facts extracted despite text content")

    score = max(0.0, min(100.0, score))

    if score >= 90:
        completeness = "Complete"
    elif score >= 70:
        completeness = "Minor gaps"
    elif score >= 40:
        completeness = "Partial"
    else:
        completeness = "Severely degraded"

    return DataQuality(
        source_file=doc.source_file,
        score=round(score, 1),
        completeness=completeness,
        issues=issues,
    )
