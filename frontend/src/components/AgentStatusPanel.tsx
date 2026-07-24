import type { AgentActivity } from "../api/types";
import { AgentIcon, agentEngine } from "./AgentIcons";

// Fixed lane layout so the board never reshuffles as activity streams in
// from files processed out of order by asyncio.gather -- an agent that
// hasn't run yet on this job still shows up in its lane, dimmed, rather
// than popping into existence mid-run.
const LANES: { id: string; name: string; agents: string[] }[] = [
  {
    id: "L1",
    name: "Format Specialists",
    agents: [
      "PDF Specialist", "Image/OCR Specialist", "CSV Specialist", "Excel Specialist", "JSON Specialist",
      "Office Specialist", "Email Specialist", "Database Specialist", "Code & Log Specialist",
      "Archive Specialist", "Media Specialist", "Web/XML Specialist", "Format Specialist",
    ],
  },
  {
    id: "L2",
    name: "Extraction & Analysis",
    agents: [
      "Translator", "Chunk/Embed Extractor", "Entity Extractor", "PII Extractor",
      "Financial Extractor", "Relation Extractor", "Data Quality Validator",
    ],
  },
  {
    id: "L3",
    name: "Synthesis",
    agents: ["BI Synthesizer"],
  },
];

const ENGINE_LABEL: Record<string, string> = {
  qwen: "Qwen", kimi: "Kimi2", localml: "Local ML", rules: "Rules",
};

type RowStatus = "idle" | "running" | "completed" | "failed" | "skipped";

interface AgentRow {
  agent: string;
  status: RowStatus;
  done: number;
  total: number;
  maxDurationMs: number;
  slow: boolean;
}

function summarize(activity: AgentActivity[]): Map<string, AgentRow> {
  const byAgent = new Map<string, AgentActivity[]>();
  for (const a of activity) {
    if (!byAgent.has(a.agent)) byAgent.set(a.agent, []);
    byAgent.get(a.agent)!.push(a);
  }

  const allMax = [...byAgent.values()]
    .map((items) => Math.max(0, ...items.map((i) => i.duration_ms ?? 0)))
    .filter((d) => d > 0)
    .sort((a, b) => a - b);
  const median = allMax.length ? allMax[Math.floor(allMax.length / 2)] : 0;
  const threshold = Math.max(median * 2, 4000);

  const rows = new Map<string, AgentRow>();
  for (const [agent, items] of byAgent) {
    const running = items.some((i) => i.status === "running");
    const failed = items.some((i) => i.status === "failed");
    const allSkipped = items.every((i) => i.status === "skipped");
    const status: RowStatus = running ? "running" : failed ? "failed" : allSkipped ? "skipped" : "completed";
    const durations = items.map((i) => i.duration_ms ?? 0).filter((d) => d > 0);
    const maxDurationMs = durations.length ? Math.max(...durations) : 0;
    const done = items.filter((i) => i.status !== "running").length;
    rows.set(agent, { agent, status, done, total: items.length, maxDurationMs, slow: maxDurationMs > threshold });
  }
  return rows;
}

function fmtMs(ms: number): string {
  if (ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const STATUS_LABEL: Record<RowStatus, string> = {
  idle: "Idle", running: "Active", completed: "Done", failed: "Failed", skipped: "Skipped",
};

export function activeAgentCount(activity: AgentActivity[]): number {
  return activity.filter((a) => a.status === "running").length;
}

export function AgentStatusPanel({ activity }: { activity: AgentActivity[] }) {
  if (activity.length === 0) return <p className="muted">No agent activity yet.</p>;
  const rows = summarize(activity);

  return (
    <div className="pipeline">
      {LANES.map((lane) => (
        <div className="lane" key={lane.id}>
          <div className="lane-head">
            <span className="lane-id">{lane.id}</span>
            <span className="lane-name">{lane.name}</span>
          </div>
          <div className="lane-agents">
            {lane.agents.map((agent) => {
              const row = rows.get(agent);
              const status = row?.status ?? "idle";
              const engine = agentEngine(agent);
              return (
                <div className={`agent-node agent-node-${status}`} data-engine={engine} key={agent}>
                  <div className="agent-icon-wrap">
                    {status === "running" && <span className="agent-spin-ring" />}
                    <span className="agent-icon"><AgentIcon agent={agent} /></span>
                    {status === "completed" && (
                      <span className="agent-done-check">
                        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 3 4.5 6v6c0 5 3.2 8 7.5 9 4.3-1 7.5-4 7.5-9V6Z" />
                          <path d="M9 12.3l2 2 4-4.3" />
                        </svg>
                      </span>
                    )}
                  </div>
                  <div className="agent-meta">
                    <div className="agent-name">{agent}</div>
                    <div className="agent-sub">
                      <span className={`engine-dot eng-${engine}`} />
                      {ENGINE_LABEL[engine]} · <span className="agent-state-txt">{STATUS_LABEL[status]}</span>
                      {row && row.total > 1 && ` · ${row.done}/${row.total} files`}
                      {row && row.slow && <span className="agent-status-slow-tag" title="Slower than the rest of this run">slow</span>}
                      {row && row.maxDurationMs > 0 && status !== "running" && ` · ${fmtMs(row.maxDurationMs)}`}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="pipeline-legend">
        {(["qwen", "kimi", "localml", "rules"] as const).map((eng) => (
          <span key={eng}><span className={`engine-dot eng-${eng}`} />{ENGINE_LABEL[eng]}</span>
        ))}
      </div>
    </div>
  );
}
