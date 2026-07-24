import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadFiles } from "../api/client";
import { RobotIcon } from "../components/RobotIcon";

const SUPPORTED = [
  ["PDF", "📕"], ["Images", "🖼️"], ["CSV / TSV", "📊"], ["JSON / JSONL", "{ }"],
  ["Excel", "📗"], ["Office", "📄"], ["Database", "🗄️"], ["Code & Logs", "🧾"],
  ["Archives", "🗜️"], ["Web / XML", "🌐"],
];

const FLEET = ["Orchestrator", "Extraction Agents", "Synthesiser", "Validator"];

export default function UploadPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    setFiles((prev) => [...prev, ...Array.from(incoming)]);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      addFiles(e.dataTransfer.files);
    },
    [addFiles]
  );

  const removeFile = (idx: number) => setFiles((prev) => prev.filter((_, i) => i !== idx));

  const submit = async () => {
    if (files.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await uploadFiles(files);
      navigate(`/jobs/${job.job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setSubmitting(false);
    }
  };

  return (
    <div className="landing">
      <div className="bot-hero-wrap">
        <RobotIcon className="bot-hero" visorClassName="visor" />
      </div>

      <h1 className="landing-title">New Analysis</h1>
      <p className="landing-sub">
        Drop in contracts, invoices, spreadsheets, scans — anything. The Data Analysis Agent routes
        each file to the right extraction agent, runs everything on your on-prem Qwen and Kimi2
        models, and hands back a briefing you can question directly.
      </p>

      <div
        className={`dropzone ${dragging ? "is-drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => document.getElementById("file-input")?.click()}
      >
        <input id="file-input" type="file" multiple hidden onChange={(e) => addFiles(e.target.files)} />
        <div className="dropzone-ico">⇪</div>
        <div className="dropzone-title">Drag &amp; drop files here, or click to browse</div>
        <div className="dropzone-sub">No size limit set locally · nothing leaves this network</div>
      </div>

      <div className="filetype-row">
        {SUPPORTED.map(([name, icon]) => (
          <span key={name} className="filetype-chip">{icon} {name}</span>
        ))}
      </div>

      {files.length > 0 && (
        <div className="file-list">
          {files.map((f, i) => (
            <div key={`${f.name}-${i}`} className="file-row">
              <span className="file-name">{f.name}</span>
              <span className="file-size">{(f.size / 1024).toFixed(1)} KB</span>
              <button className="file-remove" onClick={() => removeFile(i)}>✕</button>
            </div>
          ))}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      <button className="landing-cta" disabled={files.length === 0 || submitting} onClick={submit}>
        {submitting ? "Starting analysis…" : `Analyze ${files.length || ""} file${files.length === 1 ? "" : "s"} →`}
      </button>

      <div className="section-caption">Agents standing by</div>
      <div className="agent-fleet">
        {FLEET.map((label) => (
          <div key={label} className="agent-card">
            <div className="agent-bot-wrap">
              <RobotIcon className="agent-bot" visorClassName="visor" />
            </div>
            <div className="agent-status-row-fleet"><span className="agent-status-dot-fleet" />Ready</div>
            <div className="agent-label">{label}</div>
          </div>
        ))}
      </div>

      <div className="landing-meta-row">
        <span>Qwen · extraction &amp; translation</span>
        <span>Kimi2 · synthesis &amp; chat</span>
        <span>Neo4j · knowledge graph</span>
      </div>
    </div>
  );
}
