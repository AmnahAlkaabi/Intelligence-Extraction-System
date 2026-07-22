import type { Job } from "../api/types";
import { StatusBadge } from "./StatusBadge";

export function JobProgress({ job }: { job: Job }) {
  return (
    <div className="progress-panel">
      <div className="progress-header">
        <StatusBadge status={job.status} />
        <div className="progress-bar-track">
          <div className="progress-bar-fill" style={{ width: `${job.progress_pct}%` }} />
        </div>
        <span className="progress-pct">{job.progress_pct.toFixed(0)}%</span>
      </div>
      <div className="progress-files">
        {job.files.map((f) => (
          <div key={f.filename} className="progress-file-row">
            <span className={`ft-dot ft-dot-${f.category}`} />
            <span className="progress-file-name" title={f.filename}>
              {f.filename.split("/").pop()}
            </span>
            <StatusBadge status={f.status} />
            {f.error && <span className="progress-file-error">{f.error}</span>}
          </div>
        ))}
      </div>
      {job.error && <div className="error-banner">{job.error}</div>}
    </div>
  );
}
