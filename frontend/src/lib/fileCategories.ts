import type { FileCategory } from "../api/types";

// Mirrors backend/app/agents/domain_managers.py's _SPECIALIST_NAMES so any
// view keyed by category can show the exact glyph of the agent that
// actually handles that file type.
export const CATEGORY_AGENT: Record<FileCategory, string> = {
  pdf: "PDF Specialist",
  image: "Image/OCR Specialist",
  csv: "CSV Specialist",
  excel: "Excel Specialist",
  json: "JSON Specialist",
  office: "Office Specialist",
  email: "Email Specialist",
  database: "Database Specialist",
  code: "Code & Log Specialist",
  archive: "Archive Specialist",
  media: "Media Specialist",
  web: "Web/XML Specialist",
  unknown: "Format Specialist",
};

export const CATEGORY_LABEL: Record<FileCategory, string> = {
  pdf: "PDF", image: "Image", csv: "CSV", excel: "Excel", json: "JSON",
  office: "Office", email: "Email", database: "Database", code: "Code & Log",
  archive: "Archive", media: "Media", web: "Web/XML", unknown: "Other",
};

export const CATEGORY_ORDER: FileCategory[] = [
  "pdf", "image", "csv", "excel", "json", "office", "email",
  "database", "code", "archive", "media", "web", "unknown",
];
