import type { ChatMessage, ChatResponse, DataDumpTable, Job, SourceDocSummary } from "./types";

const BASE = "/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export async function uploadFiles(files: File[]): Promise<Job> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  return req<Job>("/ingest", { method: "POST", body: form });
}

export async function getJob(jobId: string): Promise<Job> {
  return req<Job>(`/jobs/${jobId}`);
}

export async function listJobs(): Promise<Job[]> {
  return req<Job[]>("/jobs");
}

export async function renameJob(jobId: string, name: string): Promise<Job> {
  return req<Job>(`/jobs/${jobId}/name`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function continueBatch(jobId: string): Promise<Job> {
  return req<Job>(`/jobs/${jobId}/batches/continue`, { method: "POST" });
}

export async function stopBatches(jobId: string): Promise<Job> {
  return req<Job>(`/jobs/${jobId}/batches/stop`, { method: "POST" });
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(`${BASE}/jobs/${jobId}`, { method: "DELETE" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore -- e.g. a 204 has no body to parse
    }
    throw new Error(`${res.status}: ${detail}`);
  }
}

export async function getDataDumpTables(jobId: string): Promise<DataDumpTable[]> {
  return req<DataDumpTable[]>(`/outputs/${jobId}/data-dump/tables`);
}

export async function getDataDumpDocuments(jobId: string): Promise<SourceDocSummary[]> {
  return req<SourceDocSummary[]>(`/outputs/${jobId}/data-dump/documents`);
}

export function artifactUrl(jobId: string, artifact: string): string {
  return `${BASE}/outputs/${jobId}/files/${artifact}`;
}

export function tableDownloadUrl(jobId: string, tableFilename: string): string {
  return `${BASE}/outputs/${jobId}/files/tables/${tableFilename}`;
}

export function sourcePreviewUrl(jobId: string, filename: string): string {
  return `${BASE}/outputs/${jobId}/files/preview/${encodeURIComponent(filename)}`;
}

export async function sendChatMessage(
  jobId: string,
  message: string,
  history: ChatMessage[]
): Promise<ChatResponse> {
  return req<ChatResponse>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, message, history }),
  });
}
