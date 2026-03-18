# GRC Compliance Module

AI-powered compliance mapping engine that ingests GRC framework PDFs and maps security findings to relevant controls using RAG (Retrieval-Augmented Generation).

Upload a framework PDF → the system extracts, chunks, and embeds it into a vector store. Then query any security finding → get back structured control mappings with citations, confidence scores, and adversarial validation.

---

## Architecture

```
                         ┌─────────────────────────┐
                         │     FastAPI (/api/v1)    │
                         │    uvicorn :8000 (dev)   │
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
     │ Upsert         │  │ 4. Map        │
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
| Reranker | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | 8082 | Cross-encoder reranking |
| Redis | `redis:7-alpine` | 6379 | Query response cache (LFU eviction) |
| Gateway | Built from `src/gateway/Dockerfile` | 8000 | Health-check aggregator |

**External APIs**: Gemini (embedding + LLM), Jina Reranker (optional cloud reranker)

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
GRC_EMBEDDING__TEI_URL=http://localhost:8081

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

This starts Qdrant, Reranker, Redis, and the Gateway. Check health:

```bash
curl http://localhost:8000/health
```

### 4. Start the API

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
    "finding_text": "MySQL remote login successful",
    "target_frameworks": ["iso_27001"]
  }'
```

---

## API Reference

Base path: `/api/v1`

### `POST /api/v1/query`

Map a security finding to compliance framework controls.

**Request:**
```json
{
  "finding_text": "MySQL remote login successful",
  "target_frameworks": ["iso_27001", "iso_27002"]
}
```

**Response:**
```json
{
  "finding_text": "MySQL remote login successful",
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
  "chunks_after_rerank": 10,
  "duration_seconds": 3.42,
  "token_usage": {
    "mapper_prompt_tokens": 2401,
    "mapper_total_tokens": 3838,
    "critic_prompt_tokens": 0,
    "critic_total_tokens": 0,
    "critic_skipped": true,
    "total_tokens": 3838
  }
}
```

| Status | Meaning |
|---|---|
| `200` | Mappings returned (may be empty if no relevant controls found) |
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

### `GET /health` (Gateway)

Aggregated health status of infrastructure services (Qdrant, Embedder, Reranker).

---

## Supported Frameworks

| Key | Framework | Version |
|---|---|---|
| `iso_27001` | ISO/IEC 27001 | 2022 |
| `iso_27002` | ISO/IEC 27002 | 2022 |
| `nist_800_53` | NIST SP 800-53 | Rev. 5 |
| `pci_dss_v4` | PCI DSS | 4.0 |
| `nist_csf_v2` | NIST Cybersecurity Framework | 2.0 |
| `soc2_tsc` | SOC 2 Trust Services Criteria | 2017 (w/ 2022 revisions) |
| `gdpr` | EU GDPR | 2016/679 |
| `hipaa` | HIPAA Security Rule | 2013 Omnibus |
| `cis_controls_v8` | CIS Controls | 8.0 |
| `iso_22301` | ISO 22301 | 2019 |
| `iso_31000` | ISO 31000 | 2018 |
| `nist_800_171` | NIST SP 800-171 | Rev. 3 |
| `mitre_attack` | MITRE ATT&CK Enterprise | v15 |
| `owasp_top10` | OWASP Top 10 | 2021 |
| `fedramp` | FedRAMP | Rev. 5 |
| `cmmc_v2` | CMMC | 2.0 |
| `cobit_2019` | COBIT | 2019 |
| `itil_v4` | ITIL | 4 |
| `iso_27017` | ISO/IEC 27017 | 2015 |
| `iso_27018` | ISO/IEC 27018 | 2019 |
| `iso_27701` | ISO/IEC 27701 | 2019 |

To ingest a framework, upload its PDF via the ingestion endpoint using the corresponding key.

---

## Project Structure

