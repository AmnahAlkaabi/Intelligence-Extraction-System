import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { getJob } from "../api/client";
import type { Job } from "../api/types";
import { JobProgress } from "../components/JobProgress";
import { BIReportView } from "../components/BIReportView";
import { PIIReportView } from "../components/PIIReportView";
import { KnowledgeGraphView } from "../components/KnowledgeGraphView";
import { DataDumpView } from "../components/DataDumpView";
import { BusinessUseCasesView } from "../components/BusinessUseCasesView";
import { ChatPanel } from "../components/ChatPanel";

type Tab = "overview" | "pii" | "graph" | "usecases" | "data" | "chat";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "📋 High Level Analysis" },
  { id: "pii", label: "🛡️ PII/Masking Report" },
  { id: "graph", label: "🕸️ Knowledge Graph" },
  { id: "usecases", label: "💡 Business Use Cases" },
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

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    async function poll() {
      try {
        const j = await getJob(jobId!);
        if (cancelled) return;
        setJob(j);
        if (j.status === "complete") setTab("overview");
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

  const chatAvailable = job.status === "complete" || job.status === "synthesizing" || job.status === "graph_build";

  return (
    <div className="page-wide">
      <h1 className="page-title mono-id">Job {job.job_id}</h1>
      <JobProgress job={job} />

      {job.status === "complete" && job.result && (
        <div className="tabs">
          <div className="tab-bar">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`tab-btn ${tab === t.id ? "tab-btn-active" : ""}`}
                onClick={() => setTab(t.id)}
                disabled={t.id === "chat" && !chatAvailable}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="tab-content">
            {tab === "overview" && <BIReportView report={job.result.bi_report} />}
            {tab === "pii" && <PIIReportView report={job.result.compliance_report} jobId={job.job_id} />}
            {tab === "graph" && <KnowledgeGraphView graph={job.result.knowledge_graph} />}
            {tab === "usecases" && <BusinessUseCasesView report={job.result.bi_report} />}
            {tab === "data" && <DataDumpView job={job} />}
            {tab === "chat" && <ChatPanel jobId={job.job_id} />}
          </div>
        </div>
      )}

      {job.status === "failed" && (
        <div className="error-banner">Analysis failed: {job.error ?? "Unknown error."}</div>
      )}

      {ACTIVE_STATUSES.has(job.status) && (
        <p className="muted" style={{ marginTop: 16 }}>
          Analysis in progress — this page updates automatically. GraphRAG chat unlocks once the
          knowledge graph has been built (before final synthesis finishes).
        </p>
      )}
    </div>
  );
}
