"""Builds the Data Dump tab's "where the structured data lives" view.

Two things the tab was missing once storage/dataset_library.py started
persisting every structured file's tables outside the job's own output
directory: (1) nothing showed WHERE that data actually is on disk, and
(2) every uploaded CSV/Excel/Database table was listed as its own
disconnected block even when several files shared the exact same column
shape (e.g. twelve monthly transaction exports) -- this groups those into
one combined structure instead, the same way a DBA would define one view
over several union-compatible tables rather than eyeballing N of them
side by side.

Column names/types are rendered Oracle-DESC-style (VARCHAR2/NUMBER
instead of SQLite's TEXT/INTEGER/REAL storage classes) purely as a
presentation convention -- there's no real Oracle database involved, the
underlying store is still SQLite (see dataset_library.py).
"""
from app.agents.mapping_agent import classify_entity, standardize_column
from app.models.schemas import DatasetRecord, OracleSchemaColumn, OracleSchemaGroup

# SQLite storage class -> Oracle datatype name. Sizes are nominal (this
# isn't validating against real data, just giving the DESC-style view
# recognizable Oracle types) -- REAL gets a decimal NUMBER precision to
# visually distinguish it from an INTEGER column's whole-number NUMBER.
_ORACLE_TYPE = {
    "TEXT": "VARCHAR2(255)",
    "INTEGER": "NUMBER(38)",
    "REAL": "NUMBER(18,4)",
}

# When the same standardized column has different SQLite types across the
# files being unioned (e.g. one file's "amount" column parsed as INTEGER,
# another's as REAL because of decimal cents), the merged column takes
# the WIDEST type rather than either one -- TEXT can hold anything a
# NUMBER can (as a string), and a decimal NUMBER can hold anything a
# whole-number one can. Never the other way around, or real values from
# whichever file used the wider type would round-trip lossy/wrong.
_WIDENING_RANK = {"INTEGER": 0, "REAL": 1, "TEXT": 2}


def _oracle_type(sqlite_type: str) -> str:
    return _ORACLE_TYPE.get(sqlite_type, "VARCHAR2(255)")


def _widest(types: list[str]) -> str:
    return max(types, key=lambda t: _WIDENING_RANK.get(t, _WIDENING_RANK["TEXT"]))


def _union_sql(member_tables: list[str]) -> str:
    return "\nUNION ALL\n".join(f'SELECT * FROM "{t}"' for t in member_tables)


def build_schema_catalog(datasets: list[DatasetRecord]) -> list[OracleSchemaGroup]:
    """Groups datasets whose standardized column sets are IDENTICAL --
    same columns, ignoring naming variants (standardize_column already
    folds "Cust ID"/"customer_id"/"CustomerID" together, same normalization
    mapping_agent.py uses for its cross-file join detection) -- into one
    OracleSchemaGroup apiece. A group of size 1 is just that one table,
    still rendered the same way for a consistent view; `combined` is what
    the UI keys off of to decide whether to show "N files combined".

    Column order within a group follows first-seen order across its
    members (a plain dict preserves insertion order) -- deterministic
    given a stable input order, not alphabetized, so it reads like the
    original table's column order for the common case of one dominant
    schema plus a few identically-shaped duplicates.
    """
    order: list[tuple[str, ...]] = []
    grouped: dict[tuple[str, ...], list[DatasetRecord]] = {}
    for ds in datasets:
        signature = tuple(sorted(standardize_column(name) for name, _ in ds.columns))
        if signature not in grouped:
            order.append(signature)
        grouped.setdefault(signature, []).append(ds)

    catalog: list[OracleSchemaGroup] = []
    for signature in order:
        members = grouped[signature]
        types_by_column: dict[str, list[str]] = {}
        for ds in members:
            for name, sqlite_type in ds.columns:
                types_by_column.setdefault(standardize_column(name), []).append(sqlite_type)

        columns = [
            OracleSchemaColumn(name=col.upper(), oracle_type=_oracle_type(_widest(types)))
            for col, types in types_by_column.items()
        ]
        combined = len(members) > 1
        member_tables = [m.table_name for m in members]

        entity = classify_entity(set(types_by_column.keys()))
        if entity:
            group_name = f"{entity.upper()}_UNIONED" if combined else entity.upper()
        elif combined:
            group_name = f"COMBINED_{members[0].table_name.upper()}"
        else:
            group_name = members[0].table_name.upper()

        catalog.append(OracleSchemaGroup(
            group_name=group_name,
            columns=columns,
            member_tables=member_tables,
            member_files=sorted({m.source_file for m in members}),
            row_count=sum(m.row_count for m in members),
            combined=combined,
            union_sql=_union_sql(member_tables) if combined else None,
        ))

    # Combined (multi-file) groups first, largest first -- that's the
    # information this feature exists to surface; standalone single-table
    # entries trail behind it, alphabetized for stable ordering.
    catalog.sort(key=lambda g: (not g.combined, -len(g.member_tables), g.group_name))
    return catalog
