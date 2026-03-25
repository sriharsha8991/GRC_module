"""CVSS 3.1 scoring engine — vector assembly and score computation.

Single-responsibility: takes an LLM-generated CVSSClassification,
builds the CVSS 3.1 vector string, computes the base score using
the `cvss` library, and returns a CVSSResult.

Does NOT call any LLM — that is the classifier's job.
"""

import logging

from cvss import CVSS3

from src.scoring.models import CVSSClassification, CVSSResult

logger = logging.getLogger("scoring.engine")

# CVSS 3.1 severity thresholds (FIRST spec §5)
_SEVERITY_THRESHOLDS: list[tuple[float, str]] = [
    (9.0, "Critical"),
    (7.0, "High"),
    (4.0, "Medium"),
    (0.1, "Low"),
]


def _derive_severity(score: float) -> str:
    """Map a CVSS 3.1 base score to its qualitative severity rating."""
    for threshold, label in _SEVERITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "None"


def _build_vector(classification: CVSSClassification) -> str:
    """Assemble the CVSS 3.1 vector string from individual metrics."""
    return (
        f"CVSS:3.1"
        f"/AV:{classification.attack_vector}"
        f"/AC:{classification.attack_complexity}"
        f"/PR:{classification.privileges_required}"
        f"/UI:{classification.user_interaction}"
        f"/S:{classification.scope}"
        f"/C:{classification.confidentiality_impact}"
        f"/I:{classification.integrity_impact}"
        f"/A:{classification.availability_impact}"
    )


def compute_cvss(classification: CVSSClassification) -> CVSSResult:
    """Compute CVSS 3.1 base score from an LLM classification.

    Args:
        classification: The LLM-generated metric classification.

    Returns:
        CVSSResult with computed score, vector, and severity.

    Raises:
        CVSSError: If the assembled vector is malformed (should not
                   happen with Literal-validated inputs).
    """
    vector = _build_vector(classification)

    cvss_obj = CVSS3(vector)
    score = cvss_obj.base_score

    severity = _derive_severity(score)

    logger.info(
        "CVSS computed: %s → score=%.1f severity=%s",
        vector, score, severity,
    )

    return CVSSResult(
        name=classification.name,
        description=classification.description,
        potential_impact=classification.potential_impact,
        severity=severity,
        score=score,
        cvss_vector=vector,
        cve=None,
        confidence=classification.confidence,
        how_to_remediate=classification.how_to_remediate,
        metric_rationale=classification.metric_rationale,
    )
