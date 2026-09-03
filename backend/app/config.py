"""Central configuration, loaded from environment variables (.env).

All model/service endpoints are configurable so the system can run fully
air-gapped against on-premise Qwen / Kimi2 deployments and a local Neo4j
instance, with no outbound internet calls at runtime.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Qwen: primary extraction / per-domain reasoning model ---
    qwen_base_url: str = "http://localhost:8001/v1"
    qwen_api_key: str = "EMPTY"
    qwen_model_name: str = "qwen2.5-72b-instruct"

    # --- Kimi2: long-context synthesis / chat model ---
    kimi_base_url: str = "http://localhost:8002/v1"
    kimi_api_key: str = "EMPTY"
    kimi_model_name: str = "kimi-k2"

    # Which logical role maps to which backend. Values: "qwen" | "kimi"
    role_extraction: str = "kimi"       # NER / PII / financial / relation agents
    role_synthesis: str = "kimi"        # final report + knowledge-graph merge
    role_chat: str = "kimi"             # interactive Q&A over the graph
    role_translation: str = "qwen"      # non-English -> English preprocessing, before extraction -- deliberately kept on Qwen, not switched with the rest of extraction

    llm_request_timeout_s: int = 180
    llm_max_retries: int = 3
    # Caps concurrent in-flight requests to EACH backend (Qwen, Kimi)
    # independently of how many files/segments the pipeline decides to run
    # in parallel -- MAX_PARALLEL_FILES files x up to 12 segments each can
    # otherwise fire dozens of simultaneous requests at one on-prem model
    # server, which just queues/times out under its own concurrency limit
    # rather than actually processing any faster. See llm/client.py.
    max_concurrent_llm_calls_per_backend: int = 4

    # --- Translation (Phase 2 preprocessing) ---
    translation_target_lang: str = "en"
    # Below this char count, language detection is unreliable -- skip
    # translation rather than risk mistranslating a short English snippet
    # that langdetect misclassifies.
    translation_min_chars: int = 40

    # --- BGE embeddings (local, no network) ---
    embedding_model_name: str = "BAAI/bge-large-en-v1.5"
    embedding_device: str = "cpu"          # "cuda" if a GPU is available
    embedding_dim: int = 1024              # bge-large = 1024, bge-base = 768, bge-m3 = 1024
    embedding_batch_size: int = 32

    # --- Docling / OCR ---
    # ocr_lang is used by the standalone Image Agent (pytesseract, for raw
    # image uploads). The PDF Agent's OCR fallback uses RapidOCR instead
    # (see parsers/pdf_parser.py) — its bundled model is not language-gated
    # the way Tesseract's is, so there's no equivalent setting for it.
    ocr_lang: str = "eng"
    docling_do_ocr: bool = True
    docling_do_table_structure: bool = True
    rapidocr_text_score: float = 0.5   # confidence threshold below which OCR'd text is dropped

    # --- Neo4j (graph store + vector index = GraphRAG backend) ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"
    neo4j_database: str = "neo4j"
    vector_index_name: str = "chunk_embeddings"

    # --- Storage ---
    upload_dir: str = "./data/uploads"
    output_dir: str = "./data/outputs"
    max_upload_mb: int = 500
    # Persistent, cross-job SQLite library every job's extracted structured
    # tables get mirrored into (see storage/dataset_library.py) -- kept
    # OUTSIDE output_dir on purpose, since output_dir/{job_id} is wiped
    # wholesale by file_store.delete_job_files when a job is deleted, and
    # a saved dataset should survive that.
    dataset_library_db_path: str = "./data/dataset_library.db"

    # --- Object storage (OBS) uploads -- optional, opt-in per deployment
    # via Settings > Storage in the frontend (see storage/obs_settings.py /
    # storage/obs_client.py). Off by default: this deployment still runs
    # fully air-gapped without ever touching these. ---
    obs_settings_db_path: str = "./data/obs_settings.db"
    # Fernet key encrypting each credential's secret key at rest -- if
    # unset (the default), one is generated on first use and persisted to
    # obs_secret_key_path so it survives a restart; a deployment that wants
    # the key managed externally (e.g. injected from a secrets manager)
    # can set OBS_ENCRYPTION_KEY directly instead.
    obs_encryption_key: str | None = None
    obs_secret_key_path: str = "./data/.obs_secret.key"
    # Network calls to a possibly-unreachable external bucket must never
    # hang an upload request -- these bound both the connection test and
    # the best-effort file mirror.
    obs_connect_timeout_s: float = 5.0
    obs_read_timeout_s: float = 15.0

    # --- Pipeline ---
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    max_parallel_files: int = 4
    max_parallel_files_global: int = 8  # cap across ALL concurrently-running jobs, not just within one
    job_retry_limit: int = 2

    # --- Importance-ranked batching (see agents/importance.py) ---
    # Jobs at or below this file count process as a single batch, exactly
    # like before batching existed -- no pause, no behavior change. Above
    # it, files are ranked by likely intelligence value and processed
    # batch_size_files at a time, pausing after each batch but the last for
    # the user to review a summary and choose whether to continue.
    batch_threshold_files: int = 30
    batch_size_files: int = 15

    # --- API ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    api_prefix: str = "/api"


@lru_cache
def get_settings() -> Settings:
    return Settings()
