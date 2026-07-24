import { useState } from "react";
import { renameJob } from "../api/client";

export function JobTitle({ jobId, name, onRenamed }: { jobId: string; name?: string | null; onRenamed: (name: string | null) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name ?? "");
  const [saving, setSaving] = useState(false);

  if (editing) {
    const save = async () => {
      setSaving(true);
      try {
        const updated = await renameJob(jobId, draft.trim());
        onRenamed(updated.name ?? null);
        setEditing(false);
      } catch {
        // leave the input open so the user can retry
      } finally {
        setSaving(false);
      }
    };
    return (
      <div className="job-title-edit">
        <input
          className="job-title-input"
          value={draft}
          autoFocus
          placeholder={jobId}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            if (e.key === "Escape") setEditing(false);
          }}
        />
        <button className="btn-secondary" disabled={saving} onClick={save}>Save</button>
        <button className="btn-secondary" disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
      </div>
    );
  }

  return (
    <button className="job-title-display" onClick={() => { setDraft(name ?? ""); setEditing(true); }} title="Rename this job">
      <h1 className="page-title">{name || <span className="mono-id">Job {jobId}</span>}</h1>
      <span className="job-title-edit-ico">✎</span>
    </button>
  );
}
