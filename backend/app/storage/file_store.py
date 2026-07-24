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
from app.models.schemas import Job, SynthesisOutput, TableBlock

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


def write_job_state(job: Job) -> None:
    """Snapshots the job's own state (status, per-file progress, agent
    activity, and -- once complete -- the full result) to disk on every
    meaningful change, so job history survives a backend restart instead
    of living only in the in-memory registry.
    """
    path = job_output_dir(job.job_id) / "job_state.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")


def load_all_job_states() -> list[Job]:
    _, output_dir = _ensure_dirs()
    jobs: list[Job] = []
    if not output_dir.exists():
        return jobs
    for job_dir in sorted(output_dir.iterdir()):
        state_path = job_dir / "job_state.json"
        if not state_path.exists():
            continue
        try:
            jobs.append(Job.model_validate_json(state_path.read_text(encoding="utf-8")))
        except Exception:
            logger.exception("Failed to load persisted job state from %s", state_path)
    return jobs


def write_json_report(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    path = d / "report.json"
    path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return str(path)


def write_markdown_report(job_id: str, output: SynthesisOutput) -> str:
    d = job_output_dir(job_id)
    bi, comp, kg = output.bi_report, output.compliance_report, output.knowledge_graph

    lines = ["# Intelligence Extraction Report", "", "## High Level Analysis", ""]
    lines += ["### What's in the Dump", bi.executive_summary, ""]
    for idx in bi.corpus_overview:
        sources_str = f" ({', '.join(idx.sources)})" if idx.sources else ""
        lines.append(f"- **{idx.name}: {idx.value}** — {idx.basis}{sources_str}")
    lines.append("")
    lines += ["### File-by-File Breakdown", "",
              "| File | Category | Entities | Relations | PII | Financial Facts | Tables | Chunks |",
              "|---|---|---|---|---|---|---|---|"]
    for fs in bi.file_breakdown:
        lines.append(f"| {fs.source_file} | {fs.category} | {fs.entities} | {fs.relations} | "
                      f"{fs.pii_findings} | {fs.financial_facts} | {fs.tables} | {fs.chunks} |")
    lines.append("")

    lines += ["### Quality Check Stats", "Deterministic per-file quality assessment (Validator agent).", ""]
    for q in bi.data_quality:
        lines.append(f"- **{q.source_file}: {q.score}/100 ({q.completeness})**")
        for issue in q.issues:
            lines.append(f"  - {issue}")
    lines.append("")

    lines += ["## Key Entities", *[f"- {e}" for e in bi.key_entities], ""]
    lines += ["## Financial Highlights", *[f"- {x}" for x in bi.financial_highlights], ""]
    lines += ["## Risks & Red Flags", *[f"- {x}" for x in bi.risks], ""]
    lines += ["## Market Signals", *[f"- {x}" for x in bi.market_signals], ""]
    lines += ["## Business Use Cases", "Proposed BI-layer tables, computed from column standardization and executed joins.", ""]
    for t in bi.business_use_cases:
        lines.append(f"- **{t.name}** — {t.purpose}")
        lines.append(f"  - Grain: {t.grain}")
        if t.join_logic:
            lines.append(f"  - Join: `{t.join_logic}` — {t.join_quality}")
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

    lines += ["", "## Source to Target Mapping", "One mapping sheet per proposed BI table (see Business Use Cases).", ""]
    for t in output.source_target_mapping.tables:
        lines += [f"### {t.name}", ""]
        if t.join_logic:
            lines.append(f"**Join:** `{t.join_logic}`  \n**Join quality:** {t.join_quality}")
            lines.append("")
        lines += ["| Source File | Source Table | Source Column | Target Column | Type |",
                  "|---|---|---|---|---|"]
        for c in t.columns:
            lines.append(f"| {c.source_file} | {c.source_table} | {c.source_column} | {c.target_column} | {c.data_type_guess} |")
        lines.append("")

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
    writer.writerow(["bi_table", "join_logic", "source_file", "source_table", "source_column",
                      "target_column", "data_type_guess", "sample_values"])
    for t in output.source_target_mapping.tables:
        for c in t.columns:
            writer.writerow([t.name, t.join_logic or "", c.source_file, c.source_table, c.source_column,
                              c.target_column, c.data_type_guess, "; ".join(c.sample_values)])
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
