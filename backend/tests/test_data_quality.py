"""Tests for agents/data_quality.py -- previously zero coverage.

Focused on the type-consistency reporting-threshold fix: a column's
type_consistency was computed correctly regardless of table size, but
never surfaced as an issue (or counted against the score) unless the
column had at least 10 non-null values -- a small file (very common for
JSON: test exports, API samples) with an obviously wrong-typed value in a
3-9 row column got zero signal, no matter how blatant the mismatch.
"""
from app.agents.data_quality import assess_quality
from app.models.schemas import DomainResult, FileCategory, ParsedDocument, TableBlock


def _doc_with_table(headers: list[str], rows: list[list[str]]) -> ParsedDocument:
    doc = ParsedDocument(source_file="f.json", category=FileCategory.JSON_)
    doc.tables.append(TableBlock(headers=headers, rows=rows, caption="Full data"))
    return doc


def test_small_table_type_mismatch_is_now_flagged():
    """1 of 4 values ("five") doesn't match the inferred 'number' type --
    previously invisible because the column only has 4 non-null values."""
    doc = _doc_with_table(
        ["quantity"],
        [["3"], ["five"], ["2"], ["1"]],
    )
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    table = q.tables[0]
    col = next(c for c in table.columns if c.column == "quantity")
    assert col.type_consistency == 0.75
    assert any("don't match inferred type" in issue for issue in col.issues)
    assert any("quantity" in issue for issue in q.issues)


def test_two_value_column_still_not_flagged():
    """n=2 stays below the reporting floor -- a single mismatched value
    out of two is too coarse (0%/50%/100%) to be a meaningful signal."""
    doc = _doc_with_table(["quantity"], [["3"], ["five"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    col = q.tables[0].columns[0]
    assert not any("don't match inferred type" in issue for issue in col.issues)


def test_clean_small_column_is_not_flagged():
    doc = _doc_with_table(["quantity"], [["3"], ["2"], ["1"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    col = q.tables[0].columns[0]
    assert col.type_consistency == 1.0
    assert not any("don't match inferred type" in issue for issue in col.issues)
