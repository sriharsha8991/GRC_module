# CVE ID Management — Architecture Overview

## 1. Problem Statement

The GRC module currently maps security findings to compliance controls and
computes CVSS 3.1 scores via LLM. However, it lacks **CVE identification** —
the ability to associate a finding with known, published vulnerability
identifiers from authoritative sources (NVD, MITRE/cve.org, OSV.dev).

### Key Insight

> **CVE IDs are always tied to a specific software component and its version.**
> A CVE exists because a vulnerability was found in `Product X` version `Y`.
> Misconfigurations (weak passwords, missing MFA, unreviewed firewall rules)
> are policy/control gaps — they do not have CVE IDs.

This means the system must first **classify** the finding before searching.

---

## 2. Design Principles

The implementation follows **SOLID principles** applied to the existing
codebase patterns established in `src/scoring/` and `src/retrieval/`.

| Principle | Application |
|-----------|-------------|
| **S** — Single Responsibility | Each module does exactly one thing: classify, search, fetch, evaluate, or orchestrate. No module owns two concerns. |
| **O** — Open/Closed | New CVE data sources (ExploitDB, VulnDB, CERT-In) are added by implementing a `CveDataSource` protocol — existing code stays untouched. |
| **L** — Liskov Substitution | All data sources conform to the same protocol. NVD, cve.org, and OSV.dev are interchangeable via the common interface. |
| **I** — Interface Segregation | Callers only depend on the interfaces they use. The pipeline imports `enrich_cves()` — it never sees HTTP clients or LLM prompts. |
| **D** — Dependency Inversion | High-level orchestrator depends on abstractions (protocols), not on `httpx` calls or Gemini API details. Settings are injected, not hard-coded. |

---

## 3. Module Map

All CVE modules live in **`src/scoring/`** alongside the existing CVSS scorer,
because CVE identification is a scoring/enrichment concern — not a retrieval
concern.

```
src/scoring/
├── __init__.py                 # existing — public exports
├── models.py                   # existing + new CVE models
├── classifier.py               # existing — CVSS metric classifier
├── engine.py                   # existing — CVSS score computation
│
│   ── NEW CVE MODULES ──
├── finding_classifier.py       # [S] Classify: Vulnerability vs Misconfiguration
├── cve_searcher.py             # [S] Orchestrate multi-source CVE search
├── cve_client.py               # [S] HTTP clients for NVD, cve.org, OSV.dev
├── cve_evaluator.py            # [S] LLM judge — validate CVE relevance
└── cve_pipeline.py             # [S] Orchestrator composing all CVE steps
```

---

## 4. How It Fits Into the Existing Pipeline

The current `query_finding()` in `src/retrieval/pipeline.py` uses a
`ThreadPoolExecutor` to run tasks in parallel. CVE enrichment is added as a
**third parallel track** — zero latency increase on the existing flow.

### Current Pipeline (2 parallel tracks)

```
                  ┌─── [Embed → Search → Map → Critique] ─┐
  finding_text ──►│                                         │──► QueryResponse
                  └─── [CVSS Classification + Scoring] ────┘
```

### Extended Pipeline (3 parallel tracks)

```
                  ┌─── [Embed → Search → Map → Critique] ─────────────────┐
  finding_text ──►├─── [CVSS Classification + Scoring]        (existing) ──┤──► QueryResponse
                  └─── [CVE: Classify → Search → Evaluate]    (new)  ─────┘
```

The three tracks are completely independent:
- **Track 1** needs Qdrant + Gemini (mapper/critic)
- **Track 2** needs Gemini (CVSS classifier)
- **Track 3** needs Gemini (finding classifier + CVE evaluator) + HTTP (NVD/OSV/cve.org)

After all futures complete, results are merged into a single `QueryResponse`.

---

## 5. Data Source Summary

| Source | Role | Auth | Rate Limits | Used For |
|--------|------|------|-------------|----------|
| **NVD API v2.0** | CVE search by CPE + keyword | Optional API key | 5 req/30s (no key), 50/30s (key) | Finding CVE IDs for component+version |
| **cve.org (MITRE)** | CVE record detail + CISA enrichment | None (public) | Rate-limited headers | Fetching full CVE record with SSVC/KEV |
| **OSV.dev** | Package vulnerability search | None | Zero rate limits | npm/PyPI/Maven ecosystem packages |
| **Static Lookup** | Named vulnerability → CVE mapping | N/A | N/A | Log4Shell, POODLE, Heartbleed, etc. |
| **Gemini LLM** | Classification + evaluation judge | API key | Per Gemini plan | Vuln/Misconfig split + CVE validation |

---

## 6. Response Model (Preview)

```json
{
  "finding_text": "Next.js 13.0.0 vulnerable to SSRF via Server Actions",
  "cvss": { "score": 7.5, "severity": "High", "cvss_vector": "CVSS:3.1/..." },
  "cve_enrichment": {
    "finding_type": "VULNERABILITY",
    "software_component": "Next.js",
    "vendor": "vercel",
    "version": "13.0.0",
    "cve_ids": ["CVE-2024-34351"],
    "cve_details": [{
      "cve_id": "CVE-2024-34351",
      "description": "Next.js Server-Side Request Forgery in Server Actions...",
      "cvss_score": 7.5,
      "cvss_severity": "HIGH",
      "cwe_id": "CWE-918",
      "kev": false,
      "ssvc_exploitation": "none",
      "ssvc_automatable": "yes"
    }],
    "search_sources": ["NVD_CPE"],
    "enrichment_duration_seconds": 1.23
  },
  "mappings": [...]
}
```

For a misconfiguration finding:

```json
{
  "finding_text": "MFA not enforced for VPN access",
  "cvss": { "score": 6.5, "severity": "Medium", "cvss_vector": "CVSS:3.1/..." },
  "cve_enrichment": {
    "finding_type": "MISCONFIGURATION",
    "classification_reasoning": "Finding describes a missing access control policy...",
    "software_component": null,
    "cve_ids": null,
    "cve_details": []
  },
  "mappings": [...]
}
```
