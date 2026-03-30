"""Application settings — all values loaded exclusively from .env file.

Env vars use GRC_ prefix with __ as nested delimiter.
Example: GRC_QDRANT__URL=http://... , GRC_GEMINI__API_KEY=sk-...

No hardcoded defaults — every setting MUST be defined in .env or as an
environment variable.  Missing values cause an immediate validation error.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class QdrantSettings(BaseModel):
    url: str
    collection_name: str
    distance: str


class GeminiSettings(BaseModel):
    api_key: str
    parse_model: str
    embedding_model: str
    window_size: int


class EmbeddingSettings(BaseModel):
    dimension: int
    batch_size: int


class ChunkingSettings(BaseModel):
    size: int
    overlap: int


class StorageSettings(BaseModel):
    backend: str
    local_pdf_dir: Path
    delete_pdf_after_ingestion: bool


class RetrievalSettings(BaseModel):
    limit: int
    critic_confidence_threshold: int


class RedisSettings(BaseModel):
    url: str
    enabled: bool
    socket_timeout: float
    max_memory_mb: int
    eviction_trigger_pct: int
    eviction_target_pct: int
    lock_timeout: int
    key_prefix: str


class CveSettings(BaseModel):
    """CVE identification and enrichment configuration."""

    enabled: bool = True
    nvd_api_key: str | None = None
    nvd_base_url: str = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    nvd_timeout: float = 10.0
    cve_org_base_url: str = "https://cveawg.mitre.org/api"
    osv_base_url: str = "https://api.osv.dev"
    max_cves_per_finding: int = 5
    cache_ttl_hours: int = 24
    llm_evaluation_threshold: int = 60


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRC_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    qdrant: QdrantSettings
    gemini: GeminiSettings
    embedding: EmbeddingSettings
    chunking: ChunkingSettings
    storage: StorageSettings
    retrieval: RetrievalSettings
    redis: RedisSettings
    cve: CveSettings = CveSettings()


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
