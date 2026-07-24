import type { BIReport } from "../api/types";

export function BusinessUseCasesView({ report }: { report: BIReport }) {
  return (
    <div className="report-grid">
      <section className="report-card report-card-wide">
        <h3>Recommended Actions</h3>
        <ul>{report.business_use_cases.map((x, i) => <li key={i}>{x}</li>)}</ul>
        {report.business_use_cases.length === 0 && (
          <p className="muted">No specific actions were surfaced from this batch.</p>
        )}
      </section>
      <section className="report-card">
        <h3>Grounded In</h3>
        <p>Financial highlights, risk flags, and market signals from the High Level Analysis tab.</p>
      </section>
    </div>
  );
}
