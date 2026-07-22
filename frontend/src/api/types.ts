export type FileCategory =
  | "pdf" | "image" | "csv" | "json" | "excel" | "office" | "email"
  | "database" | "code" | "archive" | "media" | "web" | "unknown";

export type JobStatusValue =
  | "queued" | "parsing" | "extracting" | "graph_build" | "synthesizing" | "complete" | "failed";

export interface FileProgress {
  filename: string;
  category: FileCategory;
  status: JobStatusValue;
  error?: string | null;
}

export interface BIReport {
  executive_summary: string;
  key_entities: string[];
  financial_highlights: string[];
  risks: string[];
  market_signals: string[];
}

export interface PIIFinding {
  finding_id: string;
  category: string;
  value_redacted: string;
  severity: "low" | "medium" | "high" | "critical";
  source_file: string;
  location?: string | null;
}

export interface ComplianceReport {
  pii_inventory: PIIFinding[];
  severity_counts: Record<string, number>;
  gap_flags: string[];
  remediation: string[];
}

export interface Entity {
  entity_id: string;
  name: string;
  type: string;
  source_file: string;
  mentions: string[];
  confidence: number;
}

export interface Relation {
  relation_id: string;
  source_entity: string;
  target_entity: string;
  relation_type: string;
  source_file: string;
  evidence?: string | null;
  confidence: number;
}

export interface KnowledgeGraphExport {
  entities: Entity[];
  relations: Relation[];
}

export interface TableBlock {
  table_id: string;
  page?: number | null;
  sheet?: string | null;
  headers: string[];
  rows: string[][];
  caption?: string | null;
}

export interface SynthesisOutput {
  bi_report: BIReport;
  compliance_report: ComplianceReport;
  knowledge_graph: KnowledgeGraphExport;
  data_dump: { tables: TableBlock[]; files_processed: string[]; chunk_count: number };
}

export interface Job {
  job_id: string;
  status: JobStatusValue;
  created_at: string;
  updated_at: string;
  files: FileProgress[];
  progress_pct: number;
  result: SynthesisOutput | null;
  error?: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface Citation {
  source_file: string;
  chunk_text: string;
  page?: number | null;
  score?: number | null;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  uncertain: boolean;
}
