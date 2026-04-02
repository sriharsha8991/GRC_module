"""Pydantic models for the GRC API.

Re-exports retrieval models for the query endpoint so the API layer
has a single schema source.  Ingestion-specific schemas live here.
"""

from pydantic import BaseModel, Field

# Re-export retrieval models for use in route type hints
from src.retrieval.models import (  # noqa: F401
    ControlMapping,
    QueryRequest,
    TokenUsage,
)
from src.scoring.models import CVSSResult  # noqa: F401


# ── Flat CVE response models ────────────────────────────


class CveDetailResponse(BaseModel):
    """Flat, essential CVE record for the API response."""

    cve_id: str
    description: str
    cwe_id: str | None = None
    published: str | None = None
    kev: bool = False


class CveEnrichmentResponse(BaseModel):
    """Simplified CVE enrichment — single-layer, essential fields only."""

    finding_type: str = Field(
        description="PRODUCT_VULNERABILITY | WEAK_DEFAULT | PURE_MISCONFIGURATION",
    )
    software_component: str | None = None
    vendor: str | None = None
    version: str | None = None
    cve_ids: list[str] | None = None
    cve_details: list[CveDetailResponse] = Field(default_factory=list)


class QueryResponseAPI(BaseModel):
    """API response — mirrors internal QueryResponse but with simplified CVE data."""

    finding_text: str
    cvss: CVSSResult | None = None
    cve_enrichment: CveEnrichmentResponse | None = None
    mappings: list[ControlMapping] = Field(default_factory=list)
    frameworks_searched: list[str] = Field(default_factory=list)
    chunks_retrieved: int = 0
    duration_seconds: float = 0.0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


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
