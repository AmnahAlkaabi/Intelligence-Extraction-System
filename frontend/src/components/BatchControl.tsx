import { useState } from "react";
import type { Job } from "../api/types";
import { continueBatch, stopBatches } from "../api/client";

export function BatchControl({ job, onUpdated }: { job: Job; onUpdated: (job: Job) => void }) {
  const [busy, setBusy] = useState<"continue" | "stop" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const latest = job.batch_summaries[job.batch_summaries.length - 1];
  const remainingBatches = job.total_batches - job.current_batch;
  const totalEntities = job.batch_summaries.reduce((a, s) => a + s.entities_found, 0);
  const totalRelations = job.batch_summaries.reduce((a, s) => a + s.relations_found, 0);
  const totalPii = job.batch_summaries.reduce((a, s) => a + s.pii_found, 0);
  const totalFinancial = job.batch_summaries.reduce((a, s) => a + s.financial_facts_found, 0);
  const filesProcessed = job.batch_summaries.reduce((a, s) => a + s.file_count, 0);
  const filesRemaining = job.files.length - filesProcessed;

  async function handleContinue() {
    setBusy("continue");
    setErr(null);
    try {
      const updated = await continueBatch(job.job_id);
      onUpdated(updated);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to continue.");
    } finally {
      setBusy(null);
    }
  }

  async function handleStop() {
    if (!confirm(`Stop here? ${filesRemaining} remaining file${filesRemaining === 1 ? "" : "s"} won't be analyzed, and the report will be built from the ${filesProcessed} file${filesProcessed === 1 ? "" : "s"} already processed.`)) {
      return;
    }
    setBusy("stop");
    setErr(null);
    try {
      const updated = await stopBatches(job.job_id);
      onUpdated(updated);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to stop.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="batch-control">
      <div className="batch-control-head">
        <span className="batch-control-title">
          Batch {job.current_batch} of {job.total_batches} processed
        </span>
        <span className="muted small">
          {filesProcessed} of {job.files.length} files analyzed so far
        </span>
      </div>

      {latest && (
        <div className="batch-control-summary">
          <div className="batch-summary-row">
            <span>This batch:</span>
            <span>{latest.file_count} files</span>
            <span>{latest.entities_found} entities</span>
            <span>{latest.relations_found} relations</span>
            <span>{latest.pii_found} PII findings</span>
            <span>{latest.financial_facts_found} financial facts</span>
          </div>
          {latest.top_files.length > 0 && (
            <p className="muted small batch-top-files">
              Top-ranked in this batch: {latest.top_files.join(", ")}
            </p>
          )}
          <div className="batch-summary-row batch-summary-total">
            <span>Running total:</span>
            <span>{totalEntities} entities</span>
            <span>{totalRelations} relations</span>
            <span>{totalPii} PII findings</span>
            <span>{totalFinancial} financial facts</span>
          </div>
        </div>
      )}

      <p className="muted small">
        {remainingBatches} batch{remainingBatches === 1 ? "" : "es"} remaining ({filesRemaining} file
        {filesRemaining === 1 ? "" : "s"}), ranked by likely importance. Continue to process the next batch,
        or stop here and build the report from what's been analyzed so far.
      </p>

      {err && <div className="error-banner">{err}</div>}

      <div className="batch-control-actions">
        <button className="btn-primary" onClick={handleContinue} disabled={busy !== null}>
          {busy === "continue" ? "Starting…" : `Continue to Batch ${job.current_batch + 1}`}
        </button>
        <button className="btn-secondary" onClick={handleStop} disabled={busy !== null}>
          {busy === "stop" ? "Stopping…" : "Stop here"}
        </button>
      </div>
    </div>
  );
}
