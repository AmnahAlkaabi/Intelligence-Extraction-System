import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { cancelJob, deleteJob, getJob } from "../api/client";
import type { Job } from "../api/types";
import { JobProgress } from "../components/JobProgress";
import { AgentStatusPanel, activeAgentCount } from "../components/AgentStatusPanel";
import { BatchControl } from "../components/BatchControl";
import { CollapsibleSection } from "../components/CollapsibleSection";
import { StatusBadge } from "../components/StatusBadge";
import { JobTitle } from "../components/JobTitle";
import { BIReportView } from "../components/BIReportView";
import { CompletionHero } from "../components/CompletionHero";
import { PIIReportView } from "../components/PIIReportView";
import { KnowledgeGraphView } from "../components/KnowledgeGraphView";
import { DataDumpView } from "../components/DataDumpView";
import { BusinessUseCasesView } from "../components/BusinessUseCasesView";
import { SourceTargetMappingView } from "../components/SourceTargetMappingView";
import { ChatPanel } from "../components/ChatPanel";

type Tab = "overview" | "pii" | "graph" | "usecases" | "mapping" | "data" | "chat";

const TABS: { id: Tab; icon: string; label: string; desc: string }[] = [
  { id: "overview", icon: "📋", label: "High Level Analysis", desc: "Executive summary & corpus overview" },
  { id: "pii", icon: "🛡️", label: "PII / Masking Report", desc: "Severity-ranked PII inventory" },
  { id: "graph", icon: "🕸️", label: "Knowledge Graph", desc: "Entities & relations, explorable" },
  { id: "usecases", icon: "💡", label: "Business Use Cases", desc: "Proposed BI tables" },
  { id: "mapping", icon: "🔀", label: "Source → Target", desc: "Mapping sheet per BI table" },
  { id: "data", icon: "📦", label: "Data Dump", desc: "Raw extracted records by type" },
  { id: "chat", icon: "💬", label: "Chat Q&A", desc: "Ask questions, grounded in the graph" },
];

const ACTIVE_STATUSES = new Set(["queued", "parsing", "extracting", "graph_build", "synthesizing"]);

export default function DashboardPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [err, setErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const pollRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);
  const autoSwitchedRef = useRef(false);
  const navigate = useNavigate();

  // Factored out (rather than inline in the mount effect) so continuing or
  // stopping a paused batch job can restart the exact same polling cadence
  // -- AWAITING_BATCH_CONFIRM isn't in ACTIVE_STATUSES, so the mount
  // effect's own chain already stopped scheduling itself by the time the
  // user acts on it.
  const pollOnce = useCallback(async () => {
    if (!jobId) return;
    try {
      const j = await getJob(jobId);
      if (cancelledRef.current) return;
      setJob(j);
      if (j.status === "complete" && !autoSwitchedRef.current) {
        autoSwitchedRef.current = true;
        setTab("overview");
      }
      if (ACTIVE_STATUSES.has(j.status)) {
        pollRef.current = window.setTimeout(pollOnce, 1500);
      }
    } catch (e) {
      if (!cancelledRef.current) setErr(e instanceof Error ? e.message : "Failed to load job.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    cancelledRef.current = false;
    pollOnce();
    return () => {
      cancelledRef.current = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [jobId, pollOnce]);

  function handleBatchUpdate(updated: Job) {
    setJob(updated);
    if (ACTIVE_STATUSES.has(updated.status)) {
      if (pollRef.current) clearTimeout(pollRef.current);
      pollRef.current = window.setTimeout(pollOnce, 1500);
    }
  }

  if (err) return <div className="page-narrow"><div className="error-banner">{err}</div></div>;
  if (!job) return <div className="page-narrow">Loading job…</div>;

  const isActive = ACTIVE_STATUSES.has(job.status);
  const isPaused = job.status === "awaiting_batch_confirm";
  const chatAvailable = job.status === "complete" || job.status === "synthesizing" || job.status === "graph_build";
  const hasResult = !!job.result;
  const showOutputs = hasResult || chatAvailable;
  const running = activeAgentCount(job.agent_activity);

  async function handleCancel() {
    if (!confirm("Cancel this job? Whatever hasn't finished processing yet will be marked as not analyzed. This can't be undone.")) {
      return;
    }
    setCancelling(true);
    try {
      const updated = await cancelJob(job!.job_id);
      handleBatchUpdate(updated);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to cancel job.");
    } finally {
      setCancelling(false);
    }
  }

  async function handleDelete() {
    if (isActive) {
      alert(`This job is still ${job!.status} — wait for it to finish before deleting it.`);
      return;
    }
    if (!confirm(`Delete "${job!.name || job!.job_id}"? This removes its uploaded files and all generated outputs. This can't be undone.`)) {
      return;
    }
    setDeleting(true);
    try {
      await deleteJob(job!.job_id);
      navigate("/history");
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to delete job.");
      setDeleting(false);
    }
  }

  return (
    <div className="page-wide">
      <div className="job-header-row">
        <JobTitle
          jobId={job.job_id}
          name={job.name}
          onRenamed={(name) => setJob((prev) => (prev ? { ...prev, name } : prev))}
        />
        <StatusBadge status={job.status} />
        {isActive && (
          <button className="job-delete-btn job-delete-btn-inline" title="Cancel job" disabled={cancelling} onClick={handleCancel}>
            {cancelling ? "Cancelling…" : "⏹ Cancel"}
          </button>
        )}
        <button className="job-delete-btn job-delete-btn-inline" title="Delete job" disabled={deleting} onClick={handleDelete}>
          {deleting ? "Deleting…" : "🗑 Delete"}
        </button>
      </div>

      {isPaused && <BatchControl job={job} onUpdated={handleBatchUpdate} />}

      <CollapsibleSection title="Job Status" defaultOpen={isActive || isPaused || job.stopped_early}>
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

      {job.status === "complete" && <CompletionHero job={job} />}

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
          <div className="tile-grid">
            {TABS.map((t) => {
              const disabled = t.id === "chat" ? !chatAvailable : !hasResult;
              const ready = t.id === "chat" ? chatAvailable : hasResult;
              return (
                <button
                  key={t.id}
                  className={`tile ${tab === t.id ? "tile-active" : ""} ${!ready ? "tile-pending" : ""} ${disabled ? "tile-disabled" : ""}`}
                  onClick={() => setTab(t.id)}
                  disabled={disabled}
                >
                  <span className="tile-stat" />
                  <span className="tile-ic">{t.icon}</span>
                  <span className="tile-t">{t.label}</span>
                  <span className="tile-d">{t.desc}</span>
                </button>
              );
            })}
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
              {hasResult && <KnowledgeGraphView graph={job.result!.knowledge_graph} active={tab === "graph"} />}
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
              {chatAvailable && <ChatPanel jobId={job.job_id} onMessageSent={pollOnce} />}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
