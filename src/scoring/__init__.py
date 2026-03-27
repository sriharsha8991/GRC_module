"""CVSS 3.1 scoring module — classifies findings and computes base scores.

Public API:
    CVSSClassifier  — LLM-based CVSS metric classifier
    compute_cvss    — vector assembly + score calculation
    CVSSResult      — API response model
    CVSSClassification — LLM structured output schema
"""

from src.scoring.classifier import CVSSClassifier
from src.scoring.engine import compute_cvss
from src.scoring.models import CVSSClassification, CVSSResult

__all__ = [
    "CVSSClassifier",
    "CVSSClassification",
    "CVSSResult",
    "compute_cvss",
]
