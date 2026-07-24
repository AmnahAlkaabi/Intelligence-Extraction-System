"""Shared data contracts passed between pipeline tiers.

These mirror the message shapes in the architecture diagram:
TaskAssign / DomainResult / Extraction / AtomicResult, etc, collapsed into
concrete Pydantic models used directly as function I/O (no wire
serialization overhead needed since everything runs in-process / same
Python asyncio loop, but they remain JSON-serializable for the API and for
persisting job state).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid.uuid4().hex[:12]


class FileCategory(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    CSV = "csv"
    JSON_ = "json"
    EXCEL = "excel"
    OFFICE = "office"          # docx/pptx - stub
    EMAIL = "email"            # stub
    DATABASE = "database"      # stub
    CODE = "code"              # stub
    ARCHIVE = "archive"        # stub
    MEDIA = "media"            # stub
    WEB = "web"                # stub
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    GRAPH_BUILD = "graph_build"
    SYNTHESIZING = "synthesizing"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------- parsing --

class TextBlock(BaseModel):
    block_id: str = Field(default_factory=_id)
    text: str
    page: int | None = None
    kind: str = "paragraph"     # paragraph | heading | table_caption | ocr | cell
    bbox: list[float] | None = None
    confidence: float | None = None


class TableBlock(BaseModel):
    table_id: str = Field(default_factory=_id)
    page: int | None = None
    sheet: str | None = None
    headers: list[str] = []
    rows: list[list[str]] = []
    caption: str | None = None


class ParsedDocument(BaseModel):
    """Unified output of every L2 file-type agent."""
    source_file: str
    category: FileCategory
    text_blocks: list[TextBlock] = []
    tables: list[TableBlock] = []
    metadata: dict = {}
    warnings: list[str] = []

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.text_blocks if b.text.strip())


class DataQuality(BaseModel):
    """Deterministic per-file quality assessment (L3 Data Quality agent) --
    computed from parser warnings, OCR confidence, and extraction yield,
    not LLM-guessed."""
    source_file: str
    score: float               # 0-100 composite
    completeness: str          # short human-readable status
    issues: list[str] = []


# ------------------------------------------------------------- extraction --

class Entity(BaseModel):
    entity_id: str = Field(default_factory=_id)
    name: str
    type: str                 # PERSON | ORG | LOCATION | DATE | MONEY | ID | ...
    source_file: str
    mentions: list[str] = []
    confidence: float = 0.8


class Relation(BaseModel):
    relation_id: str = Field(default_factory=_id)
    source_entity: str        # entity name
    target_entity: str
    relation_type: str        # e.g. OWNS, EMPLOYED_BY, PAID, LOCATED_IN
    source_file: str
    evidence: str | None = None
    confidence: float = 0.7


class PIIFinding(BaseModel):
    finding_id: str = Field(default_factory=_id)
    category: str              # EMAIL, PHONE, IBAN, EMIRATES_ID, CREDIT_CARD, ...
    value_redacted: str
    severity: str               # low | medium | high | critical
    source_file: str
    location: str | None = None


class FinancialFact(BaseModel):
    fact_id: str = Field(default_factory=_id)
    label: str
    amount: float | None = None
    currency: str | None = None
    period: str | None = None
    source_file: str
    context: str | None = None


class Chunk(BaseModel):
    chunk_id: str = Field(default_factory=_id)
    source_file: str
    text: str
    embedding: list[float] | None = None
    page: int | None = None


class DomainResult(BaseModel):
    """What each L1 domain manager reports back to the orchestrator."""
    domain: str
    source_file: str
    entities: list[Entity] = []
    relations: list[Relation] = []
    pii_findings: list[PIIFinding] = []
    financial_facts: list[FinancialFact] = []
    chunks: list[Chunk] = []
    tables: list[TableBlock] = []
    summary: str | None = None
    errors: list[str] = []


# -------------------------------------------------------------- synthesis --

class BusinessIndex(BaseModel):
    """A metric computed deterministically by linking extraction results
    across two or more files/data types — not an LLM-guessed number.
    e.g. what share of contract value sits with one counterparty, computed
    by joining financial_facts to entities across every uploaded file.
    """
    name: str
    value: str                  # formatted result: "42%", "3 of 6 files", "0.78"
    basis: str                  # what was linked/combined to compute it
    sources: list[str] = []     # contributing file names


class BIReport(BaseModel):
    executive_summary: str
    key_entities: list[str] = []
    financial_highlights: list[str] = []
    risks: list[str] = []
    market_signals: list[str] = []
    business_use_cases: list[BusinessIndex] = []  # cross-file/cross-data indices


class ComplianceReport(BaseModel):
    pii_inventory: list[PIIFinding] = []
    severity_counts: dict[str, int] = {}
    gap_flags: list[str] = []
    remediation: list[str] = []


class KnowledgeGraphExport(BaseModel):
    entities: list[Entity] = []
    relations: list[Relation] = []


class DataDump(BaseModel):
    tables: list[TableBlock] = []
    files_processed: list[str] = []
    chunk_count: int = 0


class SynthesisOutput(BaseModel):
    bi_report: BIReport
    compliance_report: ComplianceReport
    knowledge_graph: KnowledgeGraphExport
    data_dump: DataDump


# -------------------------------------------------------------------- job --

class FileProgress(BaseModel):
    filename: str
    category: FileCategory = FileCategory.UNKNOWN
    status: JobStatus = JobStatus.QUEUED
    error: str | None = None
    warnings: list[str] = []   # e.g. "NER/PII/Financial/Relation skipped: Qwen unreachable"


class Job(BaseModel):
    job_id: str = Field(default_factory=_id)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    files: list[FileProgress] = []
    progress_pct: float = 0.0
    result: SynthesisOutput | None = None
    error: str | None = None
    # Non-fatal service connectivity issues detected at job start (e.g.
    # "Cannot reach qwen model endpoint at http://... — affects: extraction").
    # Processing still proceeds with degraded functionality rather than
    # failing outright, but the user sees immediately why extraction/
    # synthesis/chat might be limited instead of watching progress stall
    # with no explanation.
    warnings: list[str] = []


# -------------------------------------------------------------------- chat --

class ChatMessage(BaseModel):
    role: str            # user | assistant
    content: str


class Citation(BaseModel):
    source_file: str
    chunk_text: str
    page: int | None = None
    score: float | None = None


class ChatRequest(BaseModel):
    job_id: str
    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    uncertain: bool = False
