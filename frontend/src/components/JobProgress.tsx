import type { Job } from "../api/types";
import { StatusBadge } from "./StatusBadge";

export function JobProgress({ job }: { job: Job }) {
  return (
    <div className="progress-panel">
      {job.warnings.length > 0 && (
        <div className="warning-banner">
          <div className="warning-banner-title">⚠ Service connectivity issues detected</div>
          <ul>
            {job.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
          <p className="warning-banner-note">
            Processing continues with reduced functionality — parsing and chunking still run,
            but steps that depend on the affected service are skipped rather than hanging.
          </p>
        </div>
      )}
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
            {f.translated && (
              <span className="translated-chip" title={`Translated from '${f.detected_language}' to English before extraction`}>
                translated: {f.detected_language}
              </span>
            )}
            {f.error && <span className="progress-file-error">{f.error}</span>}
            {f.warnings.length > 0 && (
              <span className="progress-file-error" title={f.warnings.join("; ")}>
                {f.warnings[0]}{f.warnings.length > 1 ? ` (+${f.warnings.length - 1} more)` : ""}
              </span>
            )}
          </div>
        ))}
      </div>
      {job.error && <div className="error-banner">{job.error}</div>}
    </div>
  );
}
