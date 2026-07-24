import type { SourceTargetMapping } from "../api/types";
import { artifactUrl } from "../api/client";

export function SourceTargetMappingView({ mapping, jobId }: { mapping: SourceTargetMapping; jobId: string }) {
  return (
    <div>
      <div className="pii-summary">
        <p className="muted" style={{ margin: 0, flex: 1 }}>
          One mapping sheet per proposed BI table (see Business Use Cases) — exactly which source
          column becomes which target column, and the join/foreign-key logic when a table links
          more than one file.
        </p>
        <a className="btn-secondary" href={artifactUrl(jobId, "source_target_mapping.csv")} download>
          Download CSV
        </a>
      </div>

      {mapping.tables.length === 0 && (
        <p className="muted">No structured (table) columns found across the uploaded files.</p>
      )}

      {mapping.tables.map((t) => (
        <div key={t.name} className="table-block">
          <div className="table-block-header">
            <strong>{t.name}</strong>
          </div>
          {t.join_logic && (
            <div className="mapping-join-callout">
              <code>{t.join_logic}</code>
              <span className="muted small">{t.join_quality}</span>
            </div>
          )}
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Source File</th><th>Source Table</th><th>Source Column</th>
                  <th>Target Column</th><th>Type</th><th>Sample Values</th>
                </tr>
              </thead>
              <tbody>
                {t.columns.map((c, i) => (
                  <tr key={i}>
                    <td className="mono small">{c.source_file.split("/").pop()}</td>
                    <td className="small">{c.source_table.split("::").pop()}</td>
                    <td className="mono">{c.source_column}</td>
                    <td className="mono" style={{ color: "var(--accent)" }}>{c.target_column}</td>
                    <td><span className="dtype-chip">{c.data_type_guess}</span></td>
                    <td className="small muted">{c.sample_values.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
