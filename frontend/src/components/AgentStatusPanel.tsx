import type { AgentActivity } from "../api/types";

// Fixed pipeline order so the panel doesn't reshuffle row order as
// activity streams in from files processed out of order by asyncio.gather.
const AGENT_ORDER = [
  "PDF Specialist", "Image/OCR Specialist", "CSV Specialist", "Excel Specialist", "JSON Specialist",
  "Office Specialist", "Email Specialist", "Database Specialist", "Code & Log Specialist",
  "Archive Specialist", "Media Specialist", "Web/XML Specialist", "Format Specialist",
  "Translator", "Chunk/Embed Extractor", "Entity Extractor", "PII Extractor",
  "Financial Extractor", "Relation Extractor", "Data Quality Validator", "BI Synthesizer",
];

type RowStatus = "running" | "completed" | "failed" | "skipped";

interface AgentRow {
  agent: string;
  status: RowStatus;
  done: number;
  total: number;
  maxDurationMs: number;
  avgDurationMs: number;
  slow: boolean;
}

function summarize(activity: AgentActivity[]): AgentRow[] {
  const byAgent = new Map<string, AgentActivity[]>();
  for (const a of activity) {
    if (!byAgent.has(a.agent)) byAgent.set(a.agent, []);
    byAgent.get(a.agent)!.push(a);
  }

  const rows: Omit<AgentRow, "slow">[] = [];
  for (const [agent, items] of byAgent) {
    const running = items.some((i) => i.status === "running");
    const failed = items.some((i) => i.status === "failed");
    const allSkipped = items.every((i) => i.status === "skipped");
    const status: RowStatus = running ? "running" : failed ? "failed" : allSkipped ? "skipped" : "completed";
    const durations = items.map((i) => i.duration_ms ?? 0).filter((d) => d > 0);
    const maxDurationMs = durations.length ? Math.max(...durations) : 0;
    const avgDurationMs = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
    const done = items.filter((i) => i.status !== "running").length;
    rows.push({ agent, status, done, total: items.length, maxDurationMs, avgDurationMs });
  }

  // Flag outliers relative to the run's own median, so "slow" is a
  // signal for tuning this deployment rather than a fixed guess at what
  // "slow" should mean across very different hardware.
  const allMax = rows.map((r) => r.maxDurationMs).filter((d) => d > 0).sort((a, b) => a - b);
  const median = allMax.length ? allMax[Math.floor(allMax.length / 2)] : 0;
  const threshold = Math.max(median * 2, 4000);

  return rows
    .map((r) => ({ ...r, slow: r.maxDurationMs > threshold }))
    .sort((a, b) => {
      const ia = AGENT_ORDER.indexOf(a.agent);
      const ib = AGENT_ORDER.indexOf(b.agent);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
}

function fmtMs(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const STATUS_LABEL: Record<RowStatus, string> = {
  running: "Working",
  completed: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

export function activeAgentCount(activity: AgentActivity[]): number {
  return activity.filter((a) => a.status === "running").length;
}

export function AgentStatusPanel({ activity }: { activity: AgentActivity[] }) {
  if (activity.length === 0) return <p className="muted">No agent activity yet.</p>;
  const rows = summarize(activity);

  return (
    <div className="agent-status-list scroll-list">
      {rows.map((r) => (
        <div key={r.agent} className="agent-status-row">
          <span className={`agent-status-dot agent-status-dot-${r.status}`} />
          <span className="agent-status-name">{r.agent}</span>
          <span className={`agent-status-pill agent-status-pill-${r.status}`}>{STATUS_LABEL[r.status]}</span>
          <span className="agent-status-count">{r.done}/{r.total} file{r.total === 1 ? "" : "s"}</span>
          <span className={`agent-status-duration${r.slow ? " agent-status-duration-slow" : ""}`}>
            {fmtMs(r.maxDurationMs)}
            {r.slow && <span className="agent-status-slow-tag" title="Slower than the rest of this run — worth tuning">slow</span>}
          </span>
        </div>
      ))}
    </div>
  );
}
