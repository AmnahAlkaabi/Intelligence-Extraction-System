"""Validator Agent (L2) — deterministic per-file quality assessment.

Two layers of signal, both computed -- no LLM call, so it stays available
even when both model backends are unreachable:

1. Parser/extraction-level signals (unchanged from the original version):
   parser warnings, OCR confidence, whether extraction yielded anything.
2. Cell-level signals for tabular files (CSV/Excel/Database/JSON tables):
   every row of every table is scanned column-by-column for NULL/blank
   rates, garbage/placeholder values (Excel errors, "####", repeated-char
   noise, "N/A"/"TBD"/etc.), duplicate rows, constant columns, and type
   consistency against the same type inference mapping_agent uses for the
   Source -> Target Mapping tab. This is real measured data quality, not a
   proxy -- it's what the score, issues, and the new `tables` breakdown are
   built from.
"""
import re

from app.agents.mapping_agent import guess_column_type
from app.models.schemas import (
    ColumnQuality,
    DataQuality,
    DomainResult,
    FileCategory,
    ParsedDocument,
    TableBlock,
    TableQuality,
)

# Categories where an empty extraction result (no entities/relations/PII/
# financial facts) on non-empty text is a meaningful quality signal rather
# than expected behaviour (e.g. MEDIA is metadata-only by design, so it's
# excluded).
_EXTRACTION_EXPECTED = {
    FileCategory.PDF, FileCategory.IMAGE, FileCategory.OFFICE, FileCategory.EMAIL,
    FileCategory.CODE, FileCategory.WEB, FileCategory.CSV, FileCategory.EXCEL,
    FileCategory.JSON_, FileCategory.DATABASE,
}

# ------------------------------------------------------------ cell checks --

# Values that mean "no data" even though the cell isn't literally empty --
# the common placeholder tokens spreadsheets and exports actually contain.
_NULL_TOKENS = {
    "", "null", "none", "nil", "nan", "n/a", "na", "n.a.", "-", "--", "?", "??",
    "unknown", "undefined", "tbd", "not available", "not applicable", "#n/a",
}

# Spreadsheet formula errors and pure-symbol noise ("####", "....", "@@@")
# -- non-null but not usable as data.
_EXCEL_ERROR_RE = re.compile(r"^#(N/A|VALUE!|REF!|DIV/0!|NAME\?|NUM!|NULL!)$", re.IGNORECASE)
_SYMBOL_NOISE_RE = re.compile(r"^[^a-zA-Z0-9]+$")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
_DATE_RE = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"   # 2024-03-14
    r"|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$"                        # 3/14/2024, 14-03-2024
    r"|^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}$"                    # 14 March 2024
    r"|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}$"                  # March 14, 2024
)

# Below this share of rows, a data-quality gap is noise rather than a
# reportable issue -- mirrors the orphan-ratio threshold mapping_agent uses.
_MINOR_THRESHOLD = 0.10
_MAJOR_THRESHOLD = 0.30


def _classify_cell(value: str) -> str:
    """null | garbage | ok."""
    v = value.strip()
    if not v or v.lower() in _NULL_TOKENS:
        return "null"
    if _EXCEL_ERROR_RE.match(v) or _SYMBOL_NOISE_RE.match(v):
        return "garbage"
    if len(v) >= 4 and len(set(v.lower())) == 1:  # "0000000", "xxxxxx", "......"
        return "garbage"
    return "ok"


def _matches_type(value: str, inferred: str) -> bool:
    v = value.strip()
    if inferred == "number":
        try:
            float(v.replace(",", "").replace("$", "").replace("%", ""))
            return True
        except ValueError:
            return False
    if inferred == "email":
        return bool(_EMAIL_RE.match(v))
    if inferred == "date":
        return bool(_DATE_RE.match(v))
    if inferred == "phone":
        digits = re.sub(r"\D", "", v)
        return 7 <= len(digits) <= 15
    return True  # id / text: free-form, nothing structural to check


def _table_label(table: TableBlock) -> str:
    return table.sheet or table.caption or table.table_id


