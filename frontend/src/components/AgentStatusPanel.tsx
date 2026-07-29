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
    agents: ["BI Synthesizer", "Mapping Agent", "Insight Agent", "GraphRAG Chat Synthesizer"],
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
  const rowList = [...rows.values()];
  const doneCount = rowList.filter((r) => r.status === "completed").length;
  const activeCount = rowList.filter((r) => r.status === "running").length;
  const slowCount = rowList.filter((r) => r.slow).length;

  return (
    <div className="agent-board">
      <div className="agent-board-head">
        <div className="agent-board-title-row">
          <h3 className="agent-board-title">Agent Activity</h3>
          {activeCount > 0 && (
            <span className="live-pill"><span className="live-dot" />Live</span>
          )}
        </div>
        <div className="kpi-row">
          <div className="kpi kpi-total"><div className="kpi-n">{rowList.length}</div><div className="kpi-l">Total</div></div>
          <div className="kpi kpi-done"><div className="kpi-n">{doneCount}</div><div className="kpi-l">Done</div></div>
          <div className="kpi kpi-active"><div className="kpi-n">{activeCount}</div><div className="kpi-l">Active</div></div>
          <div className="kpi kpi-slow"><div className="kpi-n">{slowCount}</div><div className="kpi-l">Slow</div></div>
        </div>
      </div>

      {LANES.map((lane) => {
        const laneDone = lane.agents.filter((a) => rows.get(a)?.status === "completed").length;
        const laneActive = lane.agents.filter((a) => rows.get(a)?.status === "running").length;
        return (
          <div className="lane" key={lane.id}>
            <div className="lane-head">
              <span className="lane-badge">{lane.id}</span>
              <span className="lane-name">{lane.name}</span>
              <span className="lane-count">
                {laneDone}/{lane.agents.length} done{laneActive > 0 ? ` · ${laneActive} active` : ""}
              </span>
            </div>
            <div className="rail">
              <div className="lane-agents">
                {lane.agents.map((agent) => {
                  const row = rows.get(agent);
                  const status = row?.status ?? "idle";
                  const engine = agentEngine(agent);
                  return (
                    <div className={`agent-card agent-card-${status}`} data-engine={engine} key={agent}>
                      <div className="agent-icon-wrap">
                        {status === "running" && <span className="pulse-ring" />}
                        <span className="agent-icon-badge"><AgentIcon agent={agent} /></span>
                        {status === "completed" && (
                          <span className="done-badge">
                            <svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M4.5 12.5l5 5 10-11" />
                            </svg>
                          </span>
                        )}
                        {status === "failed" && <span className="fail-badge">!</span>}
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
                      {status === "running" && <div className="progress-track"><div className="progress-fill" /></div>}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        );
      })}
      <div className="pipeline-legend">
        {(["qwen", "kimi", "localml", "rules"] as const).map((eng) => (
          <span key={eng}><span className={`engine-dot eng-${eng}`} />{ENGINE_LABEL[eng]}</span>
        ))}
      </div>
    </div>
  );
}
