"""Scoring module — CVSS classification, scoring, and CVE enrichment.

Public API:
    CVSSClassifier       — LLM-based CVSS metric classifier
    compute_cvss         — vector assembly + score calculation
    CVSSResult           — API response model
    CVSSClassification   — LLM structured output schema
    FindingClassifier    — Vulnerability vs Misconfiguration classifier
    enrich_cves          — Full CVE enrichment pipeline
    CveEnrichment        — CVE enrichment result model
    FindingClassification — Finding classification schema
"""

from src.scoring.classifier import CVSSClassifier
from src.scoring.cve_pipeline import enrich_cves
from src.scoring.engine import compute_cvss
from src.scoring.finding_classifier import FindingClassifier
from src.scoring.models import (
    CVSSClassification,
    CVSSResult,
    CveEnrichment,
    FindingClassification,
)

__all__ = [
    "CVSSClassifier",
    "CVSSClassification",
    "CVSSResult",
    "CveEnrichment",
    "FindingClassification",
    "FindingClassifier",
    "compute_cvss",
    "enrich_cves",
]
