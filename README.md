# Intelligence Extraction System

An air-gapped, hierarchical multi-agent document intelligence pipeline. Upload
mixed-format files, get entities/PII/financial signals/relationships extracted
by specialist agents, merged into a Neo4j knowledge graph, synthesized into a
BI report + compliance report, and query the results through a GraphRAG-backed
chat interface — all running against your on-premise Qwen and Kimi2 models
with **no outbound internet calls at runtime**.

## Stack

| Layer | Choice |
|---|---|
| Extraction / reasoning model | **Qwen** (on-prem, OpenAI-compatible endpoint) |
| Synthesis / chat model | **Kimi2** (on-prem, OpenAI-compatible endpoint) |
| Embeddings | **BGE** (`BAAI/bge-large-en-v1.5`, local, `sentence-transformers`) |
| PDF parsing | **Docling** + **RapidOCR** fallback (layout-aware text/table extraction; RapidOCR is an ONNX deployment of PaddleOCR's models for scanned pages — see [PDF engine choice](#pdf-engine-choice-docling-vs-mineru-vs-paddleocr)) |
| Knowledge graph + vector index | **Neo4j** (native vector index doubles as the RAG store — no second DB) |
| Backend | FastAPI (Python, async) |
| Frontend | React + Vite, served via nginx |
| Orchestration | Custom async pipeline mirroring the L0→L3 hierarchical agent design |

## Architecture

```
Upload → File Router (MIME/ext classify)
       → L2 Parser (PDF/Docling, Image/OCR, CSV, Excel, JSON — more stubbed)
       → Chunk + Embed (BGE)              ─┐
       → NER / PII / Financial / Relation ─┼─ L2 functional agents (Qwen)
       → DomainResult per file            ─┘
       → Neo4j ingest (entities, relations, chunk vectors)
       → Synthesiser Agent (Kimi2) → BI Report, Compliance Report,
         Knowledge Graph export, Data Dump
       → GraphRAG Chat (BGE query embed → Neo4j vector search →
         1-hop graph expansion → Kimi2 answer with citations)
```

Currently wired end-to-end: **PDF, images (OCR), CSV/TSV, JSON/JSONL, Excel**.
Other file types from the original architecture (email, office docs, code,
archives, media, DB, web/XML) are classified and routed by the File Router
already, but return a stub result — see [Extending file type support](#extending-file-type-support).

## Prerequisites

- Docker + Docker Compose on both a **connected build machine** and the
  **air-gapped target host** (same OS/architecture on both, e.g. linux/amd64).
- On-prem Qwen and Kimi2 deployments reachable from the target host, serving
  an OpenAI-compatible `/v1/chat/completions` API (vLLM, TGI, Ollama, etc).
- Neo4j 5.11+ (bundled via docker-compose already — nothing to install separately).

## Configuration

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env` — at minimum set your model endpoints:

```env
QWEN_BASE_URL=http://<your-qwen-host>:8025/v1
QWEN_MODEL_NAME=Qwen3.6-35B-A3B-AWQ

KIMI_BASE_URL=http://<your-kimi-host>:8001/v1
KIMI_MODEL_NAME=unsloth/Kimi-K2.6-GGUF

NEO4J_PASSWORD=<pick-a-real-password>
```

`ROLE_EXTRACTION` / `ROLE_SYNTHESIS` / `ROLE_CHAT` map each pipeline stage to
`qwen` or `kimi` — swap them if you'd rather use Kimi2 for extraction and
Qwen for synthesis/chat.

## Building for an air-gapped environment

Images must be built where the model weights (BGE + Docling) and Python/npm
packages can be fetched, then transferred to the air-gapped host as tarballs.

**On a connected machine:**

```bash
docker compose build
docker save iex-backend:latest iex-frontend:latest neo4j:5.26-community -o iex-images.tar
```

**Transfer** `iex-images.tar` (and this repo) to the air-gapped host via your
usual approved transfer process (USB, secure file drop, etc).

**On the air-gapped host:**

```bash
docker load -i iex-images.tar
cp backend/.env.example backend/.env   # then edit with real on-prem endpoints
docker compose up -d
```

The app is served at `http://<host>:8080`. The backend API is at
`http://<host>:8000/api` (proxied through the frontend at `/api` too). Neo4j
Browser (optional, for manually inspecting the graph) is at
`http://<host>:7474`.

> **No runtime network calls**: BGE embedding weights and Docling's model
> artifacts are downloaded once *during the image build* (see
> `backend/Dockerfile`) and baked into the image layer, so the running
> container never needs to reach the internet — only your on-prem
> Qwen/Kimi2/Neo4j hosts.

## Using it

1. Open the frontend, drop in files (PDF, images, CSV, JSON, Excel — mix
   freely), click **Analyze**.
2. Watch per-file progress (Parsing → Extracting → Building Graph →
   Synthesizing → Complete).
3. Once complete, four output tabs are populated:
   - **📋 BI Report** — executive summary, key entities, financial highlights, risks, market signals
   - **🛡️ PII & Compliance** — full PII inventory by severity, gap flags, remediation actions, CSV export
   - **🕸️ Knowledge Graph** — interactive force-directed entity/relation graph
   - **📦 Data Dump** — extracted tables + full report/graph/PII export downloads
4. **💬 Chat** unlocks once the knowledge graph is built (you don't have to
   wait for the full synthesis step) — ask natural-language questions and get
   answers grounded in retrieved passages + graph facts, with citations.

## PDF engine choice: Docling vs MinerU vs PaddleOCR

All three were evaluated for the PDF Agent:

- **Docling** — best table-structure fidelity (TableFormer) and outputs a
  structured, reading-order-aware document object; fast; Apache-2.0. Best
  fit for the business/legal/financial document types this system targets
  (reports, contracts, forms).
- **MinerU** — best layout-detection accuracy and by far the best formula
  (LaTeX) recognition, but that edge mainly matters for multi-column
  scientific papers, not the primary use case here. Internally it already
  uses PaddleOCR for its own OCR step.
- **PaddleOCR** — not a full document parser on its own (no native-text
  extraction, no reading-order/table structure), but the strongest raw OCR
  recognition of the three, especially multilingual.

**Decision**: Docling stays the primary PDF parser (layout + tables +
reading order), with its OCR fallback for scanned pages switched from
Tesseract to **RapidOCR** — an ONNX-runtime deployment of PaddleOCR's
detection/recognition/classification models, same accuracy family as
PaddleOCR without the heavy PaddlePaddle framework dependency. Its default
models ship inside the `rapidocr-onnxruntime` pip wheel, so there's no
separate model-download step even on a cold start on the air-gapped host —
a meaningful plus over Tesseract/EasyOCR for this deployment model. See
`backend/app/parsers/pdf_parser.py`.

If you later process a lot of scientific/formula-heavy PDFs, MinerU is a
reasonable addition as an alternate PDF pipeline (selectable per-job) —
its output would need a translation layer into `ParsedDocument`, the same
pattern described below for any new parser.

## Extending file type support

Add a new L2 parser in `backend/app/parsers/`, implementing `BaseParser`
(see `pdf_parser.py` for the pattern), then register it in
`backend/app/parsers/router.py`'s `_get_parser()`. Everything downstream
(chunking, NER/PII/financial/relation extraction, graph ingest, synthesis,
chat) works automatically on any parser that returns a `ParsedDocument`.

## Development (non-air-gapped, for local iteration)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev

# Neo4j (or run the full docker-compose stack and point NEO4J_URI at it)
docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/changeme neo4j:5.26-community
```

## Notes on the agent hierarchy

This implementation collapses the diagram's L0–L3 message-passing agents
into direct async function calls within a single Python process (see
`backend/app/pipeline/job_manager.py` for the L0 orchestrator equivalent,
`backend/app/agents/domain_managers.py` for L1, and `backend/app/agents/
extraction.py` / `chunking.py` for L2). This keeps the system simple to
operate and debug in a single-node air-gapped deployment while preserving
the same tiered responsibilities and data contracts (`DomainResult`,
`SynthesisOutput`, etc. in `backend/app/models/schemas.py`). If you need
true distributed multi-agent execution (separate processes/containers per
agent tier with real message queues), that's a natural next step built on
top of these same module boundaries.
