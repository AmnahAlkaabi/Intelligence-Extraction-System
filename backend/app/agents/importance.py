"""Importance Decision Agent (L1, pre-processing) -- turns the Metadata
Agent's per-file layer (see metadata_agent.py) into a priority order, so a
large multi-file dump gets worked highest-value-first in checkpointed
batches instead of arbitrary upload order.

Deliberately kept separate from metadata_agent.py: that module only
observes and records facts about a file; this module is the only place
that judges what those facts are worth. Retuning the scoring weights
below never has to touch how metadata is gathered, and the metadata
layer can grow richer later without this module's scoring logic caring
where a given fact came from.

Fully rule-based, no LLM call: scoring thousands of already-built
metadata records has to stay fast, consistent with the rest of the
pipeline's deterministic agents (data_quality.py, mapping_agent.py).
"""
import math
from dataclasses import dataclass, field

from app.agents.metadata_agent import FileMetadata
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

_MAX_KEYWORD_HITS_COUNTED = 3
_KEYWORD_HIT_WEIGHT = 1.5
_MAX_SIZE_BONUS = 0.8  # log-scaled size signal caps out here regardless of file size


@dataclass
class RankedFile:
    path: str
    category: FileCategory
    score: float
    reason: str = field(compare=False)


def _score(meta: FileMetadata) -> RankedFile:
    base = _CATEGORY_WEIGHT.get(meta.category, 0.5)
    keyword_bonus = min(len(meta.keyword_hits), _MAX_KEYWORD_HITS_COUNTED) * _KEYWORD_HIT_WEIGHT

    # log2(KB) so a 500MB file doesn't dominate an otherwise-equal PDF that
    # happens to be 2MB -- this is a tiebreaker within a category/keyword
    # tier, not the primary signal.
    size_bonus = min(math.log2(max(meta.size_bytes, 1024) / 1024), 8) * 0.1
    size_bonus = min(size_bonus, _MAX_SIZE_BONUS)

    score = base + keyword_bonus + size_bonus

    reason_parts = [meta.category.value.upper()]
    if meta.keyword_hits:
        reason_parts.append(f"filename suggests: {', '.join(meta.keyword_hits)}")
    return RankedFile(path=meta.path, category=meta.category, score=score, reason=" · ".join(reason_parts))


def rank_files(metadata: list[FileMetadata]) -> list[RankedFile]:
    """Deterministic ranking over an already-built metadata layer (see
    metadata_agent.build_metadata_layer). Ties keep their original (upload)
    order -- Python's sort is stable, so this never reshuffles equally-
    scored files unpredictably between runs."""
    ranked = [_score(m) for m in metadata]
    ranked.sort(key=lambda r: -r.score)
    return ranked


def make_batches(ranked: list[RankedFile], batch_size: int) -> list[list[RankedFile]]:
    if batch_size < 1:
        batch_size = 1
    return [ranked[i : i + batch_size] for i in range(0, len(ranked), batch_size)]
