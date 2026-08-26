# Agent Improvement Roadmap

## Baseline

All 22 agents below are already shipped and running end-to-end in production
today — file upload → parse → extract → graph → synthesize → chat. Nothing
in this roadmap is initial development; every item is an enhancement to an
agent that already works.

## Methodology

- **Every agent gets its own timeline slot**, sized by its actual technical
  complexity rather than grouped into uniform weekly buckets.
- **Scope is capability improvements only.** This roadmap deliberately
  excludes industry/use-case specialization (e.g. tailoring to a specific
  vertical) and evaluation/test-harness work — both are separate efforts,
  not agent capability work.
- **Complexity tiers:**

| Tier | Days | Definition |
|---|---|---|
| S — Simple | 2 | Single well-defined format, mostly deterministic parsing logic |
| M — Moderate | 3 | Real structural complexity, or light ML/LLM involvement |
| C — Complex | 4 | Heavy LLM dependency or nontrivial ML model tuning |
| H — High / cross-cutting | 5 | Orchestration-level, cross-document synthesis, or graph/retrieval reasoning spanning the system |

## Per-agent schedule

Serial (single-track) schedule, in build order:

| # | Agent | File | Tier | Days | Week(s) |
|---|---|---|---|---|---|
| 1 | Orchestrator | `backend/app/pipeline/job_manager.py` | H | 5 | 1 |
| 2 | PDF Specialist | `backend/app/parsers/pdf_parser.py` | C | 4 | 2 |
| 3 | Image/OCR Specialist | `backend/app/parsers/image_parser.py` | C | 4 | 2-3 |
| 4 | CSV Specialist | `backend/app/parsers/csv_parser.py` | S | 2 | 3 |
| 5 | Excel Specialist | `backend/app/parsers/excel_parser.py` | S | 2 | 4 |
| 6 | JSON Specialist | `backend/app/parsers/json_parser.py` | S | 2 | 4 |
| 7 | Office Specialist | `backend/app/parsers/office_parser.py` | M | 3 | 4-5 |
| 8 | Database Specialist | `backend/app/parsers/database_parser.py` | M | 3 | 5 |
| 9 | Code & Log Specialist | `backend/app/parsers/code_parser.py` | M | 3 | 6 |
| 10 | Media Specialist | `backend/app/parsers/media_parser.py` | C | 4 | 6-7 |
| 11 | Web/XML Specialist | `backend/app/parsers/web_parser.py` | S | 2 | 7 |
| 12 | Translator | `backend/app/agents/translation.py` | M | 3 | 7-8 |
| 13 | Chunk/Embed Extractor | `backend/app/agents/chunking.py` | C | 4 | 8-9 |
| 14 | Entity Extractor | `backend/app/agents/extraction.py` (`run_ner`) | C | 4 | 9 |
| 15 | PII Extractor | `backend/app/agents/extraction.py` (`run_pii`) | C | 4 | 10 |
| 16 | Financial Extractor | `backend/app/agents/extraction.py` (`run_financial`) | C | 4 | 10-11 |
| 17 | Relation Extractor | `backend/app/agents/extraction.py` (`run_relations`) | C | 4 | 11-12 |
| 18 | Data Quality Validator | `backend/app/agents/data_quality.py` | M | 3 | 12 |
| 19 | BI Synthesizer | `backend/app/agents/synthesizer.py` | H | 5 | 13 |
| 20 | Mapping Agent | `backend/app/agents/mapping_agent.py` | C | 4 | 14 |
| 21 | Insight Agent | `backend/app/agents/mapping_agent.py` (merged) | S | 2 | 14-15 |
| 22 | GraphRAG Chat Synthesizer | `backend/app/graph/graphrag.py` | H | 5 | 15-16 |

**Total: ~76 working days ≈ 15.2 weeks, single track.**

With two parallel tracks — e.g. Track A: format specialists (#1-11), Track B:
extraction/synthesis/chat (#12-22), largely independent until final
integration — this compresses to roughly **8 weeks**.

## Improvement scope per agent

**1. Orchestrator** — Complexity-aware batch prioritization beyond the
current keyword-based importance scorer; per-agent SLA/timeout
configuration instead of one-size-fits-all; structured execution tracing
across agent hops.

**2. PDF Specialist** — Encrypted/password-protected PDF handling;
multi-column and rotated-page layout detection; improved table-structure
extraction on complex nested tables.

**3. Image/OCR Specialist** — Image pre-processing (deskew, contrast
normalization, upscaling) before OCR; additional OCR language packs;
per-region confidence calibration improvements.

**4. CSV Specialist** — Delimiter/encoding auto-detection beyond
UTF-8/comma; malformed-row recovery instead of hard failure.

**5. Excel Specialist** — Merged-cell and multi-header table
reconstruction; formula-cell value resolution.

**6. JSON Specialist** — Streaming parse for very large top-level-array
files (via `ijson`) and a recursion-depth guard on the flattener shipped;
remaining scope: irregular/inconsistent per-record schema reconciliation
across a corpus of files (not just within one file's own key-frequency
summary).

**7. Office Specialist** — Embedded table/image extraction from DOCX (not
just running text); PPTX speaker-notes extraction.

**8. Database Specialist** — Additional DB dump formats beyond SQLite
(Postgres/MySQL dumps); foreign-key relationship extraction from schema.

**9. Code & Log Specialist** — Structured log format parsers (JSON logs,
syslog) beyond plain-text regex; broader language coverage for
function/class detection.

**10. Media Specialist** — Optional speech-to-text pipeline for audio/video
(currently metadata-only); waveform-based quality/confidence signal.

**11. Web/XML Specialist** — Sitemap/RSS/Atom feed parsing; more robust
malformed-HTML recovery.

**12. Translator** — Per-section (not just per-file) language detection for
mixed-language documents; translation quality confidence scoring.

**13. Chunk/Embed Extractor** — Adaptive chunk sizing by content density;
hybrid keyword+vector retrieval scoring.

**14. Entity Extractor** — Coreference resolution across mentions;
extensible entity-type taxonomy.

**15. PII Extractor** — Extensible/configurable PII taxonomy (add
categories without code changes); reduced false-positive rate on
structured identifiers.

**16. Financial Extractor** — Multi-currency normalization; statistically
grounded anomaly detection (distribution-based, not just
duplicate/round-trip heuristics).

**17. Relation Extractor** — Temporal and ownership-chain relation types;
relation confidence scoring for downstream graph weighting.

**18. Data Quality Validator** — Additional quality dimensions
(completeness, cross-file consistency) beyond current
parser-warning/OCR-confidence signals; configurable scoring thresholds.

**19. BI Synthesizer** — Configurable report structure/templating;
multi-job comparison/diff mode.

**20. Mapping Agent** — Fuzzy/approximate join detection beyond exact
row-overlap matching; join-quality confidence scoring.

**21. Insight Agent** — Additional cross-data statistics beyond current
matched/orphan counts (e.g. join-coverage trends over time).

**22. GraphRAG Chat Synthesizer** — Multi-hop graph traversal beyond the
current 1-hop expansion; conversation memory across follow-up questions;
per-citation confidence scoring.
