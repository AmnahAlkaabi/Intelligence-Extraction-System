"""Structured Query -- text-to-SQL branch for chat questions that are
better answered by running an actual query against a job's structured
files (CSV/Excel/SQLite) than by semantic retrieval over embedded text
chunks. See storage/structured_store.py for how the underlying per-job
SQLite database gets populated at ingestion time.

Phase 1 scope, deliberately narrow: one-shot SQL generation (no
self-correction retry on a bad query -- that's a fast-follow), single LLM
call, SELECT-only enforced at execution time against a read-only
connection. Any failure along the way (no structured tables, LLM call
fails, generated SQL is unsafe/invalid, execution errors) returns None so
the caller (graphrag.answer_question) falls through to normal GraphRAG
retrieval -- same "a wrong or absent signal can never make the answer
worse than not routing at all" philosophy query_router.py uses for
category inference.
"""
import asyncio
import logging
import re
import sqlite3

from app.llm.client import get_llm_client
from app.models.schemas import ChatResponse, StructuredQueryResult
from app.storage import structured_store

logger = logging.getLogger(__name__)

MAX_RESULT_ROWS = 200

# Cheap heuristic for "this question probably wants a computed/filtered
# answer over tabular data," not a semantic-similarity search -- aggregate
# verbs, comparison language, and explicit row/table vocabulary. False
# negatives just mean a quantitative question gets a GraphRAG answer
# instead (same as today); false positives cost one extra LLM call that
# self-discards via _is_safe_select/NOT_ANSWERABLE below -- both fail
# safe, so this can stay a blunt regex rather than a classifier.
_QUANT_RE = re.compile(
    r"\b(how many|count|sum|total|average|avg|mean|median|maximum|max|minimum|min|"
    r"top \d+|greater than|less than|more than|fewer than|at least|at most|between|"
    r"group by|sort by|order by|filter|rows?|records?|entries|join|duplicate|distinct|unique)\b",
    re.IGNORECASE,
)

_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|create|replace|vacuum|reindex|"
    r"begin|commit|rollback)\b",
    re.IGNORECASE,
)

SQL_SYSTEM = """You write a single read-only SQLite SELECT query to answer a question, given a schema.

Rules:
- Output ONLY the SQL query -- no explanation, no markdown fences, no commentary.
- SELECT statements only. Never write anything that modifies data or schema.
- Use exactly the table and column names given in the schema (they are already valid SQLite identifiers).
- One statement only, no semicolons.
- If the question cannot be answered from this schema, output exactly: SELECT 'NOT_ANSWERABLE' AS error
"""


def looks_quantitative(question: str) -> bool:
    return bool(_QUANT_RE.search(question))


def _is_safe_select(sql: str) -> bool:
    s = sql.strip().rstrip(";").strip()
    if not s or ";" in s:
        return False
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        return False
    if _FORBIDDEN_RE.search(s):
        return False
    return True


def _extract_sql(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(sql)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _schema_prompt(manifest: list[dict]) -> str:
    lines = []
    for t in manifest:
        cols = ", ".join(f"{c} ({typ})" for c, typ in t["columns"])
        lines.append(f'Table "{t["table_name"]}" (from {t["source_file"]}, {t["row_count"]} rows): {cols}')
    return "\n".join(lines)


def _execute_readonly(job_id: str, sql: str) -> tuple[list[str], list[list[str]], bool]:
    conn = structured_store.open_readonly(job_id)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        cur = conn.execute(sql)
        headers = [d[0] for d in cur.description] if cur.description else []
        fetched = cur.fetchmany(MAX_RESULT_ROWS + 1)
        truncated = len(fetched) > MAX_RESULT_ROWS
        rows = [["" if v is None else str(v) for v in r] for r in fetched[:MAX_RESULT_ROWS]]
        return headers, rows, truncated
    finally:
        conn.close()


async def answer_structured_question(job_id: str, message: str) -> ChatResponse | None:
    manifest = structured_store.read_manifest(job_id)
    if not manifest:
        return None

    client = get_llm_client()
    schema = _schema_prompt(manifest)
    user_prompt = f"Schema:\n{schema}\n\nQuestion: {message}"

    try:
        resp = await client.complete("chat", SQL_SYSTEM, user_prompt, temperature=0.0, max_tokens=300)
    except Exception:
        logger.exception("SQL generation failed for job %s", job_id)
        return None

    sql = _extract_sql(resp.text)
    if not _is_safe_select(sql):
        logger.warning("Generated SQL rejected as unsafe/invalid for job %s: %r", job_id, sql)
        return None

    try:
        headers, rows, truncated = await asyncio.to_thread(_execute_readonly, job_id, sql)
    except sqlite3.Error as exc:
        logger.warning("Structured query execution failed for job %s: %s (sql=%r)", job_id, exc, sql)
        return None

    if headers == ["error"] and rows and rows[0] == ["NOT_ANSWERABLE"]:
        return None

    answer = (
        f"Ran a structured query against {len(manifest)} table(s) of extracted data "
        f"— {len(rows)} row(s) returned" + (", showing the first 200." if truncated else ".")
    )
    return ChatResponse(
        answer=answer,
        query_mode="structured",
        sql_used=sql,
        structured_result=StructuredQueryResult(headers=headers, rows=rows, row_count=len(rows), truncated=truncated),
    )
