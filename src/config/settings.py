"""Ingestion pipeline settings — all values from env vars or .env file."""

from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


class IngestionSettings(BaseSettings):
    # Infrastructure
    qdrant_url: str = "http://localhost:6333"
    reranker_url: str = "http://localhost:8082"

    # PDF storage
    storage_backend: str = "local"
    local_pdf_dir: Path = Path("data/pdfs")

    # Gemini (google-genai SDK)
    gemini_api_key: str = ""
    gemini_parse_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_window_size: int = 30000

    # Embedding
    embedding_dimension: int = 1536
    embed_batch_size: int = 32

    # Fallback embedder (TEI)
    tei_embedder_url: str = "http://localhost:8081"

    # Chunking
    chunk_size: int = 256
    chunk_overlap: int = 50

    # Qdrant
    collection_name: str = "grc_controls"
    qdrant_distance: str = "Cosine"

    # Retrieval
    use_reranker: bool = False
    reranker_backend: str = "tei"  # "tei" (self-hosted cross-encoder) or "jina" (cloud API)
    rerank_threshold: float = 0.01  # cross-encoder sigmoid scores skew low
    retrieval_limit: int = 10
    

    # Jina Reranker (cloud)
    jina_api_key: str = ""
    jina_reranker_model: str = "jina-reranker-v2-base-multilingual"

    # Cleanup
    delete_pdf_after_ingestion: bool = True

    # Redis cache
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    redis_socket_timeout: float = 1.0
    redis_max_memory_mb: int = 512
    redis_eviction_trigger_pct: int = 80
    redis_eviction_target_pct: int = 30
    redis_lock_timeout: int = 30
    redis_key_prefix: str = "grc"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "INGESTION_"


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()
