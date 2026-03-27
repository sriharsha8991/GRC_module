# GRC Compliance Module

AI-powered compliance mapping engine that ingests GRC framework PDFs and maps security findings to relevant controls using RAG (Retrieval-Augmented Generation), with automatic **CVSS 3.1 vulnerability scoring**.

Upload a framework PDF → the system extracts, chunks, and embeds it into a vector store. Then query any security finding → get back structured control mappings with citations, confidence scores, adversarial validation, and a full CVSS 3.1 risk assessment.

---

## Architecture

```
                         ┌─────────────────────────┐
                         │     FastAPI (/api/v1)    │
                         │    uvicorn :8080 (dock)  │
                         └────────┬────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
     POST /ingestion/ingest   POST /query      GET /cache/stats
              │                   │                   │
              ▼                   ▼                   ▼
     ┌────────────────┐  ┌───────────────┐    ┌────────────┐
     │   Ingestion    │  │  Retrieval    │    │   Redis    │
     │   Pipeline     │  │  Pipeline     │───►│   Cache    │
     │                │  │               │    │  (LFU)     │
     │ Extract (PDF)  │  │ 1. Embed      │    └────────────┘
     │ Chunk          │  │ 2. Search     │
     │ Embed          │  │ 3. Rerank     │
     │ Upsert         │  │ 4. Map + CVSS │  ◄── parallel
     └───────┬────────┘  │ 5. Critique   │
             │           └──┬─────┬──────┘
             │              │     │
             ▼              ▼     ▼
        ┌─────────┐   ┌────────┐ ┌─────────┐
        │ Qdrant  │   │ Gemini │ │Reranker │
        │ :6333   │   │  API   │ │(Jina/TEI)│
        └─────────┘   └────────┘ └─────────┘
```

**Infrastructure** (Docker Compose):

| Service | Image | Port | Purpose |
|---|---|---|---|
| Qdrant | `qdrant/qdrant:v1.12.4` | 6333, 6334 | Vector database |
| Redis | `redis:7-alpine` | 6379 | Query response cache (LFU eviction) |
| API | Built from `src/api/Dockerfile` | 8080 | Core application |
| Gateway | Built from `src/gateway/Dockerfile` | 8000 | Health-check aggregator (optional) |

**External APIs**: Gemini (embedding + LLM + CVSS classification), Jina Reranker (optional)

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose
- Gemini API key

### 1. Clone & set up

```bash
git clone <repo-url>
cd GRC_module
python -m venv venv
venv\Scripts\Activate.ps1      # Windows
# source venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

### 2. Configure environment

Copy and edit the `.env` file. The minimum required settings:

```env
# Gemini (required)
GRC_GEMINI__API_KEY=your-gemini-api-key
GRC_GEMINI__PARSE_MODEL=gemini-2.5-flash
GRC_GEMINI__EMBEDDING_MODEL=gemini-embedding-001
GRC_GEMINI__WINDOW_SIZE=30000

# Qdrant
GRC_QDRANT__URL=http://localhost:6333
GRC_QDRANT__COLLECTION_NAME=grc_controls
GRC_QDRANT__DISTANCE=Cosine

# Embedding
GRC_EMBEDDING__DIMENSION=1536
GRC_EMBEDDING__BATCH_SIZE=32

# Chunking
GRC_CHUNKING__SIZE=256
GRC_CHUNKING__OVERLAP=50

# Storage
GRC_STORAGE__BACKEND=local
GRC_STORAGE__LOCAL_PDF_DIR=data/pdfs
GRC_STORAGE__DELETE_PDF_AFTER_INGESTION=true

# Retrieval
GRC_RETRIEVAL__USE_RERANKER=false
GRC_RETRIEVAL__LIMIT=10
GRC_RETRIEVAL__CRITIC_CONFIDENCE_THRESHOLD=70

# Reranker (optional — only if USE_RERANKER=true)
GRC_RERANKER__URL=http://localhost:8082
GRC_RERANKER__BACKEND=jina
GRC_RERANKER__THRESHOLD=0.01
GRC_RERANKER__JINA_API_KEY=your-jina-api-key
GRC_RERANKER__JINA_MODEL=jina-reranker-v2-base-multilingual

