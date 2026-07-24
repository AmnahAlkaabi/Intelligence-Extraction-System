import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { getJob } from "../api/client";
import type { Job } from "../api/types";
import { JobProgress } from "../components/JobProgress";
import { AgentStatusPanel, activeAgentCount } from "../components/AgentStatusPanel";
import { CollapsibleSection } from "../components/CollapsibleSection";
import { StatusBadge } from "../components/StatusBadge";
import { JobTitle } from "../components/JobTitle";
import { BIReportView } from "../components/BIReportView";
import { PIIReportView } from "../components/PIIReportView";
import { KnowledgeGraphView } from "../components/KnowledgeGraphView";
import { DataDumpView } from "../components/DataDumpView";
import { BusinessUseCasesView } from "../components/BusinessUseCasesView";
import { SourceTargetMappingView } from "../components/SourceTargetMappingView";
import { ChatPanel } from "../components/ChatPanel";

type Tab = "overview" | "pii" | "graph" | "usecases" | "mapping" | "data" | "chat";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "📋 High Level Analysis" },
  { id: "pii", label: "🛡️ PII/Masking Report" },
  { id: "graph", label: "🕸️ Knowledge Graph" },
  { id: "usecases", label: "💡 Business Use Cases" },
  { id: "mapping", label: "🔀 Source→Target Mapping" },
  { id: "data", label: "📦 Data Dump" },
  { id: "chat", label: "💬 Chat Q&A" },
];

const ACTIVE_STATUSES = new Set(["queued", "parsing", "extracting", "graph_build", "synthesizing"]);

export default function DashboardPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const autoSwitchedRef = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    async function poll() {
      try {
        const j = await getJob(jobId!);
        if (cancelled) return;
        setJob(j);
        if (j.status === "complete" && !autoSwitchedRef.current) {
          autoSwitchedRef.current = true;
          setTab("overview");
        }
        if (ACTIVE_STATUSES.has(j.status)) {
          pollRef.current = window.setTimeout(poll, 1500);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Failed to load job.");
      }
    }
    poll();
    return () => { cancelled = true; if (pollRef.current) clearTimeout(pollRef.current); };
  }, [jobId]);

  if (err) return <div className="page-narrow"><div className="error-banner">{err}</div></div>;
  if (!job) return <div className="page-narrow">Loading job…</div>;

  const isActive = ACTIVE_STATUSES.has(job.status);
  const chatAvailable = job.status === "complete" || job.status === "synthesizing" || job.status === "graph_build";
  const hasResult = !!job.result;
  const showOutputs = hasResult || chatAvailable;
  const running = activeAgentCount(job.agent_activity);

  return (
    <div className="page-wide">
      <div className="job-header-row">
        <JobTitle
          jobId={job.job_id}
          name={job.name}
          onRenamed={(name) => setJob((prev) => (prev ? { ...prev, name } : prev))}
        />
        <StatusBadge status={job.status} />
      </div>

      <CollapsibleSection title="Job Status" defaultOpen={isActive}>
        <JobProgress job={job} />
      </CollapsibleSection>

      {job.agent_activity.length > 0 && (
        <CollapsibleSection
          title="Agent Activity"
          defaultOpen={isActive}
          badge={<span className="section-badge">{running > 0 ? `${running} active` : "settled"}</span>}
        >
          <AgentStatusPanel activity={job.agent_activity} />
        </CollapsibleSection>
      )}

      {job.status === "failed" && (
        <div className="error-banner">Analysis failed: {job.error ?? "Unknown error."}</div>
      )}

      {isActive && !showOutputs && (
        <p className="muted" style={{ marginTop: 16 }}>
          Analysis in progress — this page updates automatically. GraphRAG chat unlocks once the
          knowledge graph has been built (before final synthesis finishes).
        </p>
      )}

      {showOutputs && (
        <section className="outputs-panel">
          <div className="outputs-panel-head">
            <h2 className="outputs-panel-title">📊 Outputs</h2>
            {!hasResult && (
              <span className="muted small">
                Synthesis still running — Chat is available now, other tabs populate once complete.
              </span>
            )}
          </div>
          <div className="tab-bar">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`tab-btn ${tab === t.id ? "tab-btn-active" : ""}`}
                onClick={() => setTab(t.id)}
                disabled={t.id === "chat" ? !chatAvailable : !hasResult}
              >
                {t.label}
              </button>
            ))}
          </div>
          {/* Every panel stays mounted and is only hidden via CSS, so switching
              tabs never unmounts ChatPanel (which would wipe its conversation)
              or loses scroll/zoom state in other views. */}
          <div className="tab-content">
            <div style={{ display: tab === "overview" ? "block" : "none" }}>
              {hasResult && <BIReportView report={job.result!.bi_report} />}
            </div>
            <div style={{ display: tab === "pii" ? "block" : "none" }}>
              {hasResult && <PIIReportView report={job.result!.compliance_report} jobId={job.job_id} />}
            </div>
            <div style={{ display: tab === "graph" ? "block" : "none" }}>
              {hasResult && <KnowledgeGraphView graph={job.result!.knowledge_graph} />}
            </div>
            <div style={{ display: tab === "usecases" ? "block" : "none" }}>
              {hasResult && <BusinessUseCasesView report={job.result!.bi_report} />}
            </div>
            <div style={{ display: tab === "mapping" ? "block" : "none" }}>
              {hasResult && <SourceTargetMappingView mapping={job.result!.source_target_mapping} jobId={job.job_id} />}
            </div>
            <div style={{ display: tab === "data" ? "block" : "none" }}>
              {hasResult && <DataDumpView job={job} />}
            </div>
            <div style={{ display: tab === "chat" ? "block" : "none" }}>
              {chatAvailable && <ChatPanel jobId={job.job_id} />}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
