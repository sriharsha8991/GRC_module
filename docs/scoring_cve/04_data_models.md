# CVE ID Management — Data Models

All models follow the same pattern as the existing `src/scoring/models.py`:
Pydantic `BaseModel` with `Field` descriptions for OpenAPI generation and
`Literal` types for LLM structured output validation.

---

## 1. New Models in `src/scoring/models.py`

### FindingClassification — LLM Structured Output

```python
class FindingClassification(BaseModel):
    """LLM-generated classification of a security finding.

    Determines whether the finding is a software vulnerability
    (component+version specific) or a misconfiguration (policy/control gap).
    Used as the Gemini structured output schema.
    """

    finding_type: Literal["VULNERABILITY", "MISCONFIGURATION"] = Field(
        description="VULNERABILITY if specific software flaw; MISCONFIGURATION if policy/config gap"
    )
    reasoning: str = Field(
        description="2-3 sentence justification for the classification"
    )

    # Component metadata (populated only for VULNERABILITY)
    software_component: str | None = Field(
        default=None,
        description="Software name, e.g. 'Next.js', 'OpenSSL', 'Apache Tomcat'"
    )
    vendor: str | None = Field(
        default=None,
        description="Vendor/publisher, e.g. 'vercel', 'openssl', 'apache'"
    )
    version: str | None = Field(
        default=None,
        description="Specific version, e.g. '13.0.0', '1.1.1k'"
    )
    version_range: str | None = Field(
        default=None,
        description="Version range if mentioned, e.g. '>= 13.4.0, < 14.1.1'"
    )
    ecosystem: str | None = Field(
        default=None,
        description="Package ecosystem: 'npm', 'PyPI', 'Maven', 'Go', 'OS'"
    )
    named_vulnerability: str | None = Field(
        default=None,
        description="Named vulnerability if mentioned: 'Log4Shell', 'POODLE'"
    )
    explicit_cve_ids: list[str] = Field(
        default_factory=list,
        description="CVE IDs found verbatim in the finding text"
    )
```

### CveSearchResult — Search Output

```python
class CveSearchResult(BaseModel):
    """A CVE candidate returned from search."""

    cve_id: str = Field(description="CVE identifier, e.g. 'CVE-2024-34351'")
    source: str = Field(
        description="Search source: EXPLICIT | NVD_CPE | NVD_KEYWORD | OSV"
    )
    description: str = Field(default="", description="CVE summary from source")
    affected_product: str | None = Field(
        default=None, description="Affected product name from CVE record"
    )
    affected_versions: str | None = Field(
        default=None, description="Affected version range string"
    )
```

### CveEvaluation — LLM Judge Output

```python
class CveEvaluation(BaseModel):
    """LLM evaluation of a single CVE's relevance to the finding."""

    cve_id: str = Field(description="CVE being evaluated")
    is_relevant: bool = Field(description="True if CVE matches the finding")
    relevance_score: int = Field(
        ge=0, le=100, description="Confidence that CVE is correct match"
    )
    reasoning: str = Field(description="One-line justification")


class CveEvaluationResult(BaseModel):
    """Aggregated result from LLM evaluation of all candidates."""

    evaluations: list[CveEvaluation] = Field(default_factory=list)
    final_cve_ids: list[str] = Field(
        default_factory=list,
        description="CVE IDs that passed evaluation (is_relevant=True, score >= threshold)"
    )
```

### CveDetail — Enriched CVE Record

```python
class CveDetail(BaseModel):
    """Full enriched CVE record from NVD or cve.org."""

    cve_id: str = Field(description="CVE identifier")
    description: str = Field(description="Vulnerability description")
    cvss_vector: str | None = Field(default=None, description="CVSS 3.1 vector string")
    cvss_score: float | None = Field(default=None, ge=0.0, le=10.0)
    cvss_severity: str | None = Field(
        default=None, description="NONE | LOW | MEDIUM | HIGH | CRITICAL"
    )
    cvss_source: str | None = Field(
        default=None, description="Who provided CVSS: NVD | CNA | CISA-ADP"
    )
    cwe_id: str | None = Field(default=None, description="CWE identifier, e.g. 'CWE-918'")
    references: list[str] = Field(
        default_factory=list, description="Up to 5 reference URLs"
    )
    published: str | None = Field(default=None, description="ISO 8601 publication date")
    source: str = Field(description="Data fetch source: NVD | CVE_ORG")

    # CISA enrichment (from cve.org ADP container)
    kev: bool = Field(default=False, description="In CISA Known Exploited Vulnerabilities catalog")
    ssvc_exploitation: str | None = Field(
        default=None, description="SSVC exploitation status: none | poc | active"
    )
    ssvc_automatable: str | None = Field(
        default=None, description="SSVC automatable: yes | no"
    )
    ssvc_technical_impact: str | None = Field(
        default=None, description="SSVC technical impact: partial | total"
    )
```

### CveEnrichment — Top-Level Result

