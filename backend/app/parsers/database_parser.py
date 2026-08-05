"""Database Agent (L2) — uploaded SQLite files.

Scope note: this pipeline is file-upload based, so "Database" here covers
files you can upload directly (.db/.sqlite/.sqlite3). Live connections to
a running Postgres/MySQL/MongoDB server are a different integration
pattern (connection string + credentials, not a file) and are out of
scope for this agent — see README if you need that.
"""
import asyncio
import logging
import sqlite3

from app.models.schemas import FileCategory, ParsedDocument, TableBlock, TextBlock
from app.parsers.base import BaseParser

logger = logging.getLogger(__name__)

MAX_ROWS_PER_TABLE = 500
# See csv_parser.MAX_STRUCTURED_ROWS -- same rationale, per table here.
MAX_STRUCTURED_ROWS = 200_000


class DatabaseParser(BaseParser):
    category = FileCategory.DATABASE

    async def parse(self, file_path: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedDocument:
        doc = ParsedDocument(source_file=file_path, category=self.category)
        try:
            # Open read-only via URI so a corrupt/non-SQLite file fails fast
            # instead of sqlite3 silently creating a new empty database.
            conn = sqlite3.connect(f"file:{file_path}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                table_names = [row[0] for row in cur.fetchall()]

                if not table_names:
                    doc.warnings.append("No user tables found — not a valid SQLite database?")
                    return doc

                doc.text_blocks.append(TextBlock(text=f"Database contains {len(table_names)} table(s): "
                                                       f"{', '.join(table_names)}", kind="paragraph"))

                for table in table_names:
                    # One table's failure (locked, corrupted page, an
                    # unreadable column type) shouldn't abort every table
                    # after it in table_names -- catch per-table so the
                    # loop always reaches every table, not just the ones
                    # before the first failure.
                    try:
                        cur.execute(f'PRAGMA table_info("{table}")')
                        columns = [row[1] for row in cur.fetchall()]
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        row_count = cur.fetchone()[0]

                        doc.text_blocks.append(TextBlock(
                            text=f"Table '{table}': {row_count} rows, columns: {', '.join(columns)}",
                            kind="paragraph",
                        ))

                        cur.execute(f'SELECT * FROM "{table}" LIMIT {MAX_ROWS_PER_TABLE}')
                        rows = [[str(v) if v is not None else "" for v in row] for row in cur.fetchall()]
                        doc.tables.append(TableBlock(
                            sheet=table, headers=columns, rows=rows,
                            caption=f"{table} (showing up to {MAX_ROWS_PER_TABLE} of {row_count} rows)",
                        ))

                        # Populated for every table unconditionally, same
                        # reasoning as excel_parser.py -- the structured
                        # store consumes doc.full_tables for the whole
                        # database file at once, so a partial list would
                        # silently drop every other table from chat's SQL
                        # store entirely.
                        cur.execute(f'SELECT * FROM "{table}" LIMIT {MAX_STRUCTURED_ROWS}')
                        full_rows = [[str(v) if v is not None else "" for v in row] for row in cur.fetchall()]
                        doc.full_tables.append(TableBlock(
                            sheet=table, headers=columns, rows=full_rows,
                            caption=f"{table} ({len(full_rows)} of {row_count} rows)",
                        ))
                        if row_count > MAX_STRUCTURED_ROWS:
                            doc.warnings.append(
                                f"Structured-query store only indexed the first {MAX_STRUCTURED_ROWS} of "
                                f"{row_count} rows in table '{table}' -- aggregate SQL answers over this "
                                f"table may be incomplete."
                            )
                    except sqlite3.DatabaseError as exc:
                        logger.warning("Failed to read table '%s' in %s: %s", table, file_path, exc)
                        doc.warnings.append(f"Table '{table}' could not be read and was skipped: {exc}")

                doc.metadata = {"table_count": len(table_names), "parser": "sqlite3"}
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            doc.warnings.append(f"Not a readable SQLite database: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Database parse failed on %s", file_path)
            doc.warnings.append(f"Database parse error: {exc}")
        return doc
