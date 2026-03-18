from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    api_url: str = "http://localhost:8080"
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379"
    log_level: str = "info"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