def _assess_table(table: TableBlock) -> TableQuality:
    n_rows = len(table.rows)
    columns: list[ColumnQuality] = []

    for col_idx, header in enumerate(table.headers):
        if not header or not header.strip():
            continue
        raw = [row[col_idx] if col_idx < len(row) else "" for row in table.rows]
        classified = [_classify_cell(v) for v in raw]
        null_n = classified.count("null")
        garbage_n = classified.count("garbage")
        ok_values = [v.strip() for v, c in zip(raw, classified) if c == "ok"]

        null_rate = null_n / n_rows if n_rows else 0.0
        garbage_rate = garbage_n / n_rows if n_rows else 0.0
        distinct_count = len({v.lower() for v in ok_values})
        distinct_ratio = distinct_count / len(ok_values) if ok_values else 0.0
        inferred = guess_column_type(header, ok_values[:20])
        type_hits = sum(1 for v in ok_values if _matches_type(v, inferred))
        type_consistency = type_hits / len(ok_values) if ok_values else 1.0

        issues: list[str] = []
        if null_rate >= _MAJOR_THRESHOLD:
            issues.append(f"{null_rate:.0%} NULL/blank")
        elif null_rate >= _MINOR_THRESHOLD:
            issues.append(f"{null_rate:.0%} NULL/blank (minor)")
        if garbage_rate >= 0.05:
            issues.append(f"{garbage_rate:.0%} garbage/placeholder values")
        # "Constant" is about the absolute distinct count, not a ratio -- a
        # column that's the same single value across 11 rows is just as
        # constant as one that's the same value across 10,000 rows, but a
        # distinct/total ratio only catches the latter.
        if len(ok_values) >= 5 and distinct_count == 1:
            issues.append(f"constant column (every value is '{ok_values[0]}')")
        elif len(ok_values) >= 50 and distinct_ratio <= 0.02:
            issues.append("low-cardinality column (near-constant)")
        # >= 3, not >= 10: type_consistency is an exact measurement of
        # every value actually present in the column, not an estimate
        # sampled from some larger population -- there's no statistical-
        # power reason to need a double-digit row count before trusting
        # it. The old >= 10 floor meant a small (very common for JSON --
        # test exports, API samples, small datasets) file with an
        # obviously wrong-typed value in, say, a 4-row column never
        # surfaced it at all, no matter how blatant. >= 3 still excludes
        # n=1/2, where a single value makes the ratio purely binary
        # (0%/50%/100%) and too coarse to be a meaningful signal.
        if len(ok_values) >= 3 and type_consistency < 0.85:
            issues.append(f"{1 - type_consistency:.0%} of values don't match inferred type '{inferred}'")

        columns.append(ColumnQuality(
            column=header,
            null_rate=round(null_rate, 3),
            garbage_rate=round(garbage_rate, 3),
            distinct_ratio=round(distinct_ratio, 3),
            type_consistency=round(type_consistency, 3),
            inferred_type=inferred,
            issues=issues,
        ))

    seen: set[tuple[str, ...]] = set()
    dup_rows = 0
    for row in table.rows:
        key = tuple((row[i].strip().lower() if i < len(row) else "") for i in range(len(table.headers)))
        if not any(key):
            continue
        if key in seen:
            dup_rows += 1
        else:
            seen.add(key)

    return TableQuality(
        table=_table_label(table),
        row_count=n_rows,
        duplicate_rows=dup_rows,
        duplicate_rate=round(dup_rows / n_rows, 3) if n_rows else 0.0,
        columns=columns,
    )


def _table_penalty(tables: list[TableQuality]) -> tuple[float, list[str]]:
    """Fold per-table/column findings into a score penalty (capped, so one
    messy table among many doesn't dominate the whole-file score) plus a
    short, human-readable issue list -- worst offenders first.

    Reads severity off the already-computed per-column `issues` (single
    source of truth for which checks fired, set in `_assess_table`) rather
    than re-deriving the firing conditions here, so the two can't drift."""
    findings: list[tuple[float, str]] = []  # (severity, message) for sorting

    for t in tables:
        if t.duplicate_rate >= 0.05:
            sev = 8 + min(t.duplicate_rate, 1.0) * 12
            findings.append((sev, f"{t.table}: {t.duplicate_rows} duplicate row(s) ({t.duplicate_rate:.0%})"))
        for c in t.columns:
            for issue in c.issues:
                if "NULL/blank (minor)" in issue:
                    sev = 1.5
                elif "NULL/blank" in issue:
                    sev = 4 + c.null_rate * 8
                elif "garbage/placeholder" in issue:
                    sev = 5 + c.garbage_rate * 15
                elif "constant column" in issue or "low-cardinality" in issue:
                    sev = 3.0
                elif "don't match inferred type" in issue:
                    sev = 4 + (1 - c.type_consistency) * 8
                else:
                    sev = 3.0
                findings.append((sev, f"{t.table}.{c.column}: {issue}"))

    findings.sort(key=lambda f: -f[0])
    penalty = sum(f[0] for f in findings)
    messages = [f[1] for f in findings[:8]]
    if len(findings) > 8:
        messages.append(f"...and {len(findings) - 8} more column-level issue(s) -- see the table breakdown")
    return min(penalty, 40.0), messages


# ----------------------------------------------------------- entry point --

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

    # Real cell-level scan of every table -- NULLs, garbage/placeholder
    # values, duplicate rows, constant columns, type consistency.
    tables = [_assess_table(t) for t in doc.tables if t.headers]
    if tables:
        penalty, table_issues = _table_penalty(tables)
        score -= penalty
        issues.extend(table_issues)

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
        tables=tables,
    )
