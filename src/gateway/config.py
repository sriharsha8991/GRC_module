from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    qdrant_url: str = "http://localhost:6333"
    embedder_url: str = "http://localhost:8081"
    reranker_url: str = "http://localhost:8082"
    log_level: str = "info"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
