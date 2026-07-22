import type { BIReport } from "../api/types";

export function BIReportView({ report }: { report: BIReport }) {
  return (
    <div className="report-grid">
      <section className="report-card report-card-wide">
        <h3>Executive Summary</h3>
        <p>{report.executive_summary}</p>
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
