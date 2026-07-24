import type { BIReport } from "../api/types";

function qualityClass(score: number): string {
  if (score >= 90) return "dq-good";
  if (score >= 70) return "dq-minor";
  if (score >= 40) return "dq-partial";
  return "dq-severe";
}

export function BIReportView({ report }: { report: BIReport }) {
  return (
    <div className="report-grid">
      <section className="report-card report-card-wide">
        <h3>Executive Summary</h3>
        <p>{report.executive_summary}</p>
      </section>
      <section className="report-card report-card-wide">
        <h3>Data Quality</h3>
        {report.data_quality.length === 0 && <p className="muted">No files assessed.</p>}
        <div className="dq-list">
          {report.data_quality.map((q) => (
            <div className="dq-row" key={q.source_file}>
              <span className={`dq-score ${qualityClass(q.score)}`}>{q.score}</span>
              <div className="dq-body">
                <div className="dq-header">
                  <span className="dq-file">{q.source_file}</span>
                  <span className="dq-completeness">{q.completeness}</span>
                </div>
                {q.issues.length > 0 && (
                  <ul className="dq-issues">
                    {q.issues.map((issue, i) => <li key={i}>{issue}</li>)}
                  </ul>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className="report-card">
        <h3>Key Entities</h3>
        <ul>{report.key_entities.map((e) => <li key={e}>{e}</li>)}</ul>
        {report.key_entities.length === 0 && <p className="muted">None identified.</p>}
      </section>
      <section className="report-card">
        <h3>Financial Highlights</h3>
        <ul>{report.financial_highlights.map((x, i) => <li key={i}>{x}</li>)}</ul>
        {report.financial_highlights.length === 0 && <p className="muted">None identified.</p>}
      </section>
      <section className="report-card">
        <h3>Risks &amp; Red Flags</h3>
        <ul>{report.risks.map((x, i) => <li key={i}>{x}</li>)}</ul>
        {report.risks.length === 0 && <p className="muted">None identified.</p>}
      </section>
      <section className="report-card">
        <h3>Market Signals</h3>
        <ul>{report.market_signals.map((x, i) => <li key={i}>{x}</li>)}</ul>
        {report.market_signals.length === 0 && <p className="muted">None identified.</p>}
      </section>
    </div>
  );
}
