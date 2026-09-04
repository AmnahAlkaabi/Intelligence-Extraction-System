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


# ------------------------------------------------------------- boolean type --
# Issue: guess_column_type had no boolean category at all -- a column
# like is_paid with values [true, "yes", false, 1] (genuinely
# inconsistent: native JSON bool, string, bool, int, already flattened to
# text by table-building time) fell through to the generic "text" bucket,
# whose _matches_type check is an unconditional `return True`. It scored
# a perfect 100% type consistency and was never flagged, no matter how
# mixed the representations were. Unlike every other type here (one fixed
# universal definition of "valid"), booleans legitimately come in several
# mutually exclusive conventions (true/false, yes/no, y/n, 1/0) -- a
# column consistently using any ONE is clean data, not an error; what
# indicates a real problem is MIXING conventions within one column.

def test_mixed_convention_boolean_column_is_flagged():
    """The real ground-truth defect: true/"yes"/false/1 mixed together."""
    doc = _doc_with_table(["is_paid"], [["True"], ["yes"], ["False"], ["1"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    col = q.tables[0].columns[0]
    assert col.inferred_type == "boolean"
    assert col.type_consistency == 0.5  # 2 of 4 match the dominant true/false style
    assert any("don't match inferred type" in issue for issue in col.issues)


def test_consistent_yes_no_boolean_column_is_not_flagged():
    doc = _doc_with_table(["subscribed"], [["yes"], ["no"], ["yes"], ["yes"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    col = q.tables[0].columns[0]
    assert col.inferred_type == "boolean"
    assert col.type_consistency == 1.0
    assert not any("don't match inferred type" in issue for issue in col.issues)


def test_boolean_inferred_from_column_name_prefix():
    doc = _doc_with_table(["is_active"], [["true"], ["false"], ["true"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    assert q.tables[0].columns[0].inferred_type == "boolean"


def test_ordinary_numeric_column_with_0_1_values_is_not_misread_as_boolean():
    """"1"/"0" overlap with a boolean style but are deliberately excluded
    from value-only boolean sniffing -- a neutral column name with mostly
    0/1/2/3-style values must stay "number", not become a false-positive
    "boolean"."""
    doc = _doc_with_table(["delta_x"], [["0"], ["1"], ["3"], ["2"], ["1"], ["0"], ["5"]])
    result = DomainResult(domain="json", source_file="f.json", tables=doc.tables)

    q = assess_quality(doc, result)

    assert q.tables[0].columns[0].inferred_type == "number"
