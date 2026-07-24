"""Local filesystem storage for uploads and generated output artifacts.

Air-gapped deployments have no object storage service by default, so
everything lives under UPLOAD_DIR / OUTPUT_DIR (mounted as Docker volumes
in production so data survives container restarts).
"""
import csv
import io
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.models.schemas import SynthesisOutput, TableBlock

logger = logging.getLogger(__name__)


def _ensure_dirs() -> tuple[Path, Path]:
    settings = get_settings()
    upload_dir = Path(settings.upload_dir)
    output_dir = Path(settings.output_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir, output_dir


async def save_upload(job_id: str, filename: str, content: bytes) -> str:
    upload_dir, _ = _ensure_dirs()
    job_dir = upload_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name  # strip any path components
    dest = job_dir / safe_name
    dest.write_bytes(content)
    return str(dest)


def job_output_dir(job_id: str) -> Path:
    _, output_dir = _ensure_dirs()
    d = output_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json_report(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    path = d / "report.json"
    path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return str(path)


def write_markdown_report(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    bi, comp, kg = output.bi_report, output.compliance_report, output.knowledge_graph

    lines = ["# Intelligence Extraction Report", "", "## Executive Summary", bi.executive_summary, ""]

    lines += ["## Data Quality", "Deterministic per-file quality assessment (Validator agent).", ""]
    for q in bi.data_quality:
        lines.append(f"- **{q.source_file}: {q.score}/100 ({q.completeness})**")
        for issue in q.issues:
            lines.append(f"  - {issue}")
    lines.append("")

    lines += ["## Key Entities", *[f"- {e}" for e in bi.key_entities], ""]
    lines += ["## Financial Highlights", *[f"- {x}" for x in bi.financial_highlights], ""]
    lines += ["## Risks & Red Flags", *[f"- {x}" for x in bi.risks], ""]
    lines += ["## Market Signals", *[f"- {x}" for x in bi.market_signals], ""]
    lines += ["## Business Use Cases", "Cross-file/cross-data indices computed from the extraction results.", ""]
    for idx in bi.business_use_cases:
        sources_str = f" ({', '.join(idx.sources)})" if idx.sources else ""
        lines.append(f"- **{idx.name}: {idx.value}** — {idx.basis}{sources_str}")
    lines.append("")

    lines += ["## PII / Masking Report", f"Severity counts: {comp.severity_counts}", ""]
    lines += ["### PII Inventory"]
    for f in comp.pii_inventory[:200]:
        lines.append(f"- [{f.severity.upper()}] {f.category}: {f.value_redacted} ({f.source_file})")
    lines += ["", "### Compliance Gaps", *[f"- {x}" for x in comp.gap_flags], ""]
    lines += ["### Remediation", *[f"- {x}" for x in comp.remediation], ""]

    lines += ["## Knowledge Graph", f"Entities: {len(kg.entities)}, Relations: {len(kg.relations)}", ""]
    lines += ["### Top Relations"]
    for r in kg.relations[:100]:
        lines.append(f"- {r.source_entity} --[{r.relation_type}]--> {r.target_entity}")

    lines += ["", "## Data Dump", f"Files processed: {len(output.data_dump.files_processed)}",
              f"Total chunks indexed: {output.data_dump.chunk_count}"]

    stm = output.source_target_mapping
    lines += ["", "## Source to Target Mapping", "Data dictionary: source column -> standardized target column.", ""]
    lines += ["| Source File | Source Table | Source Column | Target Column | Type |",
              "|---|---|---|---|---|"]
    for c in stm.columns:
        lines.append(f"| {c.source_file} | {c.source_table} | {c.source_column} | {c.target_column} | {c.data_type_guess} |")
    if stm.joins:
        lines += ["", "### Suggested Joins"]
        for j in stm.joins:
            lines.append(f"- `{j.left}` = `{j.right}` on **{j.target_column}** ({j.match_basis}, confidence {j.confidence})")

    content = "\n".join(lines)
    path = d / "report.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_pii_csv(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    path = d / "pii_inventory.csv"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["severity", "category", "value_redacted", "source_file", "location"])
    for f in output.compliance_report.pii_inventory:
        writer.writerow([f.severity, f.category, f.value_redacted, f.source_file, f.location or ""])
    path.write_text(buf.getvalue(), encoding="utf-8")
    return str(path)


def write_mapping_csv(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    path = d / "source_target_mapping.csv"
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_file", "source_table", "source_column", "target_column", "data_type_guess", "sample_values"])
    for c in output.source_target_mapping.columns:
        writer.writerow([c.source_file, c.source_table, c.source_column, c.target_column,
                          c.data_type_guess, "; ".join(c.sample_values)])
    path.write_text(buf.getvalue(), encoding="utf-8")
    return str(path)


def write_graph_json(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    path = d / "knowledge_graph.json"
    payload = {
        "nodes": [e.model_dump() for e in output.knowledge_graph.entities],
        "edges": [r.model_dump() for r in output.knowledge_graph.relations],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)


def write_tables_csv(job_id: str, tables: list[TableBlock]) -> list[str]:
    d = job_output_dir(job_id) / "tables"
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in tables:
        name = (t.caption or t.table_id).replace("/", "_").replace(" ", "_")[:60]
        path = d / f"{name}_{t.table_id}.csv"
        buf = io.StringIO()
        writer = csv.writer(buf)
        if t.headers:
            writer.writerow(t.headers)
        writer.writerows(t.rows)
        path.write_text(buf.getvalue(), encoding="utf-8")
        paths.append(str(path))
    return paths