# Redis
GRC_REDIS__URL=redis://localhost:6379/0
GRC_REDIS__ENABLED=true
GRC_REDIS__SOCKET_TIMEOUT=1.0
GRC_REDIS__MAX_MEMORY_MB=1024
GRC_REDIS__EVICTION_TRIGGER_PCT=80
GRC_REDIS__EVICTION_TARGET_PCT=30
GRC_REDIS__LOCK_TIMEOUT=30
GRC_REDIS__KEY_PREFIX=grc
```

All settings use the `GRC_` prefix with `__` as the nested delimiter (loaded via Pydantic Settings).

### 3. Start infrastructure

```bash
docker compose up -d
```

This starts Qdrant, Redis, and the API. Check health:

```bash
docker compose ps
```

### 4. Start the API (local dev)

```bash
uvicorn src.api.main:app --reload --port 8001
```

### 5. Ingest a framework PDF

```bash
curl -X POST http://localhost:8001/api/v1/ingestion/ingest \
  -F "file=@data/pdfs/iso_27001/iso27001.pdf" \
  -F "framework_key=iso_27001"
```

### 6. Query a security finding

```bash
curl -X POST http://localhost:8001/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "finding_text": "SQL injection vulnerability in login form allows unauthenticated attackers to extract sensitive data",
    "target_frameworks": ["iso_27001"]
  }'
```

---

## CVSS 3.1 Scoring

Every query automatically receives a **CVSS 3.1 base score assessment** alongside control mappings. The scoring module runs in parallel with the mapping/critic pipeline, adding zero extra latency.

### How it works

```
Finding text
     │
     ├──► CVSSClassifier (Gemini structured output)
     │         │
     │         ▼
     │    CVSSClassification (8 base metrics)
     │         │
     │         ▼
     │    Scoring Engine (cvss library)
     │         │
     │         ▼
     │    CVSSResult (score + vector + severity + remediation)
     │
     └──► Mapper / Critic pipeline (parallel)
