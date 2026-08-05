"""Per-job SQLite store for structured file data (CSV/Excel/Database
uploads) -- separate from the capped, all-strings TableBlock preview used
by the Data Dump tab and its CSV export. That preview is fine for display
but is a dead end for chat: only its first 20 rows ever reach the vector
index (see chunking.py), and even those have no real types. This store
keeps every row (up to what the parser itself already read) with cell
values coerced back to INTEGER/REAL where the whole column parses
cleanly, so graph/structured_query.py can run real SQL against it instead
of doing similarity search over a stringified text preview.

One SQLite file per job (job_output_dir(job_id)/structured.db), one table
per source table (a CSV file, an Excel sheet, or a database table), plus
a "_manifest" table recording which source file/sheet each table name
came from. Deleted for free when a job is deleted -- it lives under the
same job_output_dir tree file_store.delete_job_files already wipes
wholesale.
"""
import json
import logging
import re
import sqlite3
from pathlib import Path

from app.models.schemas import FileCategory, TableBlock
from app.storage.file_store import job_output_dir

logger = logging.getLogger(__name__)

_MANIFEST_DDL = '''
CREATE TABLE IF NOT EXISTS "_manifest" (
    table_name TEXT PRIMARY KEY,
    source_file TEXT,
    category TEXT,
    sheet TEXT,
    row_count INTEGER,
    columns_json TEXT
)
'''


def structured_db_path(job_id: str) -> Path:
    return job_output_dir(job_id) / "structured.db"


def _sanitize_ident(name: str, fallback: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", (name or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"_{s}"
    return s[:60]


def _dedupe(names: list[str]) -> list[str]:
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


def _is_null_sentinel(value: str | None) -> bool:
    return value is None or str(value).strip().lower() in _NULL_SENTINELS


def _clean_numeric(value: str) -> str:
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
        int(_clean_numeric(v))
        return True
    except (ValueError, TypeError):
        return False


def _is_float(v: str) -> bool:
    try:
        float(_clean_numeric(v))
        return True
    except (ValueError, TypeError):
        return False


def _infer_column_type(values: list[str]) -> str:
    candidates = [v for v in values if not _is_null_sentinel(v)]
    if not candidates:
        return "TEXT"
    if sum(_is_int(v) for v in candidates) / len(candidates) >= _NUMERIC_TYPE_THRESHOLD:
        return "INTEGER"
    if sum(_is_float(v) for v in candidates) / len(candidates) >= _NUMERIC_TYPE_THRESHOLD:
        return "REAL"
    return "TEXT"


def _coerce_cell(value: str | None, col_type: str):
    if _is_null_sentinel(value):
        return None
    if col_type == "INTEGER":
        try:
            return int(_clean_numeric(value))
        except ValueError:
            return value
    if col_type == "REAL":
        try:
            return float(_clean_numeric(value))
        except ValueError:
            return value
    return value


def write_tables(job_id: str, source_file: str, tables: list[TableBlock], category: FileCategory) -> list[str]:
    """Writes every table this file produced into the job's structured
    SQLite store. Best-effort: callers should treat a failure here as
    non-fatal (chat's structured-query branch just won't see this file's
    data, same as any other single-agent-step failure in this pipeline).

    Synchronous/blocking (plain sqlite3) -- callers must run this via
    asyncio.to_thread, matching how the CSV/Excel/Database parsers
    themselves already offload their own pandas/sqlite3 work.
    """
    usable = [t for t in tables if t.headers and t.rows]
    if not usable:
        return []

    conn = sqlite3.connect(str(structured_db_path(job_id)))
    created: list[str] = []
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(_MANIFEST_DDL)
        existing = {r[0] for r in conn.execute('SELECT table_name FROM "_manifest"').fetchall()}

        base = Path(source_file).stem.lower()
        for table in usable:
            name_seed = f"{base}_{table.sheet}" if table.sheet else base
            base_name = _sanitize_ident(name_seed, "table")
            table_name = base_name
            n = 2
            while table_name in existing:
                table_name = f"{base_name[:55]}_{n}"
                n += 1
            existing.add(table_name)

            columns = _dedupe([_sanitize_ident(h, f"col_{i}") for i, h in enumerate(table.headers)])
            col_types = [
                _infer_column_type([row[i] if i < len(row) else "" for row in table.rows])
                for i in range(len(columns))
            ]
            col_defs = ", ".join(f'"{c}" {t}' for c, t in zip(columns, col_types))
            conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

            placeholders = ", ".join("?" for _ in columns)
            coerced_rows = [
                [_coerce_cell(row[i] if i < len(row) else "", col_types[i]) for i in range(len(columns))]
                for row in table.rows
            ]
            conn.executemany(f'INSERT INTO "{table_name}" VALUES ({placeholders})', coerced_rows)

            conn.execute(
                'INSERT INTO "_manifest" (table_name, source_file, category, sheet, row_count, columns_json) '
                "VALUES (?, ?, ?, ?, ?, ?)",
                (table_name, source_file, category.value, table.sheet, len(table.rows),
                 json.dumps(list(zip(columns, col_types)))),
            )
            created.append(table_name)
        conn.commit()
    finally:
        conn.close()
    return created


def read_manifest(job_id: str) -> list[dict]:
    """[] for a job with no structured files, or one whose store hasn't
    been created yet -- callers treat that as "no opinion," same
    "missing means try the other path" convention query_router.py uses."""
    path = structured_db_path(job_id)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        rows = conn.execute(
            'SELECT table_name, source_file, category, sheet, row_count, columns_json FROM "_manifest"'
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        {
            "table_name": r[0], "source_file": r[1], "category": r[2], "sheet": r[3],
            "row_count": r[4], "columns": json.loads(r[5]),
        }
        for r in rows
    ]


def open_readonly(job_id: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{structured_db_path(job_id)}?mode=ro", uri=True)
