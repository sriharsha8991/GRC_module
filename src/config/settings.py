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
    rerank_threshold: float = 0.9
    retrieval_limit: int = 30

    # Cleanup
    delete_pdf_after_ingestion: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "INGESTION_"


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()
