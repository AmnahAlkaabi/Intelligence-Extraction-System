"""Importance Ranking Agent (L1, pre-processing) -- deterministic triage so
a large multi-file dump gets worked highest-value-first, in checkpointed
batches, instead of in arbitrary upload order with no visibility until the
very end.

Fully rule-based, no LLM call: this runs once, up front, before any
per-file parsing/extraction starts, so ranking thousands of files has to
stay fast and can't wait on a model round-trip per file. The signals used
are all available before a single byte of file content is read -- file
type, filename, and size -- which keeps this consistent with the rest of
the pipeline's deterministic agents (data_quality.py, mapping_agent.py).
"""
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.models.schemas import FileCategory

# How likely this format is to carry dense, structured intelligence value
# on a first pass, before any content has been read. Deliberately a plain
# dict rather than trying to be exhaustive/precise -- this is a triage
# heuristic, not a scoring model.
_CATEGORY_WEIGHT: dict[FileCategory, float] = {
    FileCategory.PDF: 3.0,
    FileCategory.OFFICE: 3.0,
    FileCategory.EMAIL: 2.8,
    FileCategory.DATABASE: 2.6,
    FileCategory.CSV: 2.2,
    FileCategory.EXCEL: 2.2,
    FileCategory.JSON_: 2.0,
    FileCategory.WEB: 1.6,
    FileCategory.IMAGE: 1.4,
    FileCategory.CODE: 1.0,
    FileCategory.ARCHIVE: 0.8,
    FileCategory.MEDIA: 0.6,
    FileCategory.UNKNOWN: 0.5,
}

# Filename keywords suggesting higher-value business content -- matched
# case-insensitively against the filename only, since that's all that's
# known before parsing. Deliberately biased toward the categories this
# system already targets (financial/PII/compliance intelligence).
_KEYWORDS = (
    "contract", "agreement", "invoice", "financial", "finance", "statement",
    "confidential", "legal", "audit", "compliance", "payroll", "tax",
    "merger", "acquisition", "nda", "report", "budget", "salary", "ledger",
    "balance", "transaction", "settlement", "board", "executive",
)
_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in _KEYWORDS), re.IGNORECASE)
_MAX_KEYWORD_HITS_COUNTED = 3
_KEYWORD_HIT_WEIGHT = 1.5
_MAX_SIZE_BONUS = 0.8  # log-scaled size signal caps out here regardless of file size


@dataclass
class RankedFile:
    path: str
    category: FileCategory
    score: float
    reason: str = field(compare=False)


def _score_file(path: str, category: FileCategory, size_bytes: int) -> RankedFile:
    name = Path(path).name
    base = _CATEGORY_WEIGHT.get(category, 0.5)

    keyword_hits = sorted({m.group(0).lower() for m in _KEYWORD_RE.finditer(name)})
    keyword_bonus = min(len(keyword_hits), _MAX_KEYWORD_HITS_COUNTED) * _KEYWORD_HIT_WEIGHT

    # log2(KB) so a 500MB file doesn't dominate an otherwise-equal PDF that
    # happens to be 2MB -- this is a tiebreaker within a category/keyword
    # tier, not the primary signal.
    size_bonus = min(math.log2(max(size_bytes, 1024) / 1024), 8) * 0.1
    size_bonus = min(size_bonus, _MAX_SIZE_BONUS)

    score = base + keyword_bonus + size_bonus

    reason_parts = [category.value.upper()]
    if keyword_hits:
        reason_parts.append(f"filename suggests: {', '.join(keyword_hits)}")
    return RankedFile(path=path, category=category, score=score, reason=" · ".join(reason_parts))


def rank_files(file_paths: list[str], categories: dict[str, FileCategory]) -> list[RankedFile]:
    """Deterministic, content-blind triage ranking. Ties keep their
    original (upload) order -- Python's sort is stable, so this never
    reshuffles equally-scored files unpredictably between runs."""
    ranked = []
    for path in file_paths:
        try:
            size_bytes = Path(path).stat().st_size
        except OSError:
            size_bytes = 0
        ranked.append(_score_file(path, categories.get(path, FileCategory.UNKNOWN), size_bytes))
    ranked.sort(key=lambda r: -r.score)
    return ranked


def make_batches(ranked: list[RankedFile], batch_size: int) -> list[list[RankedFile]]:
    if batch_size < 1:
        batch_size = 1
    return [ranked[i : i + batch_size] for i in range(0, len(ranked), batch_size)]
