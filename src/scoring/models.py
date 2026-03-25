"""CVSS 3.1 data models — LLM schema and API response.

Single-responsibility: defines the data shapes for CVSS classification
and results.  No business logic — only data containers and validation.

Two models:
    CVSSClassification — Gemini structured output (individual metrics)
    CVSSResult         — Final API response (score + vector + metadata)
"""

from typing import Literal

from pydantic import BaseModel, Field


class CVSSClassification(BaseModel):
    """LLM-generated CVSS 3.1 base metric classification.

    Used as the Gemini structured output schema.  Each metric field
    accepts only the valid CVSS 3.1 abbreviation values, enforced
    via Literal types so Pydantic rejects invalid LLM output.
    """

    name: str = Field(description="Finding name")
    description: str = Field(description="One-sentence finding description")
    potential_impact: str = Field(description="One-sentence impact if exploited")

    # ── Exploitability metrics ──────────────────────────
    attack_vector: Literal["N", "A", "L", "P"] = Field(description="AV")
    attack_complexity: Literal["L", "H"] = Field(description="AC")
    privileges_required: Literal["N", "L", "H"] = Field(description="PR")
    user_interaction: Literal["N", "R"] = Field(description="UI")

    # ── Scope ───────────────────────────────────────────
    scope: Literal["U", "C"] = Field(description="S")

    # ── Impact metrics ──────────────────────────────────
    confidentiality_impact: Literal["N", "L", "H"] = Field(description="C")
    integrity_impact: Literal["N", "L", "H"] = Field(description="I")
    availability_impact: Literal["N", "L", "H"] = Field(description="A")

    # ── Meta ────────────────────────────────────────────
    confidence: Literal["High", "Medium", "Low"] = Field(description="Scoring confidence")
    how_to_remediate: str = Field(description="One-sentence remediation")
    metric_rationale: str = Field(
        description="One line per metric: AV:X because..., AC:X because...",
    )


class CVSSResult(BaseModel):
    """Final CVSS scoring result attached to the API response.

    Produced by the scoring engine from a CVSSClassification.
    The score and severity are computed; all other fields pass through
    from the LLM classification.
    """

    name: str = Field(description="Finding name")
    description: str = Field(description="Finding description")
    potential_impact: str = Field(description="Impact if exploited")
    severity: str = Field(description="Critical/High/Medium/Low/None")
    score: float = Field(ge=0.0, le=10.0, description="CVSS 3.1 base score")
    cvss_vector: str = Field(description="Full CVSS 3.1 vector string")
    cve: str | None = Field(default=None, description="CVE identifier")
    confidence: str = Field(description="High/Medium/Low")
    how_to_remediate: str = Field(description="Remediation guidance")
    metric_rationale: str = Field(description="One line per metric explaining the choice")
