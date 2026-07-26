"""Metadata Agent (L1, pre-processing) -- builds a lightweight metadata
layer for every uploaded file before any parsing/extraction happens, and
before any importance decision gets made about it.

This is deliberately split from importance.py's scoring/ranking: this
module's only job is to observe and record facts about a file (type,
size, modified time, which triage keywords its filename carries) --
never to judge or score them. That separation means the signals available
for triage can grow later (e.g. a quick page-count peek for PDFs) without
touching the scoring logic in importance.py, and the scoring weights can
be retuned without touching how metadata is gathered.

Fully rule-based, no LLM call and no file content read: this runs once,
up front, for potentially thousands of files, so it has to stay cheap --
stat() + a filename regex, nothing that opens or parses the file itself.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.models.schemas import FileCategory

# Filename keywords suggesting higher-value business content -- matched
# case-insensitively against the filename only, since that's all that's
# known before parsing. Deliberately biased toward the categories this
# system already targets (financial/PII/compliance intelligence). Owned
# here (not importance.py) because *detecting* these hits is a metadata
# fact about the file, independent of how much they should count for.
_KEYWORDS = (
    "contract", "agreement", "invoice", "financial", "finance", "statement",
    "confidential", "legal", "audit", "compliance", "payroll", "tax",
    "merger", "acquisition", "nda", "report", "budget", "salary", "ledger",
    "balance", "transaction", "settlement", "board", "executive",
)
_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in _KEYWORDS), re.IGNORECASE)


@dataclass
class FileMetadata:
    """Objective, pre-parse facts about one uploaded file -- the metadata
    layer that importance.py's decision agent scores against. Nothing in
    here is a judgment call; it's what could be observed about the file
    without opening it."""
    path: str
    category: FileCategory
    size_bytes: int
    modified_at: datetime | None
    keyword_hits: list[str] = field(default_factory=list)


def _file_metadata(path: str, category: FileCategory) -> FileMetadata:
    try:
        st = Path(path).stat()
        size_bytes = st.st_size
        modified_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    except OSError:
        size_bytes = 0
        modified_at = None

    keyword_hits = sorted({m.group(0).lower() for m in _KEYWORD_RE.finditer(Path(path).name)})

    return FileMetadata(
        path=path, category=category, size_bytes=size_bytes,
        modified_at=modified_at, keyword_hits=keyword_hits,
    )


def build_metadata_layer(file_paths: list[str], categories: dict[str, FileCategory]) -> list[FileMetadata]:
    """One FileMetadata record per path, in the same order as file_paths.
    Missing/unreadable files (OSError on stat) still get a record -- with
    size_bytes=0 and modified_at=None -- rather than being silently
    dropped from the layer, so a later ranking pass always has exactly
    one entry per input file to work with."""
    return [_file_metadata(path, categories.get(path, FileCategory.UNKNOWN)) for path in file_paths]
