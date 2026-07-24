import { useState } from "react";
import type { FileCategory, FileProgress, Job } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { AgentIcon } from "./AgentIcons";

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

// Mirrors backend/app/agents/domain_managers.py's _SPECIALIST_NAMES so the
// grouped file list uses the exact same glyph as the agent working on it.
const CATEGORY_AGENT: Record<FileCategory, string> = {
  pdf: "PDF Specialist",
  image: "Image/OCR Specialist",
  csv: "CSV Specialist",
  excel: "Excel Specialist",
  json: "JSON Specialist",
  office: "Office Specialist",
  email: "Email Specialist",
  database: "Database Specialist",
  code: "Code & Log Specialist",
  archive: "Archive Specialist",
  media: "Media Specialist",
  web: "Web/XML Specialist",
  unknown: "Format Specialist",
};

const CATEGORY_LABEL: Record<FileCategory, string> = {
  pdf: "PDF", image: "Image", csv: "CSV", excel: "Excel", json: "JSON",
  office: "Office", email: "Email", database: "Database", code: "Code & Log",
  archive: "Archive", media: "Media", web: "Web/XML", unknown: "Other",
};

const CATEGORY_ORDER: FileCategory[] = [
  "pdf", "image", "csv", "excel", "json", "office", "email",
  "database", "code", "archive", "media", "web", "unknown",
];

function extractionSummary(f: FileProgress): string | null {
  const parts: string[] = [];
  if (f.entities_found) parts.push(`${f.entities_found} entit${f.entities_found === 1 ? "y" : "ies"}`);
  if (f.relations_found) parts.push(`${f.relations_found} relation${f.relations_found === 1 ? "" : "s"}`);
  if (f.pii_found) parts.push(`${f.pii_found} PII finding${f.pii_found === 1 ? "" : "s"}`);
  if (f.financial_facts_found) parts.push(`${f.financial_facts_found} financial fact${f.financial_facts_found === 1 ? "" : "s"}`);
  if (f.tables_found) parts.push(`${f.tables_found} table${f.tables_found === 1 ? "" : "s"}`);
  if (parts.length === 0 && f.chunks_found) parts.push(`${f.chunks_found} text chunk${f.chunks_found === 1 ? "" : "s"}`);
  return parts.length ? parts.join(" · ") : null;
}

function currentAgent(job: Job, filename: string): string | null {
  for (let i = job.agent_activity.length - 1; i >= 0; i--) {
    const a = job.agent_activity[i];
    if (a.file === filename && a.status === "running") return a.agent;
  }
  return null;
}

function FileDetail({ job, f }: { job: Job; f: FileProgress }) {
  if (f.status === "complete") {
    const summary = extractionSummary(f);
    return summary
      ? <span className="progress-file-detail progress-file-detail-found">Extracted: {summary}</span>
      : <span className="progress-file-detail progress-file-detail-empty">No info extracted</span>;
  }
  if (f.status === "extracting" || f.status === "parsing") {
    const agent = currentAgent(job, f.filename);
    return <span className="progress-file-detail progress-file-detail-active">→ {agent ?? "Reading"}…</span>;
  }
  if (f.status === "queued") {
    return <span className="progress-file-detail progress-file-detail-queued">Queued</span>;
  }
  return null;
}

export function JobProgress({ job }: { job: Job }) {
  const groups = CATEGORY_ORDER
    .map((cat) => ({ cat, files: job.files.filter((f) => f.category === cat) }))
    .filter((g) => g.files.length > 0);

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
      <div className="file-groups scroll-list">
        {groups.map(({ cat, files }) => {
          const done = files.filter((f) => f.status === "complete").length;
          return (
            <div className="file-group" key={cat}>
              <div className="file-group-head">
                <span className="file-group-icon"><AgentIcon agent={CATEGORY_AGENT[cat]} /></span>
                <span className="file-group-name">{CATEGORY_LABEL[cat]}</span>
                <span className="file-group-count">{files.length} file{files.length === 1 ? "" : "s"}</span>
                <span className="file-group-progress">{done}/{files.length} extracted</span>
              </div>
              <div className="file-group-rows">
                {files.map((f) => (
                  <div key={f.filename} className={`progress-file-row progress-file-row-${f.status}`}>
                    <div className="progress-file-main">
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
                    <FileDetail job={job} f={f} />
                    {f.error && <ErrorText text={f.error} />}
                    {f.warnings.length > 0 && <ErrorText text={f.warnings.join(" · ")} />}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {job.error && <div className="error-banner">{job.error}</div>}
    </div>
  );
}
