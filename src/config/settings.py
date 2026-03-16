"""Ingestion pipeline settings — all values from env vars or .env file."""

from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


class IngestionSettings(BaseSettings):
    # Infrastructure service URLs
    qdrant_url: str = "http://localhost:6333"
    embedder_url: str = "http://localhost:8081"

    # PDF storage (local now, S3 later)
    storage_backend: str = "local"  # "local" | "s3" (future)
    local_pdf_dir: Path = Path("data/pdfs")

    # S3 (future — no-ops for now)
    s3_bucket: str = ""
    s3_prefix: str = "grc-pdfs/"
    aws_region: str = "us-east-1"

    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 50

    # Embedding
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    embedding_dimension: int = 1024
    embed_batch_size: int = 32

    # Qdrant
    qdrant_distance: str = "Cosine"

    # Cleanup
    delete_pdf_after_ingestion: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "INGESTION_"


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()
