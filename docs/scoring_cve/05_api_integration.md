# CVE ID Management — External API Integration Guide

This document details the three external data sources used for CVE identification,
including request/response examples, rate limiting, error handling, and caching.

---

## 1. NVD API v2.0 (Primary)

| Property        | Value                                                  |
|-----------------|--------------------------------------------------------|
| Base URL        | `https://services.nvd.nist.gov/rest/json/cves/2.0`    |
| Auth            | Optional API key via `apiKey` header                   |
| Rate Limit      | **5 req/30s** (no key) · **50 req/30s** (with key)    |
| Data Format     | JSON — NVD CVE 2.0 schema                             |
| Documentation   | https://nvd.nist.gov/developers/vulnerabilities        |

### 1.1 CPE Match Search (Primary Strategy)

Use `virtualMatchString` with a wildcard vendor to find CVEs
affecting a specific product+version:

```
GET /rest/json/cves/2.0?virtualMatchString=cpe:2.3:a:*:next.js:13.0.0:*:*:*:*:*:*:*
```

**Response (abbreviated):**
```json
{
  "resultsPerPage": 13,
  "startIndex": 0,
  "totalResults": 13,
  "vulnerabilities": [
    {
      "cve": {
        "id": "CVE-2024-34351",
        "descriptions": [
          {"lang": "en", "value": "Next.js Server-Side Request Forgery in Server Actions..."}
        ],
        "metrics": {
          "cvssMetricV31": [{
            "source": "nvd@nist.gov",
            "cvssData": {
              "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
              "baseScore": 7.5,
              "baseSeverity": "HIGH"
            }
          }]
        },
        "weaknesses": [
          {"description": [{"value": "CWE-918"}]}
        ],
        "configurations": [{
          "nodes": [{
            "cpeMatch": [{
              "criteria": "cpe:2.3:a:vercel:next.js:*:*:*:*:*:*:*:*",
              "versionStartIncluding": "13.4.0",
              "versionEndExcluding": "14.1.1",
              "vulnerable": true
            }]
          }]
        }]
      }
    }
  ]
}
```

**CPE Format:**
```
cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*
```

Use `*` for vendor when unknown. The `a` denotes application type.

### 1.2 Keyword Search (Fallback)

When CPE match yields zero results, fall back to keyword search:

```
GET /rest/json/cves/2.0?keywordSearch=next.js%2013.0.0&resultsPerPage=10
```

Less precise — returns CVEs mentioning those keywords in descriptions.
Apply `keywordExactMatch` for stricter matching.

### 1.3 Single CVE Fetch

For explicit CVE IDs found in finding text:

```
GET /rest/json/cves/2.0?cveId=CVE-2024-34351
```

### 1.4 NVD Client Implementation

```python
class NvdClient:
    """NVD API v2.0 client with rate limiting and retry."""

    def __init__(self, settings: CveSettings):
        self.base_url = settings.nvd_base_url
        self.api_key = settings.nvd_api_key
        self.timeout = settings.nvd_timeout

    async def search_by_cpe(
        self, product: str, version: str, vendor: str = "*"
    ) -> list[CveSearchResult]:
        """CPE virtual match search — primary strategy."""
        params = {
            "virtualMatchString": f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*",
            "resultsPerPage": 20,
        }
        # ... HTTP GET with rate limiting ...

    async def search_by_keyword(
        self, keywords: str, max_results: int = 10
    ) -> list[CveSearchResult]:
        """Keyword search — fallback strategy."""
        params = {"keywordSearch": keywords, "resultsPerPage": max_results}
        # ...

    async def fetch_cve(self, cve_id: str) -> CveDetail | None:
        """Fetch a single CVE record by ID."""
        params = {"cveId": cve_id}
        # ...
```

### 1.5 Rate Limiting Strategy

```python
class NvdRateLimiter:
    """Token bucket: 5 req/30s (no key) or 50 req/30s (with key)."""

    def __init__(self, has_api_key: bool):
        self.max_tokens = 50 if has_api_key else 5
        self.window_seconds = 30
        self.tokens = self.max_tokens
        self.last_refill = time.monotonic()

    async def acquire(self):
        """Wait until a request token is available."""
        # Refill tokens based on elapsed time
        # If tokens <= 0, sleep until next refill window
```

