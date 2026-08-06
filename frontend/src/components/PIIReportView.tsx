import { useState } from "react";
import type { ComplianceReport } from "../api/types";
import { artifactUrl } from "../api/client";

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

const DETECTION_LABEL: Record<string, string> = {
  rules_checksum: "✓ Verified",
  rules_shape: "Pattern match",
  llm: "AI-flagged",
};

const DETECTION_TITLE: Record<string, string> = {
  rules_checksum: "Matched an internationally standardized ID format (passport MRZ, IBAN, card number, or a national ID) with its checksum verified -- the strongest confidence signal this system produces.",
  rules_shape: "Matched a known national ID format's shape/length, but has no verified checksum for this format.",
  llm: "Flagged by the language model from context, not matched against a specific standardized format.",
};

export function PIIReportView({ report, jobId }: { report: ComplianceReport; jobId: string }) {
  const [filter, setFilter] = useState<string | null>(null);
  const rows = filter ? report.pii_inventory.filter((f) => f.severity === filter) : report.pii_inventory;

  return (
    <div>
      <div className="pii-summary">
        {SEVERITY_ORDER.map((sev) => (
          <button
            key={sev}
            className={`pii-count pii-count-${sev} ${filter === sev ? "pii-count-active" : ""}`}
            onClick={() => setFilter(filter === sev ? null : sev)}
          >
            <span className="pii-count-num">{report.severity_counts[sev] ?? 0}</span>
            <span className="pii-count-label">{sev}</span>
          </button>
        ))}
        <a className="btn-secondary" href={artifactUrl(jobId, "pii_inventory.csv")} download>
          Download CSV
        </a>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr><th>Severity</th><th>Category</th><th>Value</th><th>Detection</th><th>Source File</th><th>Location</th></tr>
          </thead>
          <tbody>
            {rows.map((f) => {
              const method = f.detection_method ?? "llm";
              return (
                <tr key={f.finding_id}>
                  <td><span className={`sev-chip sev-${f.severity}`}>{f.severity}</span></td>
                  <td>{f.category}</td>
                  <td className="mono">{f.value_redacted}</td>
                  <td>
                    <span className={`detect-chip detect-${method}`} title={DETECTION_TITLE[method]}>
                      {DETECTION_LABEL[method]}
                    </span>
                  </td>
                  <td className="mono small">{f.source_file.split("/").pop()}</td>
                  <td className="small">{f.location ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <p className="muted">No PII findings{filter ? ` at severity "${filter}"` : ""}.</p>}
      </div>

      <div className="report-grid" style={{ marginTop: 20 }}>
        <section className="report-card">
          <h3>Compliance Gaps</h3>
          <ul>{report.gap_flags.map((x, i) => <li key={i}>{x}</li>)}</ul>
          {report.gap_flags.length === 0 && <p className="muted">None flagged.</p>}
        </section>
        <section className="report-card">
          <h3>Remediation Actions</h3>
          <ul>{report.remediation.map((x, i) => <li key={i}>{x}</li>)}</ul>
          {report.remediation.length === 0 && <p className="muted">None suggested.</p>}
        </section>
      </div>
    </div>
  );
}
