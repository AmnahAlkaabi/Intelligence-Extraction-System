import type { Job } from "../api/types";

function elapsed(job: Job): string {
  const ms = new Date(job.updated_at).getTime() - new Date(job.created_at).getTime();
  if (!(ms > 0)) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function CompletionHero({ job }: { job: Job }) {
  const result = job.result;
  if (!result) return null;

  const unprocessed = job.files.filter((f) => f.status !== "complete").length;

  const stats: { label: string; value: string | number; danger?: boolean }[] = [
    { label: "Files processed", value: job.files.length - unprocessed },
    { label: "Entities found", value: result.knowledge_graph.entities.length },
    { label: "Relations mapped", value: result.knowledge_graph.relations.length },
    { label: "PII findings", value: result.compliance_report.pii_inventory.length },
    { label: "BI tables proposed", value: result.bi_report.business_use_cases.length },
    { label: "Time to complete", value: elapsed(job) },
  ];
  if (unprocessed > 0) {
    stats.splice(1, 0, { label: "Unprocessed", value: unprocessed, danger: true });
  }

  return (
    <div className="completion-hero">
      <div className="completion-hero-badge">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9.5" />
          <path d="M8 12.3l2.6 2.6 5.4-5.8" />
        </svg>
      </div>
      <div className="completion-hero-body">
        <div className="completion-hero-title">Analysis complete</div>
        <p className="completion-hero-sub">
          {job.name || job.job_id} finished processing — every output below is ready to review.
        </p>
        <div className="completion-hero-stats">
          {stats.map((s) => (
            <div className="completion-stat" key={s.label}>
              <span className={`completion-stat-value${s.danger ? " completion-stat-value-danger" : ""}`}>{s.value}</span>
              <span className="completion-stat-label">{s.label}</span>
            </div>
          ))}
        </div>
        {unprocessed > 0 && (
          <p className="completion-hero-warning">
            {unprocessed} file{unprocessed === 1 ? "" : "s"} didn't finish processing:{" "}
            {job.files.filter((f) => f.status !== "complete").map((f) => f.filename.split("/").pop()).join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}
