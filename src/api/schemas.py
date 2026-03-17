"""Pydantic models for the GRC API.

Re-exports retrieval models for the query endpoint so the API layer
has a single schema source.  Ingestion-specific schemas live here.
"""

from pydantic import BaseModel

# Re-export retrieval models for use in route type hints
from src.retrieval.models import (  # noqa: F401
    ControlMapping,
    QueryRequest,
    QueryResponse,
)


class IngestionResponse(BaseModel):
    framework_key: str
    collection_name: str
    chunks_created: int
    points_upserted: int
    duration_seconds: float
    success: bool
    error: str | None = None


class FrameworkInfo(BaseModel):
    key: str
    display_name: str
    version: str
    description: str