```

### Scoring approach

- **LLM-as-expert**: The classifier reasons through each CVSS metric like an experienced penetration tester, analyzing the vulnerability class, attack scenarios, and realistic worst-case impact.
- **No hardcoded rules**: The LLM independently evaluates attack vector, complexity, privileges, user interaction, scope, and CIA impact for each finding.
- **Deterministic scoring**: Once metrics are classified, the `cvss` library computes the exact base score per the FIRST CVSS v3.1 specification.
- **Severity thresholds** (FIRST spec §5): Critical (9.0–10.0), High (7.0–8.9), Medium (4.0–6.9), Low (0.1–3.9), None (0.0).

### Output fields

| Field | Description |
|---|---|
| `name` | Finding name |
| `description` | 2–3 sentence technical description of the vulnerability |
| `potential_impact` | 2–3 sentences: what an attacker gains, affected assets, business consequences |
| `severity` | Critical / High / Medium / Low / None |
| `score` | CVSS 3.1 base score (0.0–10.0) |
| `cvss_vector` | Full vector string (e.g., `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H`) |
| `cve` | CVE identifier (null — reserved for future CVE lookup integration) |
| `confidence` | High / Medium / Low — classifier's confidence in the assessment |
| `how_to_remediate` | Numbered step-by-step remediation plan |
| `metric_rationale` | Per-metric reasoning (e.g., "AV:N — the service is exposed over the network") |

### Graceful degradation

If CVSS classification fails (e.g., Gemini timeout), the response returns `"cvss": null` and the control mappings are unaffected.

---

## API Reference

Base path: `/api/v1`

### `POST /api/v1/query`

Map a security finding to compliance framework controls with CVSS scoring.

**Request:**
```json
{
  "finding_text": "SQL injection vulnerability in login form allows unauthenticated attackers to extract sensitive data",
  "target_frameworks": ["iso_27001"]
}
```

**Response:**
```json
{
  "finding_text": "SQL injection vulnerability in login form...",
  "cvss": {
    "name": "SQL Injection in Login Form",
    "description": "The login form does not sanitize user input, allowing an attacker to inject arbitrary SQL queries. This arises from directly concatenating user-supplied data into SQL statements without parameterization.",
    "potential_impact": "An attacker can extract, modify, or delete database contents including user credentials, PII, and business-critical data. This could lead to full database compromise, unauthorized access to other accounts, and regulatory violations.",
    "severity": "Critical",
    "score": 10.0,
    "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
    "cve": null,
    "confidence": "High",
    "how_to_remediate": "1. Implement parameterized queries or prepared statements for all database interactions. 2. Apply input validation and sanitization on all user inputs. 3. Use an ORM or query builder that handles escaping automatically. 4. Deploy a WAF rule to detect and block SQL injection patterns.",
    "metric_rationale": "AV:N — web login form is network-accessible. AC:L — standard SQL injection techniques, no special conditions. PR:N — unauthenticated attack via login form. UI:N — no user interaction required. S:C — database compromise can affect other application components. C:H — full read access to database. I:H — ability to modify or delete data. A:H — attacker can drop tables or lock the database."
  },
  "mappings": [
    {
      "framework": "ISO/IEC 27001:2022",
      "framework_version": "2022",
      "control_id": "A.8.20",
      "control_title": "Networks security",
      "domain": "Technological controls",
      "risk_mitigated": "Unauthorized remote database access",
      "citation": "Network services shall be secured...",
      "citation_source": "ISO/IEC 27001:2022, Annex A > A.8 Technological controls",
      "confidence_score": 92,
      "status": "APPROVED",
      "critic_reason": null
    }
  ],
  "frameworks_searched": ["iso_27001"],
  "chunks_retrieved": 10,
  "duration_seconds": 3.42,
  "token_usage": {
    "mapper_prompt_tokens": 2401,
    "mapper_total_tokens": 3838,
    "critic_prompt_tokens": 0,
    "critic_total_tokens": 0,
    "critic_skipped": true,
    "cvss_prompt_tokens": 450,
    "cvss_total_tokens": 1800,
    "total_tokens": 5638
  }
}
```

| Status | Meaning |
|---|---|
| `200` | Mappings + CVSS returned (mappings may be empty if no relevant controls found) |
| `422` | Unknown framework key(s) in `target_frameworks` |
| `500` | Pipeline failure (Qdrant/Gemini unreachable) |

On a **cache hit**, `token_usage` is all zeros and `duration_seconds` is sub-millisecond.

### `POST /api/v1/ingestion/ingest`

Upload a GRC framework PDF and ingest it into the vector store.

**Request** (multipart/form-data):
- `file` — PDF document
- `framework_key` — registered framework identifier (e.g. `iso_27001`)

**Response:**
```json
{
  "framework_key": "iso_27001",
  "collection_name": "grc_controls",
  "chunks_created": 342,
  "points_upserted": 342,
  "duration_seconds": 45.2,
  "success": true,
  "error": null
}
```

Re-ingesting the same framework overwrites existing chunks (idempotent).

### `GET /api/v1/cache/stats`

Redis cache metrics.

**Response:**
```json
{
  "enabled": true,
  "connected": true,
  "hits": 15,
  "misses": 8,
  "hit_ratio": 0.6522,
  "keys_count": 8,
  "memory_used_mb": 2.34,
  "memory_max_mb": 1024,
  "memory_pct": 0.2
}
```

### `GET /health` (Gateway — optional)

Aggregated health status of infrastructure services (Qdrant, Embedder, Reranker).

---

## Supported Frameworks

56 frameworks are registered in `src/config/frameworks.json`. To ingest a framework, upload its PDF via the ingestion endpoint using the corresponding key.

<details>
<summary>Full framework list</summary>

| Key | Framework | Version |
|---|---|---|
| **ISO Standards** | | |
| `iso_27001` | ISO/IEC 27001 | 2022 |
| `iso_27002` | ISO/IEC 27002 | 2022 |
| `iso_27005` | ISO/IEC 27005 | 2022 |
| `iso_27017` | ISO/IEC 27017 | 2015 |
| `iso_27018` | ISO/IEC 27018 | 2019 |
| `iso_27035` | ISO/IEC 27035 | 2023 |
| `iso_27701` | ISO/IEC 27701 | 2019 |
| `iso_20000` | ISO/IEC 20000 | 2018 |
| `iso_22301` | ISO 22301 | 2019 |
| `iso_31000` | ISO 31000 | 2018 |
| `iso_42001` | ISO/IEC 42001 | 2023 |
| **NIST** | | |
| `nist_800_53` | NIST SP 800-53 | Rev. 5 |
| `nist_csf_v2` | NIST Cybersecurity Framework | 2.0 |
| `nist_800_171` | NIST SP 800-171 | Rev. 3 |
| `nist_800_82` | NIST SP 800-82 | Rev. 3 |
| `nist_ai_rmf` | NIST AI Risk Management Framework | 1.0 |
| `nist_privacy` | NIST Privacy Framework | 1.0 |
| `nist_ssdf` | NIST SSDF | 1.1 |
| **Industry** | | |
| `pci_dss_v4` | PCI DSS | 4.0 |
| `soc2_tsc` | SOC 2 Trust Services Criteria | 2017 (w/ 2022 revisions) |
| `hipaa` | HIPAA Security Rule | 2013 Omnibus |
| `hitrust_csf` | HITRUST CSF | 11.x |
| `cis_controls_v8` | CIS Controls | 8.0 |
| `cmmc_v2` | CMMC | 2.0 |
| `fedramp` | FedRAMP | Rev. 5 |
| **Privacy** | | |
| `gdpr` | EU GDPR | 2016/679 |
| `ccpa_cpra` | CCPA / CPRA | 2023 |
| `pdpa_singapore` | PDPA Singapore | 2012 (amended 2021) |
| `pipeda` | PIPEDA | 2000 (amended) |
| `lgpd` | LGPD (Brazil) | 2018 |
| `popia` | POPIA (South Africa) | 2013 |
| **Governance** | | |
| `cobit_2019` | COBIT | 2019 |
| `itil_v4` | ITIL | 4 |
| `mitre_attack` | MITRE ATT&CK Enterprise | v15 |
| `owasp_top10` | OWASP Top 10 | 2021 |
| `asvs` | OWASP ASVS | 4.0.3 |
| **Sector-Specific** | | |
| `glba` | GLBA | 1999 (amended) |
| `sox_it` | SOX IT Controls | 2002 |
| `dora` | DORA (EU) | 2023 |
| `nerc_cip` | NERC CIP | v5+ |
| `nis2` | NIS2 Directive | 2022 |
| `swift_cscf` | SWIFT CSCF | 2024 |
| `nydfs_500` | NYDFS 23 NYCRR 500 | 2023 |
| `mas_trmg` | MAS TRM Guidelines | 2021 |
| `cps_234` | APRA CPS 234 | 2019 |
| `isa_62443` | ISA/IEC 62443 | 2018 |
| `tisax` | TISAX | VDA ISA 6.0 |
| `csa_ccm_v4` | CSA CCM | 4.0 |
| `enisa_guidelines` | ENISA Guidelines | 2024 |
| `eu_ai_act` | EU AI Act | 2024 |

</details>

---

## Project Structure

```
GRC_module/
├── docker-compose.yml          # Qdrant, Redis, API
├── docker-compose.qdrant.yml   # Standalone Qdrant (dev)
├── requirements.txt
├── .env                        # All configuration (GRC_* prefix)
│
├── src/
│   ├── api/                    # FastAPI application
│   │   ├── main.py             # App entrypoint
│   │   ├── Dockerfile          # API container image
│   │   ├── schemas.py          # Request/response re-exports
│   │   └── routes/
│   │       ├── query.py        # POST /api/v1/query
│   │       ├── ingestion.py    # POST /api/v1/ingestion/ingest
│   │       └── cache.py        # GET  /api/v1/cache/stats
│   │
│   ├── config/                 # Settings & framework registry
│   │   ├── settings.py         # Pydantic Settings (AppSettings)
│   │   ├── frameworks.json     # Framework metadata (56 frameworks)
│   │   ├── registry.py         # Framework lookup helpers
│   │   └── genai_client.py     # Shared Gemini client singleton
│   │
│   ├── ingestion/              # PDF → vector store pipeline
│   │   ├── pipeline.py         # Orchestrator
│   │   ├── extractor.py        # PDF → Markdown (pymupdf4llm)
│   │   ├── chunker.py          # Markdown → chunks (heading-aware)
│   │   ├── embedder.py         # Chunks → vectors (Gemini)
│   │   ├── qdrant_loader.py    # Vectors → Qdrant upsert
│   │   └── storage.py          # PDF file management
│   │
│   ├── retrieval/              # Finding → control mappings pipeline
│   │   ├── pipeline.py         # Orchestrator (cache + parallel CVSS)
│   │   ├── qdrant_retriever.py # Vector search (per-framework)
│   │   ├── mapper.py           # Gemini structured mapping
│   │   ├── critic.py           # Adversarial validation
│   │   ├── cache.py            # Redis client (get/set/lock/evict)
│   │   ├── normalizer.py       # Query normalization + cache keys
│   │   └── models.py           # Domain models (QueryResponse, TokenUsage)
│   │
│   ├── scoring/                # CVSS 3.1 vulnerability scoring
│   │   ├── classifier.py       # LLM-based metric classification (Gemini)
│   │   ├── engine.py           # Vector assembly + score computation
│   │   └── models.py           # CVSSClassification, CVSSResult
│   │
│   └── gateway/                # Health-check aggregator (optional)
│       ├── main.py
│       ├── routes.py
│       ├── clients.py
│       └── Dockerfile
│
├── data/pdfs/                  # Framework PDFs (local storage)
├── docs/                       # Design documents
└── tests/                      # Test suite
    └── test_cvss_scoring.py    # CVSS engine + live LLM tests