```python
class CveEnrichment(BaseModel):
    """Complete CVE enrichment result for a finding."""

    finding_type: str = Field(description="VULNERABILITY | MISCONFIGURATION")
    classification_reasoning: str = Field(
        default="", description="Why the finding was classified this way"
    )

    # Component info (null for misconfigurations)
    software_component: str | None = Field(default=None)
    vendor: str | None = Field(default=None)
    version: str | None = Field(default=None)

    # CVE results (null for misconfigurations)
    cve_ids: list[str] | None = Field(
        default=None,
        description="Matched CVE IDs — null for misconfigurations"
    )
    cve_details: list[CveDetail] = Field(default_factory=list)
    evaluation_summary: list[CveEvaluation] = Field(default_factory=list)

    # Metadata
    search_sources: list[str] = Field(
        default_factory=list,
        description="Which sources were queried: NVD_CPE, NVD_KEYWORD, OSV, EXPLICIT"
    )
    enrichment_duration_seconds: float = Field(default=0.0)
```

---

## 2. Modifications to Existing Models

### `src/retrieval/models.py` — QueryResponse

```python
# Add import
from src.scoring.models import CveEnrichment  # NEW

class QueryResponse(BaseModel):
    finding_text: str
    cvss: CVSSResult | None = Field(default=None)
    cve_enrichment: CveEnrichment | None = Field(     # ← NEW
        default=None,
        description="CVE identification and enrichment results",
    )
    mappings: list[ControlMapping] = Field(default_factory=list)
    frameworks_searched: list[str] = Field(default_factory=list)
    chunks_retrieved: int = 0
    duration_seconds: float = 0.0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
```

### `src/retrieval/models.py` — TokenUsage

```python
class TokenUsage(BaseModel):
    # Existing fields
    mapper_prompt_tokens: int = 0
    mapper_total_tokens: int = 0
    critic_prompt_tokens: int = 0
    critic_total_tokens: int = 0
    critic_skipped: bool = False
    cvss_prompt_tokens: int = 0
    cvss_total_tokens: int = 0

    # NEW: CVE enrichment tokens
    cve_classifier_prompt_tokens: int = 0        # ← NEW
    cve_classifier_total_tokens: int = 0         # ← NEW
    cve_evaluator_prompt_tokens: int = 0         # ← NEW
    cve_evaluator_total_tokens: int = 0          # ← NEW

    total_tokens: int = Field(default=0)
```

### `src/config/settings.py` — CveSettings

```python
class CveSettings(BaseModel):
    """CVE identification and enrichment configuration."""

    enabled: bool = True
    nvd_api_key: str | None = None
    nvd_base_url: str = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    nvd_timeout: float = 10.0
    cve_org_base_url: str = "https://cveawg.mitre.org/api"
    osv_base_url: str = "https://api.osv.dev"
    max_cves_per_finding: int = 5
    cache_ttl_hours: int = 24
    llm_evaluation_threshold: int = 60   # minimum relevance_score to accept

class AppSettings(BaseSettings):
    # ... existing fields ...
    cve: CveSettings = CveSettings()     # ← NEW
```

---

## 3. Model Dependency Chain

```
FindingClassification
        │
        ├── (MISCONFIGURATION) ──► CveEnrichment (cve_ids=null)
        │
        └── (VULNERABILITY)
                │
                ▼
        CveSearchResult[]
                │
                ▼
        CveDetail[]
                │
                ▼
        CveEvaluation[] ──► CveEvaluationResult
                │
                ▼
        CveEnrichment ──► QueryResponse.cve_enrichment
```

---

## 4. Example Payloads

### Vulnerability Finding

```json
{
  "finding_classification": {
    "finding_type": "VULNERABILITY",
    "reasoning": "Finding identifies Next.js 13.0.0 with a specific SSRF flaw in Server Actions",
    "software_component": "Next.js",
    "vendor": "vercel",
    "version": "13.0.0",
    "ecosystem": "npm",
    "named_vulnerability": null,
    "explicit_cve_ids": []
  },
  "cve_search_results": [
    {
      "cve_id": "CVE-2024-34351",
      "source": "NVD_CPE",
      "description": "Next.js Server-Side Request Forgery in Server Actions",
      "affected_product": "next.js",
      "affected_versions": ">= 13.4.0, < 14.1.1"
    }
  ],
  "cve_evaluation": {
    "cve_id": "CVE-2024-34351",
    "is_relevant": true,
    "relevance_score": 45,
    "reasoning": "Version 13.0.0 is below the affected range 13.4.0-14.1.1 — not affected"
  }
}
```

### Misconfiguration Finding

```json
{
  "finding_classification": {
    "finding_type": "MISCONFIGURATION",
    "reasoning": "Finding describes missing MFA enforcement, a policy/control gap with no software-specific vulnerability",
    "software_component": null,
    "vendor": null,
    "version": null,
    "ecosystem": null,
    "named_vulnerability": null,
    "explicit_cve_ids": []
  }
}
```

### Named Vulnerability Finding

```json
{
  "finding_classification": {
    "finding_type": "VULNERABILITY",
    "reasoning": "Finding mentions Log4Shell vulnerability in Apache Log4j 2.14.1",
    "software_component": "Log4j",
    "vendor": "apache",
    "version": "2.14.1",
    "ecosystem": "Maven",
    "named_vulnerability": "Log4Shell",
    "explicit_cve_ids": []
  }
}
```