```
GRC_module/
├── docker-compose.yml          # Qdrant, Reranker, Redis, Gateway
├── requirements.txt
├── .env                        # All configuration (GRC_* prefix)
│
├── src/
│   ├── api/                    # FastAPI application
│   │   ├── main.py             # App entrypoint
│   │   ├── schemas.py          # Request/response models
│   │   └── routes/
│   │       ├── query.py        # POST /api/v1/query
│   │       ├── ingestion.py    # POST /api/v1/ingestion/ingest
│   │       └── cache.py        # GET  /api/v1/cache/stats
│   │
│   ├── config/                 # Settings & framework registry
│   │   ├── settings.py         # Pydantic Settings (AppSettings)
│   │   ├── frameworks.json     # Framework metadata (20 frameworks)
│   │   ├── registry.py         # Framework lookup helpers
│   │   └── genai_client.py     # Shared Gemini client singleton
│   │
│   ├── ingestion/              # PDF → vector store pipeline
│   │   ├── pipeline.py         # Orchestrator
│   │   ├── extractor.py        # PDF → Markdown (pymupdf)
│   │   ├── chunker.py          # Markdown → chunks (heading-aware)
│   │   ├── embedder.py         # Chunks → vectors (Gemini)
│   │   ├── qdrant_loader.py    # Vectors → Qdrant upsert
│   │   └── storage.py          # PDF file management
│   │
│   ├── retrieval/              # Finding → control mappings pipeline
│   │   ├── pipeline.py         # Orchestrator (with Redis cache)
│   │   ├── qdrant_retriever.py # Vector search (per-framework)
│   │   ├── reranker.py         # Cross-encoder reranking (TEI/Jina)
│   │   ├── mapper.py           # Gemini structured mapping
│   │   ├── critic.py           # Adversarial validation
│   │   ├── cache.py            # Redis client (get/set/lock/evict)
│   │   ├── normalizer.py       # Query normalization + cache keys
│   │   └── models.py           # Domain models
│   │
│   └── gateway/                # Health-check aggregator
│       ├── main.py
│       ├── routes.py
│       ├── clients.py
│       └── Dockerfile
│
├── data/pdfs/                  # Framework PDFs (local storage)
├── docs/                       # Design documents
└── tests/                      # Test suite
```

---

## Retrieval Pipeline

The query pipeline runs 5 stages, with Redis caching at the boundary:

```
  Query arrives
       │
  ┌────▼────┐
  │  Redis  │── HIT ──► Return cached response (0 tokens, <1ms)
  │  Cache  │
  └────┬────┘
       │ MISS
       ▼
  1. Embed finding          (Gemini embedding-001)
  2. Search Qdrant          (per-framework, top-K)
  3. Rerank                 (optional: Jina API or TEI cross-encoder)
  4. Map to controls        (Gemini — structured JSON output)
  5. Adversarial critique   (Gemini — validates citations + logic)
       │
  ┌────▼────┐
  │  Redis  │── Store response for future hits
  │  Cache  │
  └─────────┘
```

**Cache behavior:**
- Deterministic key: `SHA-256(normalized_finding + sorted_frameworks + model + collection)`
- Query normalization: lowercase, trim, collapse whitespace, strip filler prefixes
- No TTL — eviction is memory-pressure driven (LFU)
- Stampede protection via atomic locks (fail-open, never blocks)
- Redis failure is always transparent — pipeline runs normally without cache

---

## Redis Cache

- **What's cached**: the full `QueryResponse` (all mappings, citations, scores)
- **When**: after the pipeline completes successfully; only the lock holder writes
- **Eviction**: application-level LFU at 80% memory → evicts bottom 30% by access frequency
- **Safety net**: Redis server configured with `allkeys-lfu` policy

Check cache status:
```bash
curl http://localhost:8001/api/v1/cache/stats
```

Inspect Redis directly:
```bash
docker exec grc-redis redis-cli KEYS "grc:*"
docker exec grc-redis redis-cli HGETALL "grc:stats"
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
| **Gemini** | `GRC_GEMINI__API_KEY`, `PARSE_MODEL`, `EMBEDDING_MODEL` | LLM + embedding |
| **Chunking** | `GRC_CHUNKING__SIZE`, `OVERLAP` | Text splitting params |
| **Retrieval** | `GRC_RETRIEVAL__USE_RERANKER`, `LIMIT`, `CRITIC_CONFIDENCE_THRESHOLD` | Pipeline behavior |
| **Reranker** | `GRC_RERANKER__BACKEND` (`jina`/`tei`), `THRESHOLD` | Cross-encoder config |
| **Redis** | `GRC_REDIS__URL`, `ENABLED`, `MAX_MEMORY_MB` | Cache layer |
| **Storage** | `GRC_STORAGE__BACKEND`, `LOCAL_PDF_DIR` | PDF file handling |

---

## Docker Compose Commands

```bash
# Start all services
docker compose up -d

# Start specific service
docker compose up redis -d

# Check service health
docker compose ps

# View logs
docker compose logs -f redis
docker compose logs -f qdrant

# Stop (data preserved)
docker compose down

# Stop and delete all data (volumes removed)
docker compose down -v

# Restart a single service
docker compose restart redis
```

---

## Development

```bash
# Activate venv
venv\Scripts\Activate.ps1

# Start API with auto-reload
uvicorn src.api.main:app --reload --port 8001

# Run tests
python -m pytest tests/

# Swagger docs
# http://localhost:8001/docs
```
