import { useState } from "react";
import type { Job } from "../api/types";
import { StatusBadge } from "./StatusBadge";

const TRUNCATE_AT = 110;

export function ErrorText({ text, className = "progress-file-error" }: { text: string; className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > TRUNCATE_AT;
  const shown = expanded || !isLong ? text : text.slice(0, TRUNCATE_AT) + "…";
  return (
    <div className={className}>
      <span>{shown}</span>
      {isLong && (
        <button className="error-toggle" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "show less" : "show more"}
        </button>
      )}
    </div>
  );
}

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
      <div className="progress-files scroll-list">
        {job.files.map((f) => (
          <div key={f.filename} className="progress-file-row">
            <div className="progress-file-main">
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
            </div>
            {f.error && <ErrorText text={f.error} />}
            {f.warnings.length > 0 && <ErrorText text={f.warnings.join(" · ")} />}
          </div>
        ))}
      </div>
      {job.error && <div className="error-banner">{job.error}</div>}
    </div>
  );
}