```

---

## Retrieval Pipeline

The query pipeline runs 5 stages with parallel CVSS scoring, and Redis caching at the boundary:

```
  Query arrives
       │
  ┌────▼────┐
  │  Redis  │── HIT ──► Return cached response (0 tokens, <1ms)
  │  Cache  │
  └────┬────┘
       │ MISS
       ▼
  1. Embed finding            (Gemini embedding-001)
  2. Search Qdrant            (per-framework, top-K)
  3. Rerank                   (optional: Jina API or TEI cross-encoder)
       │
  ┌────▼────────────────────────────────────────┐
  │         ThreadPoolExecutor (parallel)        │
  │                                              │
  │  ┌─────────────────┐  ┌──────────────────┐  │
  │  │ Per-framework:   │  │ CVSS Classifier  │  │
  │  │ 4. Map controls  │  │ (Gemini → metrics│  │
  │  │ 5. Critic review │  │  → engine score) │  │
  │  └─────────────────┘  └──────────────────┘  │
  └──────────────┬──────────────────────────────┘
                 │
            ┌────▼────┐
            │  Redis  │── Store response for future hits
            │  Cache  │
            └─────────┘
```

The CVSS classifier runs in a **separate thread**, in parallel with framework mapping/critique. This means CVSS scoring adds zero latency — it completes while the mapper and critic are already running.

**Cache behavior:**
- Deterministic key: `SHA-256(normalized_finding + sorted_frameworks + model + collection)`
- Query normalization: lowercase, trim, collapse whitespace, strip filler prefixes
- No TTL — eviction is memory-pressure driven (LFU)
- Stampede protection via atomic locks (fail-open, never blocks)
- Redis failure is always transparent — pipeline runs normally without cache

---

## Redis Cache

- **What's cached**: the full `QueryResponse` (all mappings, CVSS result, citations, scores)
- **When**: after the pipeline completes successfully; only the lock holder writes
- **Eviction**: application-level LFU at 80% memory → evicts bottom 30% by access frequency
- **Safety net**: Redis server configured with `allkeys-lfu` policy

Check cache status:
```bash
curl http://localhost:8001/api/v1/cache/stats
```

Inspect Redis directly:
```bash
docker exec grc_module-redis-1 redis-cli KEYS "grc:*"
docker exec grc_module-redis-1 redis-cli HGETALL "grc:stats"
```

Disable caching:
```env
GRC_REDIS__ENABLED=false
```

---

## Configuration Reference

All settings use `GRC_` prefix with `__` as nested delimiter in `.env`.

| Group | Key Variables | Description |
|---|---|---|
| **Qdrant** | `GRC_QDRANT__URL`, `COLLECTION_NAME`, `DISTANCE` | Vector DB connection |
| **Gemini** | `GRC_GEMINI__API_KEY`, `PARSE_MODEL`, `EMBEDDING_MODEL`, `WINDOW_SIZE` | LLM + embedding + CVSS |
| **Embedding** | `GRC_EMBEDDING__DIMENSION`, `BATCH_SIZE` | Embedding configuration |
| **Chunking** | `GRC_CHUNKING__SIZE`, `OVERLAP` | Text splitting params |
| **Storage** | `GRC_STORAGE__BACKEND`, `LOCAL_PDF_DIR`, `DELETE_PDF_AFTER_INGESTION` | PDF file handling |
| **Retrieval** | `GRC_RETRIEVAL__USE_RERANKER`, `LIMIT`, `CRITIC_CONFIDENCE_THRESHOLD` | Pipeline behavior |
| **Reranker** | `GRC_RERANKER__BACKEND` (`jina`/`tei`), `THRESHOLD`, `JINA_API_KEY` | Cross-encoder config |
| **Redis** | `GRC_REDIS__URL`, `ENABLED`, `MAX_MEMORY_MB`, `EVICTION_TRIGGER_PCT` | Cache layer |

---

## Dependencies

| Package | Purpose |
|---|---|
| `google-genai>=1.67.0` | Gemini API (embedding, mapping, CVSS classification) |
| `qdrant-client>=1.12.0,<1.14.0` | Vector database client |
| `pydantic>=2.12.0` | Data validation & structured output schemas |
| `pydantic-settings>=2.13.0` | Environment-based configuration |
| `fastapi>=0.135.0` | Web framework |
| `uvicorn>=0.41.0` | ASGI server |
| `pymupdf4llm>=1.27.0` | PDF → Markdown extraction |
| `langchain-text-splitters>=1.1.0` | Heading-aware text chunking |
| `httpx>=0.28.0` | HTTP client (reranker, health checks) |
| `cvss>=3.2,<4.0` | CVSS 3.1 score computation |
| `redis>=5.0.0,<6.0.0` | Redis cache client |

---

## Docker Compose Commands

```bash
# Start all services (Qdrant + Redis + API)
docker compose up -d

