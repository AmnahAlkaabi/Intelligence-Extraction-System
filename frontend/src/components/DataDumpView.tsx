import { useEffect, useState } from "react";
import type { DataDumpTable, FileCategory, Job, SourceDocSummary } from "../api/types";
import { artifactUrl, getDataDumpDocuments, getDataDumpTables, sourcePreviewUrl, tableDownloadUrl } from "../api/client";
import { AgentIcon } from "./AgentIcons";
import { CATEGORY_AGENT, CATEGORY_LABEL, CATEGORY_ORDER } from "../lib/fileCategories";

interface FileEntry {
  source_file: string;
  category: FileCategory;
  doc?: SourceDocSummary;
  tables: DataDumpTable[];
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

function tableCsvFilename(t: DataDumpTable): string {
  return `${(t.caption ?? t.table_id).replace(/[/\s]/g, "_").slice(0, 60)}_${t.table_id}.csv`;
}

function TableCard({ jobId, table }: { jobId: string; table: DataDumpTable }) {
  return (
    <div className="table-block">
      <div className="table-block-header">
        <strong>{table.caption ?? table.sheet ?? table.table_id}</strong>
        <a href={tableDownloadUrl(jobId, tableCsvFilename(table))} download>Download CSV</a>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead><tr>{table.headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>
            {table.rows.slice(0, 20).map((row, ri) => (
              <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.rows.length > 20 && (
        <p className="muted small">Showing 20 of {table.rows.length} rows — download CSV for full data.</p>
      )}
    </div>
  );
}

function DocCard({ jobId, entry }: { jobId: string; entry: FileEntry }) {
  const { doc, tables } = entry;
  const [imgFailed, setImgFailed] = useState(false);
  const hasPreview = doc?.has_preview && !imgFailed;
  const hasInfo = !!(doc?.entities.length || doc?.pii_types.length || doc?.text_excerpt);

  return (
    <div className="doc-card">
      <div className={hasPreview ? "doc-card-split" : undefined}>
        {hasPreview && (
          <div className="doc-card-media">
            <img
              src={sourcePreviewUrl(jobId, basename(entry.source_file))}
              alt={basename(entry.source_file)}
              loading="lazy"
              onError={() => setImgFailed(true)}
            />
          </div>
        )}
        <div className="doc-card-body">
          <div className="doc-card-name" title={entry.source_file}>{basename(entry.source_file)}</div>
          {!hasInfo && tables.length === 0 && (
            <p className="muted small">No information was extracted from this file.</p>
          )}
          {doc && doc.entities.length > 0 && (
            <div className="doc-chip-row">
              {doc.entities.map((e) => <span className="doc-chip" key={e}>{e}</span>)}
            </div>
          )}
          {doc && doc.pii_types.length > 0 && (
            <div className="doc-chip-row">
              {doc.pii_types.map((p) => <span className="doc-chip doc-chip-pii" key={p}>{p}</span>)}
            </div>
          )}
          {doc?.text_excerpt && <p className="doc-excerpt">"{doc.text_excerpt}"</p>}
        </div>
      </div>
      {tables.length > 0 && (
        <div className="doc-card-tables">
          {tables.map((t) => <TableCard key={t.table_id} jobId={jobId} table={t} />)}
        </div>
      )}
    </div>
  );
}

export function DataDumpView({ job }: { job: Job }) {
  const [tables, setTables] = useState<DataDumpTable[]>([]);
  const [docs, setDocs] = useState<SourceDocSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getDataDumpTables(job.job_id), getDataDumpDocuments(job.job_id)])
      .then(([t, d]) => { setTables(t); setDocs(d); })
      .finally(() => setLoading(false));
  }, [job.job_id]);

  const dump = job.result?.data_dump;

  const byFile = new Map<string, FileEntry>();
  for (const d of docs) {
    byFile.set(d.source_file, { source_file: d.source_file, category: d.category, doc: d, tables: [] });
  }
  for (const t of tables) {
    let entry = byFile.get(t.source_file);
    if (!entry) {
      entry = { source_file: t.source_file, category: t.category, tables: [] };
      byFile.set(t.source_file, entry);
    }
    entry.tables.push(t);
  }

  const groups = CATEGORY_ORDER
    .map((cat) => ({ cat, files: [...byFile.values()].filter((f) => f.category === cat) }))
    .filter((g) => g.files.length > 0);

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

      <h3 style={{ marginTop: 24 }}>By File Type</h3>
      {loading && <p className="muted">Loading…</p>}
      {!loading && groups.length === 0 && <p className="muted">No files were processed.</p>}
      {groups.map(({ cat, files }) => (
        <div className="file-group doc-file-group" key={cat}>
          <div className="file-group-head">
            <span className="file-group-icon"><AgentIcon agent={CATEGORY_AGENT[cat]} /></span>
            <span className="file-group-name">{CATEGORY_LABEL[cat]}</span>
            <span className="file-group-count">{files.length} file{files.length === 1 ? "" : "s"}</span>
          </div>
          <div className="doc-card-list">
            {files.map((f) => <DocCard key={f.source_file} jobId={job.job_id} entry={f} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
