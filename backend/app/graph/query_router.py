"""Query Router -- deterministic, keyword-based category inference for
chat questions, so retrieval can narrow its candidate pool instead of
always ranking every chunk in the job regardless of format. Mirrors the
same triage philosophy metadata_agent.py applies to filenames, just
applied to the chat question instead.

Two signals, both cheap regex/substring matches -- no LLM call, so this
adds no latency to a chat turn:
  1. Explicit format mentions ("the emails", "that spreadsheet") map
     straight to the matching FileCategory.
  2. Business-content keywords (the same BUSINESS_KEYWORDS vocabulary
     metadata_agent.py uses for filename triage -- "invoice", "payroll",
     "contract", etc) map to the categories most likely to carry that
     kind of content, using the same "high value" tier importance.py
     already scores highest.

This is a SOFT signal only: infer_categories() returns None when nothing
matches, meaning "no opinion -- search every category," not "search
nothing." Callers (graphrag.answer_question) must fall back to an
unfiltered search whenever a filtered search comes back empty, so a
wrong or absent guess can never make an answer worse than not routing at
all -- only faster and more precise when the guess is right.
"""
import re

from app.agents.metadata_agent import BUSINESS_KEYWORDS
from app.models.schemas import FileCategory

_BUSINESS_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in BUSINESS_KEYWORDS), re.IGNORECASE)

# Same "carries dense business content" tier importance.py weights
# highest (_CATEGORY_WEIGHT >= 2.0) -- reused here rather than
# duplicating a second opinion about which formats matter.
_BUSINESS_CATEGORIES = (
    FileCategory.PDF, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.DATABASE, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_,
)

# Explicit mentions of a format, independent of BUSINESS_KEYWORDS -- lets
# "what's in the emails" or "check the spreadsheet" route to exactly that
# one category even when no business keyword is present.
_FORMAT_PHRASES: dict[FileCategory, tuple[str, ...]] = {
    FileCategory.EMAIL: ("email", "e-mail", "inbox", "correspondence"),
    FileCategory.EXCEL: ("excel", "workbook", "worksheet", "spreadsheet"),
    FileCategory.CSV: ("csv file", "csv data"),
    FileCategory.JSON_: ("json file", "json data", ".json"),
    FileCategory.DATABASE: ("database", "sql table", "db table"),
    FileCategory.CODE: ("log file", "source code", "script file"),
    FileCategory.IMAGE: ("scanned image", "screenshot", "photo"),
    FileCategory.WEB: ("webpage", "website", "html page"),
    FileCategory.OFFICE: ("presentation", "slide deck", "word document"),
}


def infer_categories(question: str) -> list[FileCategory] | None:
    """Best-effort category guess for a chat question. Returns None when
    nothing matches -- treat that as "no opinion," not as an exclusionary
    empty result."""
    q = question.lower()
    matched: set[FileCategory] = set()

    for category, phrases in _FORMAT_PHRASES.items():
        if any(p in q for p in phrases):
            matched.add(category)

    if _BUSINESS_KEYWORD_RE.search(q):
        matched.update(_BUSINESS_CATEGORIES)

    return sorted(matched, key=lambda c: c.value) if matched else None
