import type { ChatMessage, ChatResponse, Job, TableBlock } from "./types";

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

export async function getDataDumpTables(jobId: string): Promise<TableBlock[]> {
  return req<TableBlock[]>(`/outputs/${jobId}/data-dump/tables`);
}

export function artifactUrl(jobId: string, artifact: string): string {
  return `${BASE}/outputs/${jobId}/files/${artifact}`;
}

export function tableDownloadUrl(jobId: string, tableFilename: string): string {
  return `${BASE}/outputs/${jobId}/files/tables/${tableFilename}`;
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
