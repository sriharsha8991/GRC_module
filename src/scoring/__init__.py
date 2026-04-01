"""Scoring module — CVSS classification, scoring, and CVE enrichment.

Public API:
    CVSSClassifier       — LLM-based CVSS metric classifier
    compute_cvss         — vector assembly + score calculation
    CVSSResult           — API response model
    CVSSClassification   — LLM structured output schema
    enrich_cves          — Full CVE enrichment pipeline
    CveEnrichment        — CVE enrichment result model
"""

from src.scoring.classifier import CVSSClassifier
from src.scoring.cve_pipeline import enrich_cves
from src.scoring.engine import compute_cvss
from src.scoring.models import (
    CVSSClassification,
    CVSSResult,
    CveEnrichment,
)

__all__ = [
    "CVSSClassifier",
    "CVSSClassification",
    "CVSSResult",
    "CveEnrichment",
    "compute_cvss",
    "enrich_cves",
]
