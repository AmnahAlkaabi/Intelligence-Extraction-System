"""Shared SQLite table-building helpers for TableBlock data.

Used by both structured_store.py (per-job, scratch) and dataset_library.py
(persistent, cross-job) so column sanitization, type inference, and cell
coercion behave identically in both -- moved here rather than duplicated
when dataset_library.py was added, since a divergence between the two
(e.g. one treating "N/A" as NULL and the other not) would make a dataset
look different depending on which store it came from for no real reason.
"""
import re

from app.models.schemas import TableBlock


def sanitize_ident(name: str, fallback: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", (name or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"_{s}"
    return s[:60]


def dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n not in seen:
            seen[n] = 0
            out.append(n)
        else:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
    return out


# Real spreadsheets mark missing values a dozen different ways, not just
# empty string -- treating these as NULL (instead of the one stray "N/A"
# forcing an otherwise-numeric column to TEXT for good) is what actually
# makes SUM()/AVG() work on real-world exports.
_NULL_SENTINELS = {"", "n/a", "na", "null", "none", "nan", "-", "--", "n.a.", "unknown", "tbd"}

# Presentational noise that doesn't change a number's value: thousands
# separators, a currency symbol, incidental whitespace.
_NUMERIC_NOISE_RE = re.compile(r"[,$\s]")

# A column counts as numeric once this fraction of its non-null values
# parse cleanly -- not literally every value. A handful of genuinely
# malformed cells (a typo, a stray label) shouldn't force the whole
# column to TEXT and break aggregate queries on the other 95% that are
# perfectly good numbers; SQLite's type affinity tolerates the rare
# leftover string in a numeric-affinity column without error anyway.
_NUMERIC_TYPE_THRESHOLD = 0.9


def is_null_sentinel(value: str | None) -> bool:
    return value is None or str(value).strip().lower() in _NULL_SENTINELS


def clean_numeric(value: str) -> str:
    """Strips presentational formatting so "$1,234.56" / " 1,234 " parse
    as numbers, and converts accounting-style negatives -- "(250)" -- to
    "-250". Anything else (a real non-numeric string) is returned as-is
    and will simply fail int()/float() below, same as before."""
    s = value.strip()
    negative = s.startswith("(") and s.endswith(")") and len(s) > 2
    if negative:
        s = s[1:-1]
    s = _NUMERIC_NOISE_RE.sub("", s)
    return f"-{s}" if negative and s else s


def _is_int(v: str) -> bool:
    try:
        int(clean_numeric(v))
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v: str) -> bool:
    try:
        float(clean_numeric(v))
        return True
    except (ValueError, TypeError):
        return False


def infer_column_type(values: list[str]) -> str:
    candidates = [v for v in values if not is_null_sentinel(v)]
    if not candidates:
        return "TEXT"
    if sum(_is_int(v) for v in candidates) / len(candidates) >= _NUMERIC_TYPE_THRESHOLD:
        return "INTEGER"
    if sum(_is_float(v) for v in candidates) / len(candidates) >= _NUMERIC_TYPE_THRESHOLD:
        return "REAL"
    return "TEXT"


def coerce_cell(value: str | None, col_type: str):
    if is_null_sentinel(value):
        return None
    if col_type == "INTEGER":
        try:
            return int(clean_numeric(value))
        except ValueError:
            return value
    if col_type == "REAL":
        try:
            return float(clean_numeric(value))
        except ValueError:
            return value
    return value


def usable_tables(tables: list[TableBlock]) -> list[TableBlock]:
    return [t for t in tables if t.headers and t.rows]


def build_columns_and_types(table: TableBlock) -> tuple[list[str], list[str]]:
    columns = dedupe([sanitize_ident(h, f"col_{i}") for i, h in enumerate(table.headers)])
    col_types = [
        infer_column_type([row[i] if i < len(row) else "" for row in table.rows])
        for i in range(len(columns))
    ]
    return columns, col_types


def coerce_rows(table: TableBlock, columns: list[str], col_types: list[str]) -> list[list]:
    return [
        [coerce_cell(row[i] if i < len(row) else "", col_types[i]) for i in range(len(columns))]
        for row in table.rows
    ]