# Rebuild and start (no cache)
docker compose build --no-cache && docker compose up -d

# Start standalone Qdrant only (dev)
docker compose -f docker-compose.qdrant.yml up -d

# Check service health
docker compose ps

# View logs
docker compose logs -f api
docker compose logs -f qdrant

# Stop (data preserved)
docker compose down

# Stop and delete all data (volumes removed)
docker compose down -v

# Restart a single service
docker compose restart redis
```

---

## Testing

### CVSS Scoring Tests

```bash
# Engine tests only (deterministic, no API calls)
python tests/test_cvss_scoring.py

# Engine + live LLM classifier tests
python tests/test_cvss_scoring.py --live
```

**Engine test groups** (deterministic):
- Severity thresholds — FIRST spec §5 boundary values
- Vector assembly — CVSS vector string construction
- Score computation — score + severity for known vectors
- Pydantic validation — rejects invalid metric values
- Result model — CVSSResult field validation

**Live LLM test cases** (with `--live`):
- DNS Zone Transfer (AXFR) — expects High, AV:N
- Default admin credentials — expects Critical, AV:N
- SQL Injection in login form — expects Critical, AV:N
- Unencrypted USB drive with PII — expects Medium, AV:P

---

## Development

```bash
# Activate venv
venv\Scripts\Activate.ps1      # Windows
# source venv/bin/activate     # Linux/macOS

# Start API with auto-reload
uvicorn src.api.main:app --reload --port 8001

# Run tests
python -m pytest tests/

# Swagger docs
# http://localhost:8001/docs
```