---

## 2. cve.org API (Enrichment)

| Property        | Value                                               |
|-----------------|-----------------------------------------------------|
| Base URL        | `https://cveawg.mitre.org/api`                      |
| Auth            | None (public `GET /api/cve/{id}`)                   |
| Rate Limit      | Undocumented — apply 10 req/min conservative limit   |
| Data Format     | CVE JSON 5.x (CNA + ADP containers)                |
| Documentation   | https://cveawg.mitre.org/api-docs                   |

### 2.1 Fetch CVE Record

```
GET https://cveawg.mitre.org/api/cve/CVE-2024-34351
```

**Response structure:**
```json
{
  "cveMetadata": {
    "cveId": "CVE-2024-34351",
    "state": "PUBLISHED",
    "datePublished": "2024-05-09T...",
    "dateUpdated": "2025-02-13T..."
  },
  "containers": {
    "cna": {
      "title": "Next.js Server-Side Request Forgery in Server Actions",
      "descriptions": [{"lang": "en", "value": "..."}],
      "affected": [{
        "vendor": "vercel",
        "product": "next.js",
        "versions": [
          {"version": "13.4.0", "lessThan": "14.1.1", "status": "affected"}
        ]
      }],
      "metrics": [{
        "cvssV3_1": {
          "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
          "baseScore": 7.5,
          "baseSeverity": "HIGH"
        }
      }],
      "problemTypes": [{
        "descriptions": [{"cweId": "CWE-918", "description": "SSRF"}]
      }]
    },
    "adp": [{
      "providerMetadata": {"shortName": "CISA-ADP"},
      "title": "CISA ADP Vulnrichment",
      "metrics": [{
        "other": {
          "type": "ssvc",
          "content": {
            "id": "CVE-2024-34351",
            "options": [
              {"Exploitation": "none"},
              {"Automatable": "yes"},
              {"Technical Impact": "partial"}
            ]
          }
        }
      }]
    }]
  }
}
```

### 2.2 Unique Value: CISA-ADP Container

The cve.org API is the **only** source providing CISA-ADP enrichment inline:

| Field               | Description                                  |
|---------------------|----------------------------------------------|
| `Exploitation`      | `none` · `poc` · `active`                    |
| `Automatable`       | `yes` · `no`                                 |
| `Technical Impact`  | `partial` · `total`                          |
| KEV flag            | Present if in Known Exploited Vulnerabilities |

### 2.3 When to Use cve.org

