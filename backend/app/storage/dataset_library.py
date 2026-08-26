"""Persistent, cross-job dataset library.

structured_store.py's SQLite store lives under job_output_dir(job_id) and
is deleted the moment its job is deleted (file_store.delete_job_files wipes
that whole directory tree) -- it exists purely as scratch input for chat's
text-to-SQL branch during that one job's lifetime. This module is the
opposite: a single SQLite file OUTSIDE any job's output directory, at
settings.dataset_library_db_path, that every job's extracted structured
tables (CSV/Excel/JSON/Database) get mirrored into and that nothing ever cleans
up automatically -- a dataset saved here survives its source job being
deleted, a backend restart, or the job's own structured.db scratch file
being wiped.

Schema: one "_datasets" registry table (id, job_id, source_file, table
name, category, row/column metadata, saved_at) plus one real data table
per saved dataset, named uniquely across the WHOLE library (not just
within one job, unlike structured_store's per-job uniqueness) since many
jobs' tables now share one file. Column sanitization/type-inference/cell
coercion is shared with structured_store.py via storage/tabular.py so a
dataset looks identical whichever store you inspect it from.
"""
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.models.schemas import DatasetRecord, FileCategory, TableBlock
from app.storage import tabular

logger = logging.getLogger(__name__)

_DATASETS_DDL = '''
CREATE TABLE IF NOT EXISTS "_datasets" (
    dataset_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    sheet TEXT,
    category TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    columns_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
)
'''


def library_db_path() -> Path:
    path = Path(get_settings().dataset_library_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(library_db_path()))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(_DATASETS_DDL)
    return conn


def save_tables(job_id: str, source_file: str, tables: list[TableBlock], category: FileCategory) -> list[str]:
    """Mirrors every table this file produced into the persistent library,
    each as its own real, queryable SQLite table plus a "_datasets"
    registry row. Best-effort, same convention as structured_store.write_tables
    -- a failure here shouldn't fail the file's extraction, it just means
    that file's tables won't show up in the saved-datasets library.

    Synchronous/blocking (plain sqlite3) -- callers must run this via
    asyncio.to_thread AND hold a single process-wide lock around the call
    (unlike structured_store's per-job lock), since this one file is
    shared by every concurrently-running job in the process, not just
    files within the same job.
    """
    usable = tabular.usable_tables(tables)
    if not usable:
        return []

    conn = _connect()
    saved_ids: list[str] = []
    try:
        existing_names = {r[0] for r in conn.execute('SELECT table_name FROM "_datasets"').fetchall()}
        base = Path(source_file).stem.lower()
        saved_at = datetime.now(timezone.utc).isoformat()

        for table in usable:
            name_seed = f"{base}_{table.sheet}" if table.sheet else base
            base_name = tabular.sanitize_ident(name_seed, "dataset")
            table_name = base_name
            n = 2
            while table_name in existing_names:
                table_name = f"{base_name[:55]}_{n}"
                n += 1
            existing_names.add(table_name)

            columns, col_types = tabular.build_columns_and_types(table)
            col_defs = ", ".join(f'"{c}" {t}' for c, t in zip(columns, col_types))
            conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

            placeholders = ", ".join("?" for _ in columns)
            coerced_rows = tabular.coerce_rows(table, columns, col_types)
            conn.executemany(f'INSERT INTO "{table_name}" VALUES ({placeholders})', coerced_rows)

            dataset_id = uuid.uuid4().hex
            conn.execute(
                'INSERT INTO "_datasets" '
                "(dataset_id, job_id, source_file, table_name, sheet, category, row_count, columns_json, saved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, job_id, source_file, table_name, table.sheet, category.value,
                 len(table.rows), json.dumps(list(zip(columns, col_types))), saved_at),
            )
            saved_ids.append(dataset_id)
        conn.commit()
    finally:
        conn.close()
    return saved_ids


def list_datasets() -> list[DatasetRecord]:
    if not library_db_path().exists():
        return []
    conn = sqlite3.connect(f"file:{library_db_path()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        rows = conn.execute(
            'SELECT dataset_id, job_id, source_file, table_name, sheet, category, row_count, columns_json, saved_at '
            'FROM "_datasets" ORDER BY saved_at DESC'
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        DatasetRecord(
            dataset_id=r[0], job_id=r[1], source_file=r[2], table_name=r[3], sheet=r[4],
            category=r[5], row_count=r[6], columns=json.loads(r[7]), saved_at=r[8],
        )
        for r in rows
    ]


def _find_dataset(conn: sqlite3.Connection, dataset_id: str) -> DatasetRecord | None:
    row = conn.execute(
        'SELECT dataset_id, job_id, source_file, table_name, sheet, category, row_count, columns_json, saved_at '
        'FROM "_datasets" WHERE dataset_id = ?',
        (dataset_id,),
    ).fetchone()
    if row is None:
        return None
    return DatasetRecord(
        dataset_id=row[0], job_id=row[1], source_file=row[2], table_name=row[3], sheet=row[4],
        category=row[5], row_count=row[6], columns=json.loads(row[7]), saved_at=row[8],
    )


def get_dataset(dataset_id: str) -> DatasetRecord | None:
    if not library_db_path().exists():
        return None
    conn = sqlite3.connect(f"file:{library_db_path()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        return _find_dataset(conn, dataset_id)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def read_dataset_rows(dataset_id: str, limit: int | None = 200, offset: int = 0) -> tuple[DatasetRecord, list[list]] | None:
    """None when the dataset_id doesn't exist. Row limit/offset let a
    caller page through a large saved dataset instead of always loading
    every row -- there's no cap on how many rows save_tables() will store
    (it mirrors whatever the parser already read), so an unbounded read
    here could be sizeable for a large CSV. limit=None reads every row
    (used by the CSV download route, which needs the whole dataset, not
    a page of it)."""
    if not library_db_path().exists():
        return None
    conn = sqlite3.connect(f"file:{library_db_path()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        record = _find_dataset(conn, dataset_id)
        if record is None:
            return None
        if limit is None:
            # SQLite has no "OFFSET without LIMIT" syntax -- a bare OFFSET
            # clause is a syntax error. LIMIT -1 is SQLite's documented
            # "no limit" sentinel, so this is the correct way to combine
            # "every row" with a real offset, not just a magic-number hack.
            rows = conn.execute(f'SELECT * FROM "{record.table_name}" LIMIT -1 OFFSET ?', (offset,)).fetchall()
        else:
            rows = conn.execute(
                f'SELECT * FROM "{record.table_name}" LIMIT ? OFFSET ?', (limit, offset)
            ).fetchall()
        return record, [list(r) for r in rows]
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def delete_dataset(dataset_id: str) -> bool:
    """True if a dataset was actually deleted, False if dataset_id didn't
    exist -- lets the route return a real 404 instead of a silent no-op."""
    conn = _connect()
    try:
        record = _find_dataset(conn, dataset_id)
        if record is None:
            return False
        conn.execute(f'DROP TABLE IF EXISTS "{record.table_name}"')
        conn.execute('DELETE FROM "_datasets" WHERE dataset_id = ?', (dataset_id,))
        conn.commit()
        return True
    finally:
        conn.close()
