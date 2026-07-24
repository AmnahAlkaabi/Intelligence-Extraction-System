import type { BIReport } from "../api/types";

function qualityClass(score: number): string {
  if (score >= 90) return "dq-good";
  if (score >= 70) return "dq-minor";
  if (score >= 40) return "dq-partial";
  return "dq-severe";
}

export function BIReportView({ report }: { report: BIReport }) {
  return (
    <div>
      <h3 className="section-block-title">What's in the Dump</h3>
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

      {report.corpus_overview.length > 0 && (
        <div className="index-grid" style={{ marginTop: 12 }}>
          {report.corpus_overview.map((idx) => (
            <div key={idx.name} className="index-card">
              <div className="index-name">{idx.name}</div>
              <div className="index-value">{idx.value}</div>
              <p className="index-basis">{idx.basis}</p>
              {idx.sources.length > 0 && (
                <div className="index-sources">
                  {idx.sources.map((s) => (
                    <span key={s} className="index-source-chip" title={s}>{s.split("/").pop()}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <h3 className="section-block-title">File-by-File Breakdown</h3>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>File</th><th>Category</th><th>Entities</th><th>Relations</th>
              <th>PII</th><th>Financial Facts</th><th>Tables</th><th>Chunks</th>
            </tr>
          </thead>
          <tbody>
            {report.file_breakdown.map((fs) => (
              <tr key={fs.source_file}>
                <td className="mono small" title={fs.source_file}>{fs.source_file.split("/").pop()}</td>
                <td className="small">{fs.category}</td>
                <td>{fs.entities}</td>
                <td>{fs.relations}</td>
                <td>{fs.pii_findings}</td>
                <td>{fs.financial_facts}</td>
                <td>{fs.tables}</td>
                <td>{fs.chunks}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {report.file_breakdown.length === 0 && <p className="muted">No files processed.</p>}
      </div>

      <h3 className="section-block-title">Quality Check Stats</h3>
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
    </div>
  );
}
