import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadFiles } from "../api/client";

const SUPPORTED = [
  ["PDF", "📕"], ["Images", "🖼️"], ["CSV / TSV", "📊"], ["JSON / JSONL", "{ }"],
  ["Excel", "📗"],
];

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
    <div className="page-narrow">
      <h1 className="page-title">New Analysis</h1>
      <p className="page-sub">
        Upload one or more files. They'll be routed to the right extraction agents (PDF, images,
        CSV, JSON, Excel — more types coming), analyzed entirely on your on-prem Qwen / Kimi2
        models, and merged into a knowledge graph you can chat with.
      </p>

      <div
        className={`dropzone ${dragging ? "dropzone-active" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => document.getElementById("file-input")?.click()}
      >
        <input
          id="file-input"
          type="file"
          multiple
          hidden
          onChange={(e) => addFiles(e.target.files)}
        />
        <div className="dropzone-icon">⇪</div>
        <div>Drag &amp; drop files here, or click to browse</div>
      </div>

      <div className="ft-supported">
        {SUPPORTED.map(([name, icon]) => (
          <span key={name} className="ft-pill">{icon} {name}</span>
        ))}
        <span className="ft-pill ft-pill-muted">+ more types routed, extraction expanding</span>
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

      <button className="btn-primary" disabled={files.length === 0 || submitting} onClick={submit}>
        {submitting ? "Starting analysis…" : `Analyze ${files.length || ""} file${files.length === 1 ? "" : "s"}`}
      </button>
    </div>
  );
}
