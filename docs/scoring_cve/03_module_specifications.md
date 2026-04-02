# CVE ID Management — Module Specifications

Each module has a **single responsibility** and a clearly defined interface.
This document specifies the contract for every new module.

---

## Module 1: `finding_classifier.py` — Finding Type Classifier

**Responsibility:** Classify a security finding as `VULNERABILITY` or
`MISCONFIGURATION` and extract component metadata if vulnerability.

**Pattern:** Mirrors `src/scoring/classifier.py` (Gemini structured output).

### Interface

```python
class FindingClassifier:
    """Classifies findings into VULNERABILITY or MISCONFIGURATION."""

    def __init__(self, settings: AppSettings) -> None: ...

    def classify(self, finding_text: str) -> tuple[FindingClassification, dict]:
        """Classify a finding and extract component details.

        Args:
            finding_text: Raw security finding text.

        Returns:
            (FindingClassification, {"prompt_tokens": int, "total_tokens": int})
        """
```

### LLM System Prompt (Key Points)

```
You are a cybersecurity analyst. Classify the security finding as:

VULNERABILITY — A flaw in a specific software component/version that could
   have a CVE identifier. Signals: software names, version numbers,
   protocol weaknesses (TLS 1.0, SSL 3.0, WEP), named vulnerabilities
   (Log4Shell, Heartbleed), explicit CVE IDs.

MISCONFIGURATION — A policy gap, weak configuration, or missing control.
   Signals: "not configured", "not enforced", "missing", "weak policy",
   "no MFA", "access reviews not conducted", "firewall rules not reviewed".

If VULNERABILITY: extract software_component, vendor, version, ecosystem.
If MISCONFIGURATION: leave component fields null.
Always: extract any CVE IDs verbatim present via regex CVE-\d{4}-\d{4,}.
```

### Output Model

```python
class FindingClassification(BaseModel):
    finding_type: Literal["VULNERABILITY", "MISCONFIGURATION"]
    reasoning: str
    software_component: str | None    # "Next.js", "OpenSSL"
    vendor: str | None                # "vercel", "openssl"
    version: str | None               # "13.0.0", "1.1.1k"
    version_range: str | None         # ">= 13.4.0, < 14.1.1"
    ecosystem: str | None             # "npm", "PyPI", "Maven", "OS"
    named_vulnerability: str | None   # "Log4Shell", "POODLE"
    explicit_cve_ids: list[str]       # CVEs found verbatim in text
```

### SOLID Compliance

| Principle | How |
|-----------|-----|
| **S** | Only classifies — doesn't search, fetch, or validate CVEs |
| **O** | New finding types can be added to the Literal without modifying internals |
| **D** | Depends on `AppSettings` (injected), not on hard-coded config |

---

## Module 2: `cve_searcher.py` — Multi-Source CVE Search

---

## Module 3: `cve_client.py` — External API Clients

**Responsibility:** HTTP communication with NVD, cve.org, and OSV.dev.
Handles serialization, deserialization, rate limiting, and error handling.
Does NOT decide which API to call — that's the searcher's job.

### Interface

```python
from typing import Protocol

class CveDataSource(Protocol):
    """Contract for any CVE data source."""

    async def search_by_component(
        self, product: str, version: str, vendor: str | None = None,
    ) -> list[CveSearchResult]: ...

    async def fetch_detail(self, cve_id: str) -> CveDetail | None: ...


class NvdClient:
    """NIST NVD API v2.0 client."""

    def __init__(self, settings: CveSettings) -> None: ...

    def search_by_cpe(
        self, product: str, version: str, vendor: str | None = None,
    ) -> list[CveSearchResult]:
        """Search NVD using virtualMatchString CPE matching.

        URL: GET /rest/json/cves/2.0?virtualMatchString=cpe:2.3:a:{vendor}:{product}:{version}
        """

    def search_by_keyword(
        self, keywords: str, max_results: int = 10,
    ) -> list[CveSearchResult]:
        """Search NVD using keywordSearch text matching.

        URL: GET /rest/json/cves/2.0?keywordSearch={keywords}&resultsPerPage={max}
        """

    def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """Fetch full CVE record from NVD.

        URL: GET /rest/json/cves/2.0?cveId={cve_id}
        """


class CveOrgClient:
    """MITRE cve.org API client (CVE Services)."""

    def __init__(self, settings: CveSettings) -> None: ...

    def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """Fetch CVE record from cve.org — includes CISA-ADP enrichment.

        URL: GET https://cveawg.mitre.org/api/cve/{cve_id}
        Parses: CNA container + CISA-ADP container (SSVC, KEV, CVSS, CWE)
        """


class OsvClient:
    """Google OSV.dev API client."""

    def __init__(self, settings: CveSettings) -> None: ...

    def query_package(
        self, name: str, ecosystem: str, version: str,
    ) -> list[CveSearchResult]:
        """Query OSV for vulnerabilities in a specific package version.

        URL: POST https://api.osv.dev/v1/query
        Body: {"package": {"name": ..., "ecosystem": ...}, "version": ...}
        """
```

