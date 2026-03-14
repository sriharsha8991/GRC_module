"""Pydantic schemas for gateway responses."""

from pydantic import BaseModel
from typing import Optional


class ServiceHealth(BaseModel):
    status: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    gateway: str
    qdrant: ServiceHealth
    embedder: ServiceHealth
    reranker: ServiceHealth


class SearchResponse(BaseModel):
    collection: str
    results: list[SearchResult]


# ── Health ───────────────────────────────────────────────

class ServiceHealth(BaseModel):
    status: str  # "healthy" | "unhealthy"
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    gateway: str
    qdrant: ServiceHealth
    embedder: ServiceHealth
    reranker: ServiceHealth
