# Agentic GRC Compliance Mapping System — Implementation Plan

> **Version:** 3.1  
> **Date:** March 14, 2026  
> **Status:** Approved for Engineering  
> **Classification:** Internal — Engineering Blueprint

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Decisions](#2-architecture-decisions)
3. [Technology Stack](#3-technology-stack)
4. [System Architecture](#4-system-architecture)
5. [Phase 1 — Ingestion Pipeline](#5-phase-1--ingestion-pipeline)
6. [Phase 2 — API & Async Processing](#6-phase-2--api--async-processing)
7. [Phase 3 — Agent Pipeline](#7-phase-3--agent-pipeline)
8. [Phase 4 — Storage & Handoff](#8-phase-4--storage--handoff)
9. [Enterprise Requirements](#9-enterprise-requirements)
10. [Infrastructure & Deployment](#10-infrastructure--deployment)
11. [Testing Strategy](#11-testing-strategy)
12. [Implementation Roadmap](#12-implementation-roadmap)
13. [Risk Register](#13-risk-register)
14. [Target KPIs](#14-target-kpis)
15. [Project Structure](#15-project-structure)

---

## 1. Executive Summary

An enterprise-grade, asynchronous AI system that automatically maps technical security findings (e.g., "MySQL port 3306 open") to relevant GRC framework controls across ISO 27001, PCI-DSS, NIST 800-53, SOC 2, and more.

**Core characteristics:**

- **~99% accuracy** via adversarial Critic Agent with citation grounding
- **Sub-3-second latency** per finding (p95)
- **Minimal LLM cost** — single LLM call per finding regardless of framework count, multi-layer caching, open-source embeddings and reranker
- **Per-framework isolation** — one Qdrant vector collection per GRC framework; independent retrieval, independent reranking, unified output
- **Horizontally scalable** — FastAPI + Celery workers auto-scale with queue depth

---

## 2. Architecture Decisions

| Decision | Rationale |
|---|---|
| **One Qdrant collection per framework** | Clean isolation, trivial re-ingestion (drop + recreate), no cross-framework noise, natural parallel fan-out |
| **Per-framework reranking → single LLM call** | Each framework gets fair representation in reranking; combined into one LLM call to keep cost constant |
| **Document-native metadata over hardcoded taxonomy** | Metadata extracted from the document itself (framework_category, domain, control_id, title); no hardcoded threat categories that create a ceiling on recall |
| **Recursive markdown-aware chunking** | Controls chunked following document structure; large chunks (>512 tokens) split via RecursiveCharacterTextSplitter with markdown separators; no forced chunk types |
| **No pre-filter on vector search** | Per-framework collections are small (~200-500 chunks); unfiltered hybrid search + reranker is fast and avoids silent exclusion from mistagging |
| **4 agents (no taxonomy classifier)** | With no threat categories, the finding embedding goes directly to fan-out retrieval; removes a dependency and saves ~10ms |
| **Cross-encoder threshold (>0.85) over Micro-LLM filter** | Zero API cost, lower latency, equivalent accuracy |
| **Open-source embeddings (bge-large-en-v1.5)** | Top MTEB rank, free, private, locally hosted via HF TEI |
| **Local reranker (bge-reranker-v2-m3)** | Zero API cost, ~50ms per framework, hosted via HF TEI |
| **No graph database** | Each framework is queried directly via fan-out; cross-framework relationships are unnecessary when all target frameworks are searched independently |
| **LangGraph over CrewAI/AutoGen** | Most control over state, Python-native, composable, production-ready |
| **Qdrant over Pinecone/Weaviate** | Rust performance, native hybrid search, self-hosted, batch API |
| **Celery over Kafka** | Simpler for task queues, sufficient scale; Kafka reserved for future event streaming |

---

## 3. Technology Stack

### Core Infrastructure

| Layer | Technology | Purpose |
|---|---|---|
| API Gateway | **FastAPI** | Async REST API, request validation, rate limiting |
| Task Queue | **Celery + Redis** (broker) | Distributed async task processing |
| Vector Database | **Qdrant** | One collection per framework; hybrid search, payload filtering |
| Relational Database | **PostgreSQL** | State, cache persistence, metadata, audit logs |
| In-Memory Cache | **Redis** | Multi-layer caching (retrieval, reranking, final output) |
| Containerization | **Docker + Docker Compose** | All services containerized with pre-baked models |
| Orchestration | **Kubernetes** | Production scaling, health checks, rolling deployments |

### AI & ML

| Layer | Technology | Purpose |
|---|---|---|
| Agent Framework | **LangGraph** (Python) | Stateful multi-agent workflow orchestration |
| Document Parsing | **Docling** (Dockerized) | Deep-learning PDF extraction → Markdown |
| Primary LLM | **GPT-4o** or **Claude 3.5 Sonnet** | Mapper and Critic agents (with Prompt Caching) |
| Embeddings | **bge-large-en-v1.5** (local) | Open-source, hosted via HF TEI |
| Reranker | **bge-reranker-v2-m3** (local) | Cross-encoder, hosted via HF TEI |
| Observability | **LangSmith** | Agent trace logging, prompt debugging |
| Metrics | **Prometheus + Grafana** | System metrics, dashboards, alerting |

### Enterprise & Security

| Layer | Technology | Purpose |
|---|---|---|
| Authentication | **OAuth2 / OIDC** (Keycloak or Auth0) | Identity management, token-based auth |
| Secrets | **HashiCorp Vault** | API keys, DB credentials, encryption keys |
| Encryption | **TLS 1.3** (transit) + **AES-256** (rest) | Data protection |
| Logging | **ELK Stack** or **Loki** | Centralized structured logging |
| CI/CD | **GitHub Actions** or **GitLab CI** | Automated build, test, deploy |

---

## 4. System Architecture

```
                    ┌──────────────────────────────────────────────────┐
                    │              ENTERPRISE BOUNDARY                  │
                    │                                                   │
  Security          │  ┌────────────┐     ┌──────────┐                │
  Tools    ────────▶│  │ API Gateway │────▶│ Auth/RBAC│                │
  (Tenable,         │  │ (FastAPI)   │     │ (OAuth2) │                │
   Qualys,          │  └─────┬──────┘     └──────────┘                │
   AWS SH)          │        │                                         │
                    │        ▼                                         │
                    │  ┌─────────────┐     ┌───────┐                  │
                    │  │  Cache Check │────▶│ Redis │                  │
                    │  │  (L1 + L2)  │     │  L1   │                  │
                    │  └─────┬───────┘     └───────┘                  │
                    │        │ (cache miss)                            │
                    │        ▼                                         │
                    │  ┌─────────────┐                                │
                    │  │ Celery Queue │                                │
                    │  │ (Redis)      │                                │
                    │  └─────┬───────┘                                │
                    │        │                                         │
                    │        ▼                                         │
                    │  ┌─────────────────────────────────────────┐    │
                    │  │        LangGraph Worker                  │    │
                    │  │                                           │    │
                    │  │  ┌──────────────┐                        │    │
                    │  │  │ 1. Fan-Out    │  Parallel per-framework│    │
                    │  │  │    Retriever  │  Qdrant queries        │    │
                    │  │  └──────┬───────┘                        │    │
                    │  │         │                                 │    │
                    │  │    ┌────┼────┐                            │    │
                    │  │    ▼    ▼    ▼                            │    │
                    │  │  ┌───┐┌───┐┌───┐  Qdrant Collections     │    │
                    │  │  │ISO││PCI││NST│  (1 per framework)      │    │
                    │  │  └─┬─┘└─┬─┘└─┬─┘                        │    │
                    │  │    │    │    │                            │    │
                    │  │    ▼    ▼    ▼                            │    │
                    │  │  ┌──────────────┐                        │    │
                    │  │  │ 2. Reranker   │  Independent per       │    │
                    │  │  │    (Local)    │  framework, >0.85      │    │
                    │  │  └──────┬───────┘                        │    │
                    │  │         │                                 │    │
                    │  │         ▼  (combine survivors)            │    │
                    │  │  ┌──────────────┐                        │    │
                    │  │  │ 3. Mapper LLM │  Single call, all     │    │
                    │  │  │   (GPT-4o /   │  frameworks in prompt  │    │
                    │  │  │    Claude)    │                        │    │
                    │  │  └──────┬───────┘                        │    │
                    │  │         │                                 │    │
                    │  │         ▼                                 │    │
                    │  │  ┌──────────────┐                        │    │
                    │  │  │ 4. Critic     │  Adversarial QA       │    │
                    │  │  │    Agent      │  per mapping           │    │
                    │  │  └──────┬───────┘                        │    │
                    │  │         │                                 │    │
                    │  └─────────┼─────────────────────────────────┘    │
                    │            ▼                                      │
                    │  ┌──────────────────────────────┐               │
                    │  │  PostgreSQL                    │               │
                    │  │  (Storage + Cache + Audit)     │               │
                    │  └──────────────────────────────┘               │
                    │                                                   │
                    │  ┌──────────────────────────────┐               │
                    │  │  LangSmith │ Prometheus │ Grafana             │
                    │  └──────────────────────────────┘               │
                    └──────────────────────────────────────────────────┘
```

---

## 5. Phase 1 — Ingestion Pipeline

> Offline process. Executed when adding or updating a GRC framework.

### 5.1 Document Extraction

```
Framework PDF → Docling Container → Structured Markdown
```

- Dockerized Docling with **pre-baked HuggingFace models** (downloaded during `docker build`)
- Outputs clean Markdown preserving tables, headers, hierarchy
- Supports: ISO 27001, PCI-DSS, NIST 800-53, SOC 2, HIPAA, custom policies

### 5.2 Hierarchical Structure Extraction

Parse Markdown into structured JSON:

```json
{
  "framework": "ISO 27001",
  "framework_version": "2022",
  "domain": "Network Security",
  "control_id": "8.20",
  "title": "Network Security",
  "description": "...",
  "implementation_guidance": "...",
  "examples": "..."
}
```

### 5.3 Document-Native Chunking

Each control is chunked following the **document's own structure** — no forced chunk types, no hardcoded categories.

**Strategy:**

1. **Parse** the structured JSON from Phase 5.2 into one text block per control (concatenating title, description, implementation guidance, examples, etc.)
2. **If the control text ≤ 512 tokens** → single chunk as-is
3. **If the control text > 512 tokens** → split using `RecursiveCharacterTextSplitter` with markdown-aware separators:

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=50,
    separators=["\n## ", "\n### ", "\n#### ", "\n\n", "\n", " "],
    length_function=token_counter,  # tiktoken or HF tokenizer
)
```

**Why markdown-aware:** GRC documents use heading hierarchies (##, ###) to delineate sections. Splitting at natural heading boundaries preserves semantic coherence far better than fixed-size character splits.

**Metadata** extracted from document structure (not hardcoded):

```json
{
  "framework": "ISO 27001",
  "framework_version": "2022",
  "framework_category": "Technology Controls",
  "domain": "Network Security",
  "control_id": "8.20",
  "title": "Network Security",
  "chunk_index": 0
}
```

- `framework_category` — sourced from a lightweight framework → category mapping config (e.g., ISO 27001 Annex A groups, PCI-DSS requirement groups, NIST control families). This is document-native — derived from the framework's own structure, not an external taxonomy.
- `domain`, `control_id`, `title` — parsed directly from the markdown/JSON structure.
- `chunk_index` — 0 for single-chunk controls; 0, 1, 2... for multi-chunk controls.

### 5.4 Embedding & Qdrant Storage (Per-Framework Collections)

Each GRC framework gets its **own Qdrant collection** (e.g., `iso27001`, `pci_dss_v4`, `nist_800_53`).

- Embed via **bge-large-en-v1.5** (local HF TEI)
- Upsert to framework's dedicated collection
- **Payload Indexes** per collection: `domain`, `control_id`, `framework_category`

Benefits:
- Re-ingestion = drop + recreate collection
- No cross-framework noise
- Independent sizing per collection
- Clean parallel fan-out at query time

### 5.5 PostgreSQL Metadata

- `frameworks` — ID, name, version, qdrant_collection_name, ingestion date, status
- `ingestion_audit_log` — timestamp, framework, chunks_created, status

### 5.6 Framework Versioning

1. Create new Qdrant collection (e.g., `iso27001_v2025`)
2. Ingest new version
3. Swap active collection name in `frameworks` table
4. Optionally retain old collection for audit/rollback, or drop it

---

## 6. Phase 2 — API & Async Processing

### 6.1 API Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/mappings` | Submit finding(s) — single or batch up to 5,000 |
| GET | `/api/v1/mappings/{batch_id}` | Poll batch status |
| GET | `/api/v1/mappings/{batch_id}/results` | Retrieve results |
| POST | `/api/v1/frameworks/ingest` | Trigger ingestion (admin) |
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/metrics` | Prometheus metrics |

### 6.2 Request Payload

```json
{
  "findings": [
    {
      "id": "finding-001",
      "text": "MySQL port 3306 is open to 0.0.0.0/0",
      "asset_type": "database",
      "source": "tenable",
      "target_frameworks": ["ISO27001", "PCI-DSS", "NIST800-53"]
    }
  ],
  "options": {
    "confidence_threshold": 60,
    "include_citations": true
  }
}
```

### 6.3 Response (202 Accepted)

```json
{
  "batch_id": "batch-uuid-here",
  "status": "queued",
  "total_findings": 1,
  "poll_url": "/api/v1/mappings/batch-uuid-here"
}
```

### 6.4 Request Handling

- **Validation:** Pydantic schema validation; input sanitization
- **Rate limiting:** Per-tenant, default 100 req/min, burst 500, max 5,000 findings/batch
- **Cache check:** L1 Redis (TTL: 24h) → L2 PostgreSQL (persistent)
- **Cache key:** `SHA256(finding_text + asset_type + sorted(target_frameworks) + tenant_id)`
- **Cache hit** with confidence ≥ threshold → return immediately, skip queue

### 6.5 Queue Dispatch

- Celery via Redis broker
- Each finding = independent task
- Batch tracking in PostgreSQL (`batch_jobs` table)
- Priority queues: `critical`, `normal`, `bulk`
- **Dead-letter queue:** Failed tasks (after 3 retries with exponential backoff) → DLQ → alert + manual review

---

## 7. Phase 3 — Agent Pipeline

> The intelligent core. **4 agents** orchestrated via LangGraph.

### Overview

```
Finding + target_frameworks
  │
  ▼
Agent 1: Fan-Out Retriever ────────── Parallel Qdrant queries (1 per framework)
  │
  ├── iso27001    → top 10 chunks
  ├── pci_dss_v4  → top 10 chunks
  └── nist_800_53 → top 10 chunks
  │
  ▼
Agent 2: Per-Framework Reranker ──── Independent reranking, threshold > 0.85
  │
  ├── ISO survivors
  ├── PCI survivors
  └── NIST survivors
  │
  ▼
  Combine survivors (labeled by framework)
  │
  ▼
Agent 3: Compliance Mapper ────────── Single LLM call, framework-sectioned prompt
  │
  ▼
Agent 4: Adversarial Critic ───────── Validates each mapping independently
  │
  ▼
Final JSON output
```

---

### Agent 1: Fan-Out Retriever

Queries each target framework's Qdrant collection **in parallel**:

```
Finding embedding → parallel fan-out:
  ├── qdrant.search(collection="iso27001")    → top 10
  ├── qdrant.search(collection="pci_dss_v4")  → top 10
  └── qdrant.search(collection="nist_800_53") → top 10
```

- **Same embedding** for all collections (finding embedded once via bge-large-en-v1.5)
- **No pre-filter** — per-framework collections are small enough (~200-500 chunks) that unfiltered hybrid search is fast and avoids silent exclusion
- **Top 10 chunks per collection** via hybrid search (dense + sparse)
- Collections return independently — no cross-framework interference

**Redis cache:** `(finding_hash + collection_name)` → result, 1-hour TTL.

---

### Agent 2: Per-Framework Reranker

Each framework's results reranked **independently**:

```
ISO chunks  + finding → bge-reranker-v2-m3 → drop < 0.85 → ISO survivors
PCI chunks  + finding → bge-reranker-v2-m3 → drop < 0.85 → PCI survivors
NIST chunks + finding → bge-reranker-v2-m3 → drop < 0.85 → NIST survivors
```

- Local **bge-reranker-v2-m3** via HF TEI
- **Independent thresholding** — a strong ISO match cannot push out a valid PCI match
- No fixed "Top K" — all chunks above 0.85 survive
- Reranking calls parallelized across frameworks

**Redis cache:** `(finding_hash + collection_name + retrieval_hash)` → result, 1-hour TTL.

---

### Agent 3: Compliance Mapper

**Single LLM call** with framework-sectioned prompt:

```
Finding: "MySQL port 3306 is open to 0.0.0.0/0"

=== ISO 27001 Controls (Reranked) ===
[Control 8.20 - Network Security - score 0.94]
[Control 8.22 - Segregation of networks - score 0.88]

=== PCI-DSS v4.0 Controls (Reranked) ===
[Req 1.2.1 - Restrict traffic - score 0.91]
[Req 1.3.1 - Inbound traffic restricted - score 0.87]

=== NIST 800-53 Controls (Reranked) ===
[AC-4 - Information Flow Enforcement - score 0.89]
[SC-7 - Boundary Protection - score 0.86]

Generate mappings for EACH framework separately.
```

**Why single call:** 3 frameworks = 1 call. 5 frameworks = still 1 call. 1,000 findings × 5 frameworks = 1,000 LLM calls (not 5,000).

**Context window:** 6 frameworks × 3 chunks × 500 tokens = ~9K tokens — well within limits.

**Prompt Caching:** System instructions cached via Anthropic's `cache_control` or OpenAI's automatic caching. Saves 50-90% on input tokens.

**Output:**

```json
{
  "finding_id": "finding-001",
  "finding_text": "MySQL port 3306 is open to 0.0.0.0/0",
  "mappings": [
    {
      "framework": "ISO 27001",
      "framework_version": "2022",
      "domain": "Network Security",
      "control_id": "8.20",
      "control_title": "Networks security",
      "risk_mitigated": "Unrestricted network access to database services",
      "citation": "Network services shall be identified, implemented and protected.",
      "citation_source": "ISO 27001:2022 Clause 8.20, Implementation Guidance",
      "confidence_score": 92
    },
    {
      "framework": "PCI-DSS",
      "framework_version": "4.0",
      "domain": "Build and Maintain a Secure Network",
      "control_id": "1.2.1",
      "control_title": "Restrict inbound and outbound traffic",
      "risk_mitigated": "Unauthorized access to cardholder data environment via open database port",
      "citation": "Restrict inbound and outbound traffic to that which is necessary...",
      "citation_source": "PCI-DSS v4.0 Requirement 1.2.1",
      "confidence_score": 95
    }
  ]
}
```

---

### Agent 4: Adversarial Critic

Single-pass automated QA. **No loops.**

**Checks per mapping:**

| Check | Criteria | On Failure |
|---|---|---|
| Citation Grounding | Citation must exist verbatim in source chunk | `FAILED` |
| Logical Soundness | Control must plausibly mitigate the finding | `FAILED` |
| Confidence Threshold | Score ≥ configured threshold (default 60) | `FAILED` |
| Schema Validity | All required fields present and correctly typed | `FAILED` |

**Failed mapping → overwritten:**

```json
{
  "framework": "ISO 27001",
  "status": "FAILED",
  "reason": "Citation not grounded in source text",
  "original_confidence": 45
}
```

**Passed mapping → unchanged with `status: "APPROVED"`.**

---

## 8. Phase 4 — Storage & Handoff

### 8.1 Write to PostgreSQL

**Table: `finding_mappings`**

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | Multi-tenant isolation |
| `batch_id` | UUID | Batch job reference |
| `finding_id` | VARCHAR | External finding ID |
| `finding_text` | TEXT | Original finding text |
| `finding_hash` | VARCHAR(64) | SHA-256 cache key |
| `mapped_payload` | JSONB | Full mapping result |
| `overall_confidence` | INTEGER | Average confidence |
| `status` | ENUM | `completed`, `partial`, `failed` |
| `processing_time_ms` | INTEGER | End-to-end latency |
| `created_at` | TIMESTAMP | Record creation time |
| `created_by` | VARCHAR | Service account reference |

### 8.2 Update Caches

1. **Redis L1:** `finding_hash → result_json` (TTL: 24h)
2. **PostgreSQL L2:** Upsert into `finding_mapping_cache` (persistent)

### 8.3 Batch Completion

- Update `batch_jobs` per-finding status
- All findings done → batch `completed`
- Fire webhook notification (if configured)
- Emit Prometheus metric

### 8.4 Audit Trail

```json
{
  "timestamp": "2026-03-14T10:30:00Z",
  "tenant_id": "tenant-abc",
  "action": "finding_mapped",
  "finding_id": "finding-001",
  "agent_trace_id": "langsmith-trace-xyz",
  "frameworks_mapped": ["ISO27001", "PCI-DSS"],
  "confidence_scores": [92, 95],
  "cache_hit": false,
  "processing_time_ms": 2340
}
```

---

## 9. Enterprise Requirements

### 9.1 Multi-Tenancy

- All tables include `tenant_id`
- API keys scoped to tenants
- Qdrant collections are shared (frameworks are global); isolation at results/storage layer
- Redis keys prefixed with `tenant:{id}:`

### 9.2 Authentication & Authorization

- **OAuth2 / OIDC** via Keycloak or Auth0
- API key auth for service-to-service
- RBAC roles:

| Role | Permissions |
|---|---|
| `viewer` | Read results |
| `analyst` | Submit findings, read results |
| `admin` | Ingest frameworks, manage tenants, view audit logs |
| `service` | Automated system-to-system integration |

### 9.3 Encryption & Secrets

- **Transit:** TLS 1.3 for all service communication
- **At rest:** AES-256 for PostgreSQL
- **Secrets:** HashiCorp Vault for all credentials
- **PII:** Finding text may contain asset names — classify and mask in logs

### 9.4 Network Security

- All services in private VPC/network
- Only FastAPI gateway exposed via load balancer
- Internal services via private DNS
- mTLS between critical services

### 9.5 High Availability

| Component | HA Strategy |
|---|---|
| FastAPI | 3+ replicas behind load balancer |
| Celery Workers | Auto-scaling (3 min, 20 max) |
| PostgreSQL | Primary-replica with automatic failover |
| Redis | Sentinel or Cluster (3 nodes) |
| Qdrant | Distributed mode, replication factor 2 |
| HF TEI | 2+ replicas |

### 9.6 Disaster Recovery

- PostgreSQL: Daily backups, 30-day retention, point-in-time recovery
- Qdrant: Daily snapshots to object storage
- **RPO:** 1 hour | **RTO:** 4 hours

### 9.7 Rate Limiting & Circuit Breakers

- Per-tenant limits (default: 100 req/min, 5,000 findings/batch)
- LLM rate limit awareness with queue backpressure
- Circuit breaker on LLM APIs: error rate > 50% for 60s → open circuit → degraded response

---

## 10. Infrastructure & Deployment

### 10.1 Docker Services

```yaml
services:
  # Application
  api-gateway:           # FastAPI (3 replicas)
  celery-worker:         # LangGraph workers (auto-scaled)
  celery-beat:           # Periodic task scheduler

  # Data
  postgresql:            # Primary relational DB
  qdrant:                # Vector DB (one collection per framework)
  redis:                 # Cache + message broker

  # ML Models (Local)
  tei-embeddings:        # bge-large-en-v1.5 via HF TEI
  tei-reranker:          # bge-reranker-v2-m3 via HF TEI
  docling:               # PDF extraction (pre-baked models)

  # Observability
  prometheus:            # Metrics collection
  grafana:               # Dashboards

  # Security
  keycloak:              # OAuth2/OIDC
  vault:                 # Secrets management
```

### 10.2 Resource Allocation

| Service | CPU | RAM | GPU | Storage |
|---|---|---|---|---|
| FastAPI | 2 cores | 2 GB | — | — |
| Celery Worker (each) | 2 cores | 4 GB | — | — |
| PostgreSQL | 4 cores | 8 GB | — | 100 GB SSD |
| Qdrant | 4 cores | 16 GB | — | 50 GB SSD |
| Redis | 2 cores | 4 GB | — | — |
| TEI Embeddings | 2 cores | 4 GB | Optional | 5 GB |
| TEI Reranker | 2 cores | 4 GB | Optional | 5 GB |
| Docling | 2 cores | 4 GB | Optional | 10 GB |

### 10.3 CI/CD Pipeline

```
Code Push → Lint/Type Check → Unit Tests → Build Docker Images
  → Integration Tests (docker-compose) → Security Scan (Trivy/Snyk)
  → Push to Registry → Deploy to Staging → Smoke Tests
  → Manual Approval → Blue/Green Deploy to Production
```

### 10.4 Environments

| Environment | Purpose | LLM Provider |
|---|---|---|
| `local` | Developer (docker-compose) | Mock LLM / Ollama |
| `staging` | Pre-production | Real LLM, low rate limits |
| `production` | Live traffic | Real LLM, production limits |

---

## 11. Testing Strategy

### 11.1 Unit Tests (>80% coverage)

- Agent logic, schema validation
- Pydantic models, cache key generation
- Utility functions (hashing, sanitization)

### 11.2 Integration Tests

- Full pipeline: finding → cache → LangGraph → storage → retrieval
- Per-framework Qdrant collection search
- Redis cache hit/miss
- Celery task lifecycle
- Run via docker-compose in CI

### 11.3 Accuracy Tests (Golden Dataset)

200+ expert-reviewed finding-to-mapping pairs:

| Test | Criteria |
|---|---|
| Mapping correctness | Control is relevant to finding |
| Citation grounding | Citation exists in source document |
| Framework coverage | All target frameworks produce results |
| False positive rate | No hallucinated control IDs |
| Confidence calibration | High-confidence = correct; low = uncertain |

Run on every PR touching agent logic or prompts. Target: **>97% accuracy**.

### 11.4 Load Tests

- Tool: Locust or k6
- 1,000 concurrent findings
- Target: p95 < 3s (single), p95 < 60s (1,000-finding batch)

### 11.5 Chaos Tests

- Kill Celery worker mid-task → verify retry
- Redis outage → verify graceful degradation
- LLM timeout → verify circuit breaker
- Qdrant unavailable → verify error propagation

---

## 12. Implementation Roadmap

### Sprint 1 (Weeks 1-2): Foundation

| # | Task | Deliverable |
|---|---|---|
| 1 | Project scaffolding — FastAPI, Poetry/uv, Ruff, pre-commit | Repo with CI |
| 2 | Docker Compose — PostgreSQL, Redis, Qdrant, TEI containers | All services boot |
| 3 | Docling container — pre-baked HF models | PDF extraction works |
| 4 | HF TEI containers — embeddings + reranker | HTTP endpoints respond |
| 5 | PostgreSQL schemas — Alembic migrations | Tables ready |

### Sprint 2 (Weeks 3-4): Ingestion

| # | Task | Deliverable |
|---|---|---|
| 6 | Docling extraction script | Processes ISO 27001 PDF |
| 7 | Structure parser (Markdown → JSON) | Hierarchical JSON output |
| 8 | Document-native chunker + RecursiveCharacterTextSplitter (markdown-aware, 512 tokens) | Chunks with document-native metadata |
| 9 | Framework → category mapping config | `framework_categories.yaml` |
| 10 | Qdrant ingestion (per-framework collections) | Collections with indexed data |
| 11 | PostgreSQL metadata storage | Framework metadata populated |
| 12 | Framework versioning (collection swap) | Version update works |
| 13 | Ingest 3 frameworks (ISO, PCI-DSS, NIST) | Knowledge base ready |

### Sprint 3 (Weeks 5-6): Agent Pipeline

| # | Task | Deliverable |
|---|---|---|
| 14 | Fan-out retriever (Agent 1) | Top chunks per collection |
| 15 | Per-framework reranker (Agent 2) | Survivors per framework |
| 16 | Mapper agent (Agent 3) — single LLM call | Unified mapping JSON |
| 17 | Critic agent (Agent 4) | Approved/failed mappings |
| 18 | LangGraph wiring (fan-out/fan-in) | End-to-end pipeline works |
| 19 | Unit + integration tests | Suite passing |

### Sprint 4 (Weeks 7-8): API & Caching

| # | Task | Deliverable |
|---|---|---|
| 20 | FastAPI endpoints with Pydantic validation | API responds |
| 21 | Celery task setup with retry logic | Tasks process |
| 22 | Redis multi-layer cache (L1) | Cache hit in <200ms |
| 23 | PostgreSQL L2 cache | Persistent cache |
| 24 | Batch processing + webhooks | 1,000-finding batch completes |
| 25 | Dead-letter queue | Failed tasks captured |
| 26 | Priority queues | Routing works |

### Sprint 5 (Weeks 9-10): Security & Auth

| # | Task | Deliverable |
|---|---|---|
| 27 | OAuth2/OIDC (Keycloak) | Authenticated calls |
| 28 | RBAC | Unauthorized blocked |
| 29 | Multi-tenancy | Tenant isolation |
| 30 | Input sanitization | Injection prevented |
| 31 | Vault integration | No hardcoded secrets |
| 32 | Audit logging | Trail queryable |
| 33 | TLS + network config | Encrypted comms |

### Sprint 6 (Weeks 11-12): Observability & Hardening

| # | Task | Deliverable |
|---|---|---|
| 34 | LangSmith integration | Agent traces visible |
| 35 | Prometheus metrics | Metrics endpoint |
| 36 | Grafana dashboards (ops, accuracy, cost) | 3 dashboards |
| 37 | Alerting rules | Alerts fire |
| 38 | Golden dataset (200+ pairs) | Test data ready |
| 39 | Accuracy test suite | >97% verified |
| 40 | Load testing (Locust/k6) | p95 < 3s |
| 41 | Chaos testing | Degradation verified |

### Sprint 7 (Weeks 13-14): Production

| # | Task | Deliverable |
|---|---|---|
| 42 | Kubernetes manifests | K8s configs |
| 43 | CI/CD pipeline | End-to-end |
| 44 | Blue/green deployment | Zero-downtime |
| 45 | Staging environment | Staging verified |
| 46 | Production deployment | System live |
| 47 | Runbooks | Documented |
| 48 | Ingest production frameworks | Knowledge base complete |

---

## 13. Risk Register

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | LLM API outage | Medium | Circuit breaker + cached fallback + multi-provider support |
| 2 | Reranker accuracy drift | Low | Golden dataset monitoring; quarterly model updates |
| 3 | Docling fails on complex PDFs | Medium | Manual review step; alternative parsers (PyMuPDF, Marker) |
| 4 | Qdrant scaling (many collections) | Low | Distributed mode; payload index optimization |
| 5 | Prompt injection via finding text | Medium | Input sanitization; Critic validates output; structured parsing |
| 6 | High LLM costs at scale | Medium | Prompt caching, multi-layer caching, open-source models |
| 7 | Per-framework reranking latency | Low | Parallelized calls; local TEI ~50ms per framework |

---

## 14. Target KPIs

| Metric | Target | Measurement |
|---|---|---|
| Mapping accuracy | ≥ 97% | Golden dataset |
| Citation grounding rate | ≥ 99% | Critic pass rate |
| Single finding latency (p95) | < 3 seconds | Prometheus |
| 1,000-finding batch (p95) | < 60 seconds | Load test |
| Cache hit rate (steady state) | > 60% | Redis metrics |
| LLM cost per finding | < $0.02 | Token tracking |
| System uptime | 99.9% | Prometheus/Grafana |
| Critic rejection rate | < 10% | LangSmith |
| Mean time to recovery | < 4 hours | Incident tracking |

---

## 15. Project Structure

```
grc-mapping-system/
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.worker
├── Dockerfile.docling
├── pyproject.toml
├── alembic/                          # DB migrations
├── src/
│   ├── api/
│   │   ├── main.py                   # FastAPI app
│   │   ├── routes/
│   │   │   ├── mappings.py           # Finding mapping endpoints
│   │   │   ├── frameworks.py         # Ingestion endpoints
│   │   │   └── health.py             # Health + metrics
│   │   ├── middleware/
│   │   │   ├── auth.py               # OAuth2/JWT
│   │   │   ├── rate_limit.py         # Rate limiting
│   │   │   └── tenant.py             # Tenant context
│   │   └── schemas/                  # Pydantic models
│   ├── agents/
│   │   ├── graph.py                  # LangGraph workflow
│   │   ├── fanout_retriever.py       # Agent 1
│   │   ├── reranker.py               # Agent 2
│   │   ├── mapper.py                 # Agent 3
│   │   └── critic.py                 # Agent 4
│   ├── ingestion/
│   │   ├── docling_extractor.py      # PDF extraction
│   │   ├── structure_parser.py       # Markdown → JSON
│   │   ├── chunker.py                # Document-native chunking + RecursiveCharacterTextSplitter
│   │   ├── qdrant_loader.py          # Per-framework ingestion
│   │   └── versioning.py             # Collection management
│   ├── cache/
│   │   ├── redis_cache.py            # L1 cache
│   │   └── pg_cache.py               # L2 cache
│   ├── db/
│   │   ├── models.py                 # SQLAlchemy models
│   │   └── session.py                # DB sessions
│   ├── workers/
│   │   ├── celery_app.py             # Celery config
│   │   └── tasks.py                  # Task definitions
│   └── config/
│       ├── settings.py               # Pydantic settings
│       └── framework_categories.yaml # Framework → category mappings
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── accuracy/
│   │   └── golden_dataset.json
│   └── load/
├── k8s/                              # Kubernetes manifests
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/dashboards/
└── docs/
```

---

*This document is the authoritative source for all implementation work.*