### Rate Limiting Strategy

```python
# NVD: Token-bucket rate limiter
# - Without API key: 5 requests per 30 seconds
# - With API key:   50 requests per 30 seconds
# Implementation: asyncio.Semaphore + sleep(6) between requests

# cve.org: Respect RateLimit-* response headers
# - RateLimit-Remaining → remaining requests
# - RateLimit-Reset → seconds until quota resets

# OSV.dev: No rate limits — fire freely
```

### SOLID Compliance

| Principle | How |
|-----------|-----|
| **S** | Only handles HTTP — no search logic, no evaluation |
| **O** | New data sources implement `CveDataSource` protocol |
| **L** | All clients are substitutable via the protocol |
| **I** | Clients expose only the methods they support (NVD has search, cve.org doesn't) |
| **D** | Depends on `CveSettings` abstraction, not hard-coded URLs |

---

## Module 4: `cve_searcher.py` — Multi-Source Search Orchestrator

**Responsibility:** Given a classified vulnerability (component + version),
search across all sources in priority order and return deduplicated candidates.

### Interface

```python
class CveSearcher:
    """Orchestrates CVE search across NVD and OSV.dev."""

    def __init__(self, settings: AppSettings) -> None: ...

    def search(
        self, classification: FindingClassification,
    ) -> list[CveSearchResult]:
        """Search for CVE IDs matching the classified vulnerability.

        Priority order:
        1. Explicit CVE IDs from finding text (auto-approve)
        2. OSV.dev query (if ecosystem known)
        3. NVD virtualMatchString CPE search
        4. NVD keywordSearch fallback

        Returns deduplicated candidate list with source annotations.
        """
```

### Search Strategy (Decision Flow)

```
classification.explicit_cve_ids?──────► Use directly (source="EXPLICIT")
                │ empty
                ▼
classification.ecosystem known?───────► OSV.dev query (source="OSV")
                │
                ▼
NVD virtualMatchString CPE search ────► (source="NVD_CPE")
                │ empty
                ▼
NVD keywordSearch fallback ───────────► (source="NVD_KEYWORD")
                │
                ▼
Deduplicate all results
```

### SOLID Compliance

| Principle | How |
|-----------|-----|
| **S** | Only orchestrates search order — doesn't fetch details or evaluate |
| **O** | New search sources added without modifying existing strategy |
| **D** | Uses `NvdClient`, `OsvClient` via injection — not hard-coded |

---

## Module 5: `cve_evaluator.py` — LLM Evaluation Judge

**Responsibility:** Given a finding and candidate CVEs, use LLM as a
cybersecurity analyst to judge whether each CVE is a correct match.

### Interface

```python
class CveEvaluator:
    """LLM-based CVE relevance evaluator (judge pattern)."""

    def __init__(self, settings: AppSettings) -> None: ...

    def evaluate(
        self,
        finding_text: str,
        candidates: list[CveSearchResult],
        details: list[CveDetail],
    ) -> CveEvaluationResult:
        """Evaluate whether candidate CVEs are relevant to the finding.

        Args:
            finding_text: Original security finding.
            candidates: CVE candidates from searcher.
            details: Enriched CVE details from cve.org/NVD.

        Returns:
            CveEvaluationResult with per-CVE evaluations and final_cve_ids.
        """
```

### LLM System Prompt (Key Points)

```
You are a cybersecurity analyst acting as a CVE mapping expert.

Given a security finding and candidate CVEs, evaluate each CVE:

ACCEPT if:
  - The CVE affects the exact product mentioned in the finding
  - The finding's version falls within the CVE's affected version range
  - The vulnerability type is consistent (e.g., SSRF finding ↔ SSRF CVE)

REJECT if:
  - Different product (e.g., finding says "Nginx" but CVE is for "Apache")
  - Version not in affected range (e.g., finding says "v2.0" but CVE affects "< 1.5")
  - Different vulnerability class (e.g., finding describes XSS but CVE is buffer overflow)

For each CVE return:
  - is_relevant: true/false
  - relevance_score: 0-100
  - reasoning: one-line explanation
```

### Short-Circuit Rules

```python
# Skip LLM evaluation when source provides high confidence:
SKIP_EVALUATION_SOURCES = {"EXPLICIT"}

# Auto-approve: CVE IDs extracted from finding text are already
# validated by their source — no need to waste an LLM call.
```

### SOLID Compliance

| Principle | How |
|-----------|-----|
| **S** | Only evaluates — doesn't search, fetch, or classify |
| **O** | Threshold is configurable via `CveSettings.llm_evaluation_threshold` |
| **D** | Depends on Gemini client via `get_client(settings)` abstraction |

---

## Module 6: `cve_pipeline.py` — CVE Enrichment Orchestrator

**Responsibility:** Compose all CVE modules into a single function that
the retrieval pipeline can call. Owns the sequencing — not the logic.

### Interface

```python
def enrich_cves(
    finding_text: str,
    settings: AppSettings,
    cache: RedisCache | None = None,
) -> tuple[CveEnrichment, dict]:
    """Run the full CVE enrichment pipeline for a security finding.

    Steps:
    1. Classify finding (Vulnerability vs Misconfiguration)
    2. If MISCONFIGURATION → return early with cve_ids=null
    3. Search for CVE IDs (explicit → lookup → OSV → NVD)
    4. Fetch CVE details (cve.org primary, NVD fallback)
    5. Evaluate CVE relevance (LLM judge, skip for explicit/lookup)
    6. Assemble CveEnrichment with provenance metadata

    Args:
        finding_text: Raw security finding text.
        settings: Application settings.
        cache: Optional Redis cache for CVE detail caching.

    Returns:
        (CveEnrichment, {"classifier_tokens": dict, "evaluator_tokens": dict})
    """
```

### Composition Pattern

```python
def enrich_cves(finding_text, settings, cache=None):
    classifier = FindingClassifier(settings)
    searcher   = CveSearcher(settings)
    evaluator  = CveEvaluator(settings)

    # Phase 1: Classify
    classification, cls_tokens = classifier.classify(finding_text)

    if classification.finding_type == "MISCONFIGURATION":
        return CveEnrichment(finding_type="MISCONFIGURATION", cve_ids=None, ...), ...

    # Phase 2: Search
    candidates = searcher.search(classification)

    # Phase 2b: Fetch details (with Redis caching)
    details = _fetch_details(candidates, settings, cache)

    # Phase 3: Evaluate (skip for auto-approved sources)
    needs_eval = [c for c in candidates if c.source not in SKIP_EVALUATION_SOURCES]
    auto_approved = [c for c in candidates if c.source in SKIP_EVALUATION_SOURCES]

    eval_result = evaluator.evaluate(finding_text, needs_eval, details) if needs_eval else ...
    final_ids = [c.cve_id for c in auto_approved] + eval_result.final_cve_ids

    return CveEnrichment(finding_type="VULNERABILITY", cve_ids=final_ids, ...), ...
```

### SOLID Compliance

| Principle | How |
|-----------|-----|
| **S** | Only composes — delegates all logic to sub-modules |
| **O** | New phases added by injecting new step without rewriting |
| **I** | Exposes single `enrich_cves()` function — callers don't see internals |
| **D** | All sub-modules injected via `settings`, not hard-coded |