cve.org is used **after** NVD/OSV search returns CVE IDs, to fetch:
- CISA-ADP SSVC enrichment data
- CNA-provided CVSS (may differ from NVD's)
- Affected version ranges with exact semantics
- CWE identifiers from the CNA

---

## 3. OSV.dev API (Ecosystem Search)

| Property        | Value                               |
|-----------------|---------------------------------------|
| Base URL        | `https://api.osv.dev`                 |
| Auth            | None                                  |
| Rate Limit      | **None** (zero rate limits)           |
| Data Format     | OSV schema                            |
| Documentation   | https://osv.dev/docs/                 |

### 3.1 Query by Package

```
POST https://api.osv.dev/v1/query

{
  "package": {
    "name": "next",
    "ecosystem": "npm"
  },
  "version": "13.0.0"
}
```

**Response (abbreviated):**
```json
{
  "vulns": [
    {
      "id": "GHSA-fr5h-rqp8-mj6g",
      "aliases": ["CVE-2024-34351"],
      "summary": "Next.js Server-Side Request Forgery in Server Actions",
      "affected": [{
        "package": {"ecosystem": "npm", "name": "next"},
        "ranges": [{
          "type": "SEMVER",
          "events": [
            {"introduced": "13.4.0"},
            {"fixed": "14.1.1"}
          ]
        }]
      }],
      "severity": [{
        "type": "CVSS_V3",
        "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"
      }]
    }
  ]
}
```

### 3.2 Query by CVE ID

```
POST https://api.osv.dev/v1/query
{"query": "CVE-2024-34351"}
```

Returns the same record but via alias lookup.

### 3.3 Ecosystem Mapping

| Finding Ecosystem | OSV Ecosystem |
|-------------------|---------------|
| npm               | npm           |
| PyPI              | PyPI          |
| Maven             | Maven         |
| Go                | Go            |
| NuGet             | NuGet         |
| RubyGems          | RubyGems      |
| Packagist         | Packagist     |
| crates.io         | crates.io     |
| OS (Linux)        | Debian, Alpine, etc. |

### 3.4 When to Use OSV.dev

OSV.dev is queried **in parallel** with NVD when:
- Ecosystem is known (npm, PyPI, Maven, etc.)
- Zero rate limits make it ideal as a complementary source
- Provides GHSA IDs that map to CVE aliases

---

## 4. Multi-Source Search Strategy

```
┌─────────────────────────────────────────────────────┐
│              CveSearcher.search()                    │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Step 1: Short-circuit checks                        │
│  └── Explicit CVEs in text? → return immediately     │
│                                                      │
│  Step 2: Parallel API search                         │
│  ├── NVD virtualMatchString (cpe:2.3:a:*:prod:ver)  │
│  └── OSV.dev POST /v1/query (if ecosystem known)    │
│                                                      │
│  Step 3: Fallback (if Step 2 returned 0 results)     │
│  └── NVD keywordSearch (product + version)           │
│                                                      │
│  Step 4: Deduplicate across sources                  │
│  └── Merge by CVE ID, prefer NVD for CVSS data      │
│                                                      │
│  Step 5: Detail enrichment                           │
│  └── cve.org GET /api/cve/{id} for top-N matches    │
│      (fetch CISA-ADP enrichment data)                │
│                                                      │
│  Step 6: Return CveSearchResult[]                    │
└─────────────────────────────────────────────────────┘
```

---

## 5. Error Handling

| Scenario               | Behavior                                          |
|-------------------------|---------------------------------------------------|
| NVD 403 rate limited    | Exponential backoff, max 3 retries, 2s base delay |
| NVD 5xx                 | Skip NVD, rely on OSV results only                |
| OSV.dev 5xx             | Skip OSV, rely on NVD results only                |
| cve.org timeout         | Return CveDetail with available data only         |
| All sources fail        | Return `CveEnrichment` with empty `cve_ids`       |
| Network unreachable     | Log warning, return empty enrichment gracefully    |

### Retry Configuration

```python
RETRY_CONFIG = {
    "max_retries": 3,
    "base_delay": 2.0,        # seconds
    "max_delay": 30.0,        # seconds
    "backoff_factor": 2.0,    # exponential
    "retryable_status": [429, 500, 502, 503, 504],
}
```

---

## 6. Caching Strategy

### Redis Cache Keys

```
cve:nvd:cpe:{product}:{version}     → list[CveSearchResult]  TTL: 24h
cve:nvd:keyword:{hash}              → list[CveSearchResult]  TTL: 12h
cve:osv:{ecosystem}:{name}:{version}→ list[CveSearchResult]  TTL: 24h
cve:detail:{cve_id}                 → CveDetail              TTL: 48h
cve:enrichment:{finding_hash}       → CveEnrichment          TTL: 24h
```

### Cache Flow

```
1. Check cve:enrichment:{hash} → full result cache hit? return
2. Check cve:nvd:cpe:{p}:{v}  → search result cache hit? skip API
3. Check cve:osv:{eco}:{n}:{v}→ search result cache hit? skip API
4. Check cve:detail:{id}      → detail cache hit? skip cve.org fetch
5. After computation, write all cache layers
```

### Why Multi-Layer Cache?

- **Enrichment cache**: Avoids re-running full pipeline (classification + search + evaluation)
- **Search cache**: Same component+version across different findings shares results
- **Detail cache**: Same CVE ID across different findings shares enrichment data
- Longest TTL on detail records (48h) because CVE records change infrequently

---

## 7. HTTP Client Configuration

```python
import httpx

def create_http_client(timeout: float = 10.0) -> httpx.AsyncClient:
    """Shared async HTTP client with connection pooling."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=5,
        ),
        headers={"User-Agent": "GRC-Module/1.0"},
        follow_redirects=True,
    )
```

All three API clients share the same `httpx.AsyncClient` pool for
efficient connection reuse.
