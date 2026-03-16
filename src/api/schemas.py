"""Pydantic models for the GRC API."""

from pydantic import BaseModel


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
