import type { JobStatusValue } from "../api/types";

const LABELS: Record<JobStatusValue, string> = {
  queued: "Queued",
  parsing: "Parsing",
  extracting: "Extracting",
  graph_build: "Building Graph",
  synthesizing: "Synthesizing",
  complete: "Complete",
  failed: "Failed",
  awaiting_batch_confirm: "Awaiting Confirmation",
  skipped: "Skipped",
};

export function StatusBadge({ status }: { status: JobStatusValue }) {
  return <span className={`status-badge status-${status}`}>{LABELS[status]}</span>;
}
