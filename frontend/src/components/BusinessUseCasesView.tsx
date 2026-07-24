import type { BIReport } from "../api/types";

export function BusinessUseCasesView({ report }: { report: BIReport }) {
  const indices = report.business_use_cases;

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Metrics computed by linking two or more extraction results together (financial facts to
        entities, PII to source files, entity to entity via relations) — not LLM-guessed numbers.
      </p>
      <div className="index-grid">
        {indices.map((idx) => (
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
      {indices.length === 0 && <p className="muted">No indices could be computed from this batch.</p>}
    </div>
  );
}
