import type { BIReport } from "../api/types";

export function BusinessUseCasesView({ report }: { report: BIReport }) {
  const tables = report.business_use_cases;
  const joined = tables.filter((t) => t.join_logic);
  const standalone = tables.filter((t) => !t.join_logic);

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Tables proposed for the BI layer — computed by standardizing every uploaded table's columns
        and, where two files share a real overlapping key, cross-linking them into a joined table.
        Not LLM-guessed: every proposal here is backed by an executed join over the actual rows.
      </p>

      {joined.length > 0 && (
        <>
          <h3 className="section-block-title">Cross-Linked Tables</h3>
          <div className="bi-table-grid">
            {joined.map((t) => (
              <div key={t.name} className="bi-table-card bi-table-card-linked">
                <div className="bi-table-name">{t.name}</div>
                <p className="bi-table-purpose">{t.purpose}</p>
                <div className="bi-table-grain">Grain: {t.grain}</div>
                <div className="bi-table-join">
                  <code>{t.join_logic}</code>
                  <div className="bi-table-join-quality">{t.join_quality}</div>
                </div>
                <div className="index-sources">
                  {t.source_files.map((s) => (
                    <span key={s} className="index-source-chip" title={s}>{s.split("/").pop()}</span>
                  ))}
                </div>
                <div className="bi-table-colcount">{t.columns.length} columns — see Source→Target Mapping for detail</div>
              </div>
            ))}
          </div>
        </>
      )}

      {standalone.length > 0 && (
        <>
          <h3 className="section-block-title">Standalone Tables</h3>
          <div className="bi-table-grid">
            {standalone.map((t) => (
              <div key={t.name} className="bi-table-card">
                <div className="bi-table-name">{t.name}</div>
                <p className="bi-table-purpose">{t.purpose}</p>
                <div className="bi-table-grain">Grain: {t.grain}</div>
                <div className="index-sources">
                  {t.source_files.map((s) => (
                    <span key={s} className="index-source-chip" title={s}>{s.split("/").pop()}</span>
                  ))}
                </div>
                <div className="bi-table-colcount">{t.columns.length} columns</div>
              </div>
            ))}
          </div>
        </>
      )}

      {tables.length === 0 && <p className="muted">No structured tables were found to propose BI tables from.</p>}
    </div>
  );
}
