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
    role_extraction: str = "qwen"       # NER / PII / financial / relation agents
    role_synthesis: str = "kimi"        # final report + knowledge-graph merge
    role_chat: str = "kimi"             # interactive Q&A over the graph

    llm_request_timeout_s: int = 180
    llm_max_retries: int = 3

    # --- BGE embeddings (local, no network) ---
    embedding_model_name: str = "BAAI/bge-large-en-v1.5"
    embedding_device: str = "cpu"          # "cuda" if a GPU is available
    embedding_dim: int = 1024              # bge-large = 1024, bge-base = 768, bge-m3 = 1024
    embedding_batch_size: int = 32

    # --- Docling / OCR ---
    ocr_lang: str = "eng"
    docling_do_ocr: bool = True
    docling_do_table_structure: bool = True

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

    # --- Pipeline ---
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    max_parallel_files: int = 4
    job_retry_limit: int = 2

    # --- API ---
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    api_prefix: str = "/api"


@lru_cache
def get_settings() -> Settings:
    return Settings()
