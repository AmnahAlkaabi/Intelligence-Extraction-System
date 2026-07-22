import { useEffect, useState } from "react";
import type { Job, TableBlock } from "../api/types";
import { artifactUrl, getDataDumpTables, tableDownloadUrl } from "../api/client";

export function DataDumpView({ job }: { job: Job }) {
  const [tables, setTables] = useState<TableBlock[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDataDumpTables(job.job_id).then(setTables).finally(() => setLoading(false));
  }, [job.job_id]);

  const dump = job.result?.data_dump;

  return (
    <div>
      <div className="report-grid">
        <section className="report-card">
          <h3>Provenance</h3>
          <p>{dump?.files_processed.length ?? 0} file(s) processed</p>
          <p>{dump?.chunk_count ?? 0} chunks indexed for RAG</p>
        </section>
        <section className="report-card">
          <h3>Export Bundle</h3>
          <div className="export-links">
            <a href={artifactUrl(job.job_id, "report.json")} download>Full Report (JSON)</a>
            <a href={artifactUrl(job.job_id, "report.md")} download>Full Report (Markdown)</a>
            <a href={artifactUrl(job.job_id, "knowledge_graph.json")} download>Knowledge Graph (JSON)</a>
            <a href={artifactUrl(job.job_id, "pii_inventory.csv")} download>PII Inventory (CSV)</a>
          </div>
        </section>
      </div>

      <h3 style={{ marginTop: 24 }}>Extracted Tables</h3>
      {loading && <p className="muted">Loading tables…</p>}
      {!loading && tables.length === 0 && <p className="muted">No tabular data was extracted.</p>}
      {tables.map((t) => (
        <div key={t.table_id} className="table-block">
          <div className="table-block-header">
            <strong>{t.caption ?? t.sheet ?? t.table_id}</strong>
            <a href={tableDownloadUrl(job.job_id, `${(t.caption ?? t.table_id).replace(/[/\s]/g, "_").slice(0, 60)}_${t.table_id}.csv`)} download>
              Download CSV
            </a>
          </div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr>{t.headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
              <tbody>
                {t.rows.slice(0, 20).map((row, ri) => (
                  <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{cell}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.rows.length > 20 && <p className="muted small">Showing 20 of {t.rows.length} rows — download CSV for full data.</p>}
        </div>
      ))}
    </div>
  );
}
