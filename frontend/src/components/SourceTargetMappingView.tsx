import type { SourceTargetMapping } from "../api/types";
import { artifactUrl } from "../api/client";

export function SourceTargetMappingView({ mapping, jobId }: { mapping: SourceTargetMapping; jobId: string }) {
  return (
    <div>
      <div className="pii-summary">
        <p className="muted" style={{ margin: 0, flex: 1 }}>
          Data dictionary — every source column mapped to a standardized target column, computed
          structurally from column names and sample values (no LLM guessing).
        </p>
        <a className="btn-secondary" href={artifactUrl(jobId, "source_target_mapping.csv")} download>
          Download CSV
        </a>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Source File</th><th>Source Table</th><th>Source Column</th>
              <th>Target Column</th><th>Type</th><th>Sample Values</th>
            </tr>
          </thead>
          <tbody>
            {mapping.columns.map((c, i) => (
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
        {mapping.columns.length === 0 && (
          <p className="muted">No structured (table) columns found across the uploaded files.</p>
        )}
      </div>

      <section className="report-card report-card-wide" style={{ marginTop: 20 }}>
        <h3>Suggested Joins</h3>
        {mapping.joins.length === 0 && (
          <p className="muted">No cross-file joins detected — no shared column with overlapping values.</p>
        )}
        <ul>
          {mapping.joins.map((j, i) => (
            <li key={i}>
              <code>{j.left}</code> = <code>{j.right}</code> on <strong>{j.target_column}</strong>
              {" — "}{j.match_basis} (confidence {j.confidence})
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
