"""Domain models for the retrieval pipeline.

Single-responsibility: defines data shapes that flow between pipeline stages.
No business logic — only data containers and serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field

from src.scoring.models import CVSSResult  # noqa: F401 — used in QueryResponse


# ── Internal dataclasses (used between pipeline stages) ──────────


@dataclass
class ScoredChunk:
    """A chunk returned from Qdrant vector search."""

    text: str
    metadata: dict = field(default_factory=dict)
    qdrant_score: float = 0.0

    @property
    def framework(self) -> str:
        return self.metadata.get("framework", "")

    @property
    def source_document(self) -> str:
        return self.metadata.get("source_document", "")

    @property
    def heading_breadcrumb(self) -> str:
        """Build 'h1 > h2 > … > h6' breadcrumb from metadata."""
        parts = []
        for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
            val = self.metadata.get(level, "").strip()
            if val:
                parts.append(val)
        return " > ".join(parts)

    @property
    def citation_source(self) -> str:
        """Full citation path: 'ISO/IEC 27001:2022, 6 Planning > 6.1.2 ...'"""
        doc = self.source_document
        breadcrumb = self.heading_breadcrumb
        if doc and breadcrumb:
            return f"{doc}, {breadcrumb}"
        return doc or breadcrumb


# ── Pydantic models (API request / response + Gemini structured output) ──


class MappingStatus(str, Enum):
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class ControlMapping(BaseModel):
    """A single finding-to-control mapping produced by the compliance mapper."""

    framework: str = Field(description="Framework display name")
    framework_version: str = Field(description="Framework version")
    control_id: str = Field(description="Control identifier, e.g. 'A.8.20'")
    control_title: str = Field(description="Control title")
    domain: str = Field(description="Domain / category within the framework")
    risk_mitigated: str = Field(description="What risk this control mitigates for the finding")
    citation: str = Field(description="Exact text from the source document supporting this mapping")
    citation_source: str = Field(
        description="Full source path: 'Document Name, Heading > Sub-heading'"
    )
    confidence_score: int = Field(ge=0, le=100, description="Mapping confidence 0-100")
    status: MappingStatus = Field(default=MappingStatus.APPROVED)
    critic_reason: str | None = Field(
        default=None, description="Reason if status is FAILED"
    )


class QueryRequest(BaseModel):
    """Incoming query to the retrieval pipeline."""

    finding_text: str = Field(
        min_length=10,
        description="The security finding / observation to map",
    )
    target_frameworks: list[str] = Field(
        min_length=1,
        description="Framework keys to search, e.g. ['iso_27001', 'pci_dss_v4']",
    )


class TokenUsage(BaseModel):
    """Aggregated token usage across all Gemini calls in a single query."""

    mapper_prompt_tokens: int = 0
    mapper_total_tokens: int = 0
    critic_prompt_tokens: int = 0
    critic_total_tokens: int = 0
    critic_skipped: bool = False
    cvss_prompt_tokens: int = 0
    cvss_total_tokens: int = 0
    total_tokens: int = Field(
        default=0,
        description="Sum of all tokens across mapper + critic + cvss calls",
    )


class QueryResponse(BaseModel):
    """Full response from the retrieval pipeline."""

    finding_text: str
    cvss: CVSSResult | None = Field(default=None, description="CVSS 3.1 base score assessment")
    mappings: list[ControlMapping] = Field(default_factory=list)
    frameworks_searched: list[str] = Field(default_factory=list)
    chunks_retrieved: int = 0
    duration_seconds: float = 0.0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
