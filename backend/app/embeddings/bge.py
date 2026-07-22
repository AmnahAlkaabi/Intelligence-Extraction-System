"""Local BGE embedding service.

Runs entirely offline: the model weights are downloaded once (during image
build, or manually onto the air-gapped host) and cached under
sentence-transformers' local cache dir. No network call happens at
inference time.

BGE models expect an instruction prefix on the *query* side for
retrieval-style similarity (this measurably improves recall); passage/chunk
text is embedded as-is.
"""
import asyncio
import logging
import threading

from sentence_transformers import SentenceTransformer

from app.config import get_settings

logger = logging.getLogger(__name__)

QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class BGEEmbedder:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._lock = threading.Lock()
        logger.info("Loading BGE embedding model %s on %s ...",
                    settings.embedding_model_name, settings.embedding_device)
        self._model = SentenceTransformer(
            settings.embedding_model_name,
            device=settings.embedding_device,
        )
        logger.info("BGE embedding model loaded.")

    def _encode_sync(self, texts: list[str], is_query: bool) -> list[list[float]]:
        if is_query:
            texts = [QUERY_INSTRUCTION + t for t in texts]
        with self._lock:
            vectors = self._model.encode(
                texts,
                batch_size=self._settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return vectors.tolist()

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode_sync, texts, False)

    async def embed_query(self, text: str) -> list[float]:
        vecs = await asyncio.to_thread(self._encode_sync, [text], True)
        return vecs[0]


_embedder_singleton: BGEEmbedder | None = None


def get_embedder() -> BGEEmbedder:
    global _embedder_singleton
    if _embedder_singleton is None:
        _embedder_singleton = BGEEmbedder()
    return _embedder_singleton
