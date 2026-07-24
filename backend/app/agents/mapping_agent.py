"""Mapping Agent (L3) -- deterministic Source -> Target Mapping Sheet.

Builds a literal data-dictionary/ETL table: every source column across
every uploaded structured file (CSV/Excel/Database/JSON tables), mapped to
a standardized target column name, plus join logic when the same
standardized column shows up in more than one file's table with
overlapping actual values. Purely structural (column names + sample
values) -- no LLM call, same rationale as Business Use Cases and Data
Quality: anything derivable from the data's own structure shouldn't be
left to a model to guess.
"""
import re

from app.models.schemas import ColumnMapping, DomainResult, JoinRule, SourceTargetMapping, TableBlock

# Canonical aliases: normalized token -> standardized token. A small,
# hand-picked MVP set covering the most common business-entity columns;
# anything not matched here just keeps its own normalized form -- this is
# meant to be tuned per deployment as real column-name conventions show up.
_ALIASES = {
    "cust": "customer", "client": "customer",
    "org": "company", "organization": "company", "vendor": "company", "supplier": "company",
    "amt": "amount", "total": "amount", "sum": "amount", "value": "amount",
    "dt": "date", "timestamp": "date", "created": "date", "updated": "date",
    "tel": "phone", "mobile": "phone", "telephone": "phone",
    "addr": "address",
    "mail": "email",
    "fullname": "name",
    "uuid": "id", "identifier": "id", "pk": "id",
    "desc": "description",
    "qty": "quantity",
    "curr": "currency",
}

_ID_RE = re.compile(r"(^id$|_id$|^id_|_id_)")


def _tokenize(column: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", column)  # camelCase -> camel_Case
    return [t for t in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if t]


def standardize_column(column: str) -> str:
    tokens = _tokenize(column) or ["field"]
    mapped = [_ALIASES.get(t, t) for t in tokens]
    # Collapse consecutive duplicates produced by alias expansion, e.g.
    # "customer" + "id" already reads as "customer_id".
    deduped: list[str] = []
    for t in mapped:
        if not deduped or deduped[-1] != t:
            deduped.append(t)
    return "_".join(deduped)


def _guess_type(column: str, samples: list[str]) -> str:
    norm = column.lower()
    if _ID_RE.search(norm):
        return "id"
    if "email" in norm:
        return "email"
    if "phone" in norm or "tel" in norm:
        return "phone"
    if any(k in norm for k in ("date", "dt", "time")):
        return "date"
    if any(k in norm for k in ("amount", "price", "total", "qty", "quantity", "amt", "count")):
        return "number"
    checked = [s for s in samples if s.strip()][:20]
    if checked:
        numeric_hits = 0
        for s in checked:
            try:
                float(s.replace(",", ""))
                numeric_hits += 1
            except ValueError:
                pass
        if numeric_hits / len(checked) >= 0.8:
            return "number"
    return "text"


def _table_label(result: DomainResult, table: TableBlock) -> str:
    name = table.sheet or table.caption or table.table_id
    return f"{result.source_file}::{name}"


def build_source_target_mapping(results: list[DomainResult]) -> SourceTargetMapping:
    columns: list[ColumnMapping] = []
    # target_column -> [(table label, column index, TableBlock, source_file)]
    groups: dict[str, list[tuple[str, int, TableBlock, str]]] = {}

    for result in results:
        for table in result.tables:
            if not table.headers:
                continue
            label = _table_label(result, table)
            for col_idx, header in enumerate(table.headers):
                if not header or not header.strip():
                    continue
                samples = [
                    row[col_idx] for row in table.rows[:20]
                    if col_idx < len(row) and row[col_idx]
                ]
                target = standardize_column(header)
                columns.append(ColumnMapping(
                    source_file=result.source_file,
                    source_table=label,
                    source_column=header,
                    target_column=target,
                    data_type_guess=_guess_type(header, samples),
                    sample_values=samples[:5],
                ))
                groups.setdefault(target, []).append((label, col_idx, table, result.source_file))

    joins: list[JoinRule] = []
    for target, occurrences in groups.items():
        # Cross-file only: two sheets in the same file sharing a column
        # name isn't the "linking multiple data" case this sheet exists
        # for -- that's just one table, no join needed.
        distinct_files = {o[3] for o in occurrences}
        if len(distinct_files) < 2:
            continue
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                label_a, idx_a, table_a, file_a = occurrences[i]
                label_b, idx_b, table_b, file_b = occurrences[j]
                if file_a == file_b:
                    continue
                values_a = {row[idx_a].strip().lower() for row in table_a.rows if idx_a < len(row) and row[idx_a]}
                values_b = {row[idx_b].strip().lower() for row in table_b.rows if idx_b < len(row) and row[idx_b]}
                if not values_a or not values_b:
                    continue
                shared = values_a & values_b
                union = values_a | values_b
                overlap = len(shared) / len(union) if union else 0.0
                # Require both a minimum absolute overlap and a minimum
                # ratio -- name match alone (e.g. two unrelated "name"
                # columns) isn't enough evidence of an actual join key.
                if len(shared) >= 2 and overlap >= 0.05:
                    joins.append(JoinRule(
                        left=f"{label_a}.{table_a.headers[idx_a]}",
                        right=f"{label_b}.{table_b.headers[idx_b]}",
                        target_column=target,
                        match_basis=f"column name -> '{target}'; {len(shared)} shared value(s), {overlap:.0%} overlap",
                        confidence=round(min(overlap * 2, 1.0), 2),
                    ))

    return SourceTargetMapping(columns=columns, joins=joins)
