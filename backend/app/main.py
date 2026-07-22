import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_outputs import router as outputs_router
from app.config import get_settings
from app.graph.neo4j_client import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        await get_store().ensure_schema()
        logger.info("Neo4j schema/vector index verified at startup.")
    except Exception:
        logger.exception(
            "Could not reach Neo4j at %s during startup — will retry when the first job runs.",
            settings.neo4j_uri,
        )
    yield
    await get_store().close()


app = FastAPI(
    title="Intelligence Extraction System",
    description="Air-gapped hierarchical multi-agent document intelligence pipeline "
                "(Qwen + Kimi2 + Neo4j GraphRAG + BGE embeddings).",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router, prefix=settings.api_prefix)
app.include_router(jobs_router, prefix=settings.api_prefix)
app.include_router(outputs_router, prefix=settings.api_prefix)
app.include_router(chat_router, prefix=settings.api_prefix)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
