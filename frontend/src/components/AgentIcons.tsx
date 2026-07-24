import type { ReactNode } from "react";

// One glyph per pipeline agent so the live agent board reads at a glance
// instead of as a wall of identical rows. Engine determines the accent
// color applied via CSS (see .agent-node[data-engine] in styles.css) --
// kept here as the single source of truth so the board and any future
// legend stay in sync with how job_manager/domain_managers actually name
// and dispatch each agent.
export type Engine = "qwen" | "kimi" | "localml" | "rules";

const ICON_PROPS = {
  viewBox: "0 0 24 24",
  width: 18,
  height: 18,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const ICONS: Record<string, { engine: Engine; path: ReactNode }> = {
  "PDF Specialist": {
    engine: "localml",
    path: <><rect x="6" y="3" width="12" height="18" rx="1.4" /><path d="M9 8h6M9 12h6M9 16h4" /></>,
  },
  "Image/OCR Specialist": {
    engine: "localml",
    path: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.6" /></>,
  },
  "CSV Specialist": {
    engine: "rules",
    path: <><rect x="3.5" y="3.5" width="7" height="7" rx="1" /><rect x="13.5" y="3.5" width="7" height="7" rx="1" /><rect x="3.5" y="13.5" width="7" height="7" rx="1" /><rect x="13.5" y="13.5" width="7" height="7" rx="1" /></>,
  },
  "Excel Specialist": {
    engine: "rules",
    path: <><rect x="3.5" y="3.5" width="7" height="7" rx="1" /><rect x="13.5" y="3.5" width="7" height="7" rx="1" /><rect x="3.5" y="13.5" width="7" height="7" rx="1" /><rect x="13.5" y="13.5" width="7" height="7" rx="1" /></>,
  },
  "JSON Specialist": {
    engine: "rules",
    path: <path d="M9 4c-2 0-2.5 1-2.5 2.6V9c0 1.4-.6 2-1.8 2 1.2 0 1.8.6 1.8 2v2.4C6.5 17 7 18 9 18M15 4c2 0 2.5 1 2.5 2.6V9c0 1.4.6 2 1.8 2-1.2 0-1.8.6-1.8 2v2.4c0 1.6-.5 2.6-2.5 2.6" />,
  },
  "Office Specialist": {
    engine: "rules",
    path: <><path d="M7 3h7l4 4v13.6a.4.4 0 0 1-.4.4H7.4A.4.4 0 0 1 7 20.6V3.4A.4.4 0 0 1 7.4 3Z" /><path d="M14 3v4h4" /><path d="M9.5 12h5M9.5 15.5h5" /></>,
  },
  "Email Specialist": {
    engine: "rules",
    path: <><rect x="3" y="5" width="18" height="14" rx="1.6" /><path d="M3.5 6 12 13l8.5-7" /></>,
  },
  "Database Specialist": {
    engine: "rules",
    path: <><ellipse cx="12" cy="5.5" rx="7" ry="2.6" /><path d="M5 5.5v13c0 1.44 3.13 2.6 7 2.6s7-1.16 7-2.6v-13" /><path d="M5 12c0 1.44 3.13 2.6 7 2.6s7-1.16 7-2.6" /></>,
  },
  "Code & Log Specialist": {
    engine: "rules",
    path: <path d="M8 6 3 12l5 6M16 6l5 6-5 6" />,
  },
  "Archive Specialist": {
    engine: "rules",
    path: <><rect x="4" y="7" width="16" height="14" rx="1.4" /><path d="M4 11h16M11 7v3M11 14h2" /><path d="M9 3h6l1.5 4h-9Z" /></>,
  },
  "Media Specialist": {
    engine: "rules",
    path: <path d="M2.5 12h2.5l2-6 3 12 3-15 3 12 2-6h3.5" />,
  },
  "Web/XML Specialist": {
    engine: "rules",
    path: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.6 2.3 4 5.2 4 8.5s-1.4 6.2-4 8.5c-2.6-2.3-4-5.2-4-8.5s1.4-6.2 4-8.5Z" /></>,
  },
  "Format Specialist": {
    engine: "rules",
    path: <><path d="M7 3h7l4 4v13.6a.4.4 0 0 1-.4.4H7.4A.4.4 0 0 1 7 20.6V3.4A.4.4 0 0 1 7.4 3Z" /><path d="M14 3v4h4" /></>,
  },
  "Translator": {
    engine: "qwen",
    path: <><path d="M4 5.5h9M8.3 3v2.5M6 5.5c.4 3.4 2.6 6 5.6 7.4M10.6 5.5c-.8 3.8-3.6 6.9-7.2 8.4" /><path d="M14 21l4-9 4 9M15.4 18h5.2" /></>,
  },
  "Chunk/Embed Extractor": {
    engine: "localml",
    path: <><path d="M12 3.5 3.5 8 12 12.5 20.5 8Z" /><path d="M3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16" /></>,
  },
  "Entity Extractor": {
    engine: "qwen",
    path: <><circle cx="9" cy="9" r="4.5" /><path d="M13.5 5.5 20 12l-8 8-6.5-6.5" /></>,
  },
  "PII Extractor": {
    engine: "qwen",
    path: <><path d="M12 3 4.5 6v6c0 5 3.2 8 7.5 9 4.3-1 7.5-4 7.5-9V6Z" /><path d="M8.7 12.2l2.2 2.2 4.4-4.6" /></>,
  },
  "Financial Extractor": {
    engine: "qwen",
    path: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v10M9.3 9.3c0-1.3 1.2-2 2.7-2s2.7.8 2.7 2c0 3-5.4 1.7-5.4 4.6 0 1.3 1.2 2.1 2.7 2.1s2.7-.8 2.7-2.1" /></>,
  },
  "Relation Extractor": {
    engine: "qwen",
    path: <><path d="M9.5 14.5 14.5 9.5" /><path d="M7 17 4.8 14.8a4 4 0 0 1 0-5.6l2-2a4 4 0 0 1 5.6 0M17 7l2.2 2.2a4 4 0 0 1 0 5.6l-2 2a4 4 0 0 1-5.6 0" /></>,
  },
  "Data Quality Validator": {
    engine: "rules",
    path: <><path d="M12 3 4.5 6v6c0 5 3.2 8 7.5 9 4.3-1 7.5-4 7.5-9V6Z" /><path d="M9 12.3l2 2 4-4.3" /></>,
  },
  "BI Synthesizer": {
    engine: "kimi",
    path: <><path d="M4 20V10M11 20V4M18 20v-7" /><path d="M2.5 20.5h19" /></>,
  },
};

const FALLBACK: { engine: Engine; path: ReactNode } = {
  engine: "rules",
  path: <circle cx="12" cy="12" r="8" />,
};

export function agentEngine(agent: string): Engine {
  return (ICONS[agent] ?? FALLBACK).engine;
}

export function AgentIcon({ agent }: { agent: string }) {
  const def = ICONS[agent] ?? FALLBACK;
  return <svg {...ICON_PROPS}>{def.path}</svg>;
}
