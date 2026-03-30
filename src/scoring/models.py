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
    description: str = Field(description="2-3 sentence technical description of the vulnerability, how it arises, and what it exposes")
    potential_impact: str = Field(description="2-3 sentence detailed impact: what an attacker gains, affected assets, and business consequences")

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
    how_to_remediate: str = Field(description="Step-by-step remediation plan with numbered steps")
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
    description: str = Field(description="Detailed finding description")
    potential_impact: str = Field(description="Detailed impact if exploited")
    severity: str = Field(description="Critical/High/Medium/Low/None")
    score: float = Field(ge=0.0, le=10.0, description="CVSS 3.1 base score")
    cvss_vector: str = Field(description="Full CVSS 3.1 vector string")
    cve: str | None = Field(default=None, description="CVE identifier")
    confidence: str = Field(description="High/Medium/Low")
    how_to_remediate: str = Field(description="Step-by-step remediation plan")
    metric_rationale: str = Field(description="One line per metric explaining the choice")


# ── CVE Enrichment models ───────────────────────────────


class FindingClassification(BaseModel):
    """LLM-generated classification of a security finding.

    Three-class system:
      PRODUCT_VULNERABILITY — flaw in a specific software component/version,
                              CVE expected (e.g. buffer overflow in OpenSSL 1.1.1k).
      WEAK_DEFAULT          — insecure default or bad practice shipped with a product,
                              CVE may or may not exist (e.g. default admin creds in
                              vendor appliance, TLS 1.0 still enabled by product).
      PURE_MISCONFIGURATION — operational policy gap with no product-specific flaw,
                              CVE not expected (e.g. MFA not enforced, no log review).

    Used as the Gemini structured output schema.
    """

    finding_type: Literal[
        "PRODUCT_VULNERABILITY", "WEAK_DEFAULT", "PURE_MISCONFIGURATION",
    ] = Field(
        description=(
            "PRODUCT_VULNERABILITY if specific software flaw tied to a version; "
            "WEAK_DEFAULT if insecure default/bad practice shipped with a product; "
            "PURE_MISCONFIGURATION if operational policy gap with no product flaw"
        ),
    )
    reasoning: str = Field(
        description="2-3 sentence justification for the classification",
    )
    software_component: str | None = Field(
        default=None,
        description="Software name, e.g. 'Next.js', 'OpenSSL', 'Apache Tomcat'",
    )
    vendor: str | None = Field(
        default=None,
        description="Vendor/publisher, e.g. 'vercel', 'openssl', 'apache'",
    )
    version: str | None = Field(
        default=None,
        description="Specific version, e.g. '13.0.0', '1.1.1k'",
    )
    version_range: str | None = Field(
        default=None,
        description="Version range if mentioned, e.g. '>= 13.4.0, < 14.1.1'",
    )
    ecosystem: str | None = Field(
        default=None,
        description="Package ecosystem: 'npm', 'PyPI', 'Maven', 'Go', 'OS'",
    )
    named_vulnerability: str | None = Field(
        default=None,
        description="Named vulnerability if mentioned: 'Log4Shell', 'POODLE'",
    )
    explicit_cve_ids: list[str] = Field(
        default_factory=list,
        description="CVE IDs found verbatim in the finding text",
    )
    cpe_vendor: str | None = Field(
        default=None,
        description="CPE-normalized vendor, e.g. 'vercel', 'apache', 'openssl'",
    )
    cpe_product: str | None = Field(
        default=None,
        description="CPE-normalized product name, e.g. 'next.js', 'tomcat', 'openssl'",
    )


class CveSearchResult(BaseModel):
    """A CVE candidate returned from search."""

    cve_id: str = Field(description="CVE identifier, e.g. 'CVE-2024-34351'")
    source: str = Field(
        description="Search source: EXPLICIT | NVD_CPE | NVD_KEYWORD | OSV",
    )
    description: str = Field(default="", description="CVE summary from source")
    affected_product: str | None = Field(
        default=None, description="Affected product name from CVE record",
    )
    affected_versions: str | None = Field(
        default=None, description="Affected version range string",
    )


class CveEvaluation(BaseModel):
    """LLM evaluation of a single CVE's relevance to the finding."""

    cve_id: str = Field(description="CVE being evaluated")
    is_relevant: bool = Field(description="True if CVE matches the finding")
    relevance_score: int = Field(
        ge=0, le=100, description="Confidence that CVE is correct match",
    )
    reasoning: str = Field(description="One-line justification")


class CveEvaluationResult(BaseModel):
    """Aggregated result from LLM evaluation of all candidates."""

    evaluations: list[CveEvaluation] = Field(default_factory=list)
    final_cve_ids: list[str] = Field(
        default_factory=list,
        description="CVE IDs that passed evaluation (is_relevant=True, score >= threshold)",
    )


class CveDetail(BaseModel):
    """Full enriched CVE record from NVD or cve.org."""

    cve_id: str = Field(description="CVE identifier")
    description: str = Field(description="Vulnerability description")
    cvss_vector: str | None = Field(default=None, description="CVSS 3.1 vector string")
    cvss_score: float | None = Field(default=None, ge=0.0, le=10.0)
    cvss_severity: str | None = Field(
        default=None, description="NONE | LOW | MEDIUM | HIGH | CRITICAL",
    )
    cvss_source: str | None = Field(
        default=None, description="Who provided CVSS: NVD | CNA | CISA-ADP",
    )
    cwe_id: str | None = Field(default=None, description="CWE identifier, e.g. 'CWE-918'")
    references: list[str] = Field(
        default_factory=list, description="Up to 5 reference URLs",
    )
    published: str | None = Field(default=None, description="ISO 8601 publication date")
    source: str = Field(description="Data fetch source: NVD | CVE_ORG")
    kev: bool = Field(default=False, description="In CISA Known Exploited Vulnerabilities catalog")
    ssvc_exploitation: str | None = Field(
        default=None, description="SSVC exploitation status: none | poc | active",
    )
    ssvc_automatable: str | None = Field(
        default=None, description="SSVC automatable: yes | no",
    )
    ssvc_technical_impact: str | None = Field(
        default=None, description="SSVC technical impact: partial | total",
    )


class CveEnrichment(BaseModel):
    """Complete CVE enrichment result for a finding."""

    finding_type: str = Field(
        description="PRODUCT_VULNERABILITY | WEAK_DEFAULT | PURE_MISCONFIGURATION",
    )
    classification_reasoning: str = Field(
        default="", description="Why the finding was classified this way",
    )
    software_component: str | None = Field(default=None)
    vendor: str | None = Field(default=None)
    version: str | None = Field(default=None)
    cve_ids: list[str] | None = Field(
        default=None,
        description="Matched CVE IDs — null for pure misconfigurations",
    )
    cve_details: list[CveDetail] = Field(default_factory=list)
    evaluation_summary: list[CveEvaluation] = Field(default_factory=list)
    search_sources: list[str] = Field(
        default_factory=list,
        description="Which sources were queried",
    )
    enrichment_duration_seconds: float = Field(default=0.0)
