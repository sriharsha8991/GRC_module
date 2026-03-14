# Final Implementation Plan: Enterprise-Grade Agentic GRC Compliance Mapping System

> **Version:** 2.0  
> **Date:** March 14, 2026  
> **Status:** Approved for Engineering  
> **Classification:** Internal — Engineering Blueprint

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Cross-Document Analysis & Design Decisions](#2-cross-document-analysis--design-decisions)
3. [Final Technology Stack](#3-final-technology-stack)
4. [Enterprise Architecture Overview](#4-enterprise-architecture-overview)
5. [Phase 1 — Ingestion & Knowledge Pipeline](#5-phase-1--ingestion--knowledge-pipeline)
6. [Phase 2 — Online API & Async Processing](#6-phase-2--online-api--async-processing)
7. [Phase 3 — LangGraph Multi-Agent Workflow](#7-phase-3--langgraph-multi-agent-workflow)
8. [Phase 4 — Storage, Cache & Handoff](#8-phase-4--storage-cache--handoff)
9. [Enterprise-Grade Requirements](#9-enterprise-grade-requirements)
10. [Infrastructure & Deployment](#10-infrastructure--deployment)
11. [Testing Strategy](#11-testing-strategy)
12. [Step-by-Step Implementation Roadmap](#12-step-by-step-implementation-roadmap)
13. [Risk Register & Mitigations](#13-risk-register--mitigations)
14. [Target KPIs](#14-target-kpis)

---

## 1. Executive Summary

This document is the **single source of truth** for building the Agentic GRC Compliance Mapping System. It consolidates insights from all prior iterations (initial notes, first draft architecture, second update optimizations, and the optimized ingestion strategy) into one enterprise-ready implementation plan.

**What the system does:** Automatically maps technical security findings (e.g., "MySQL port 3306 open") to relevant GRC framework controls across ISO 27001, PCI-DSS, NIST 800-53, SOC 2, and more — with ~99% accuracy, sub-3-second latency, and minimal LLM cost.

**Key design principles adopted from all iterations:**

| Decision | Source | Rationale |
|---|---|---|
| LangGraph multi-agent pipeline | First Draft | Stateful, composable orchestration for complex reasoning |
| One Qdrant collection per framework + parallel fan-out | Architecture Review | Clean isolation, trivial re-ingestion, independent scaling per framework |
| Per-framework reranking → single LLM call | Architecture Review | Each framework gets fair representation; single LLM call keeps cost constant regardless of framework count |
| Taxonomy-based classification over LLM decomposition | Second Update | Deterministic, 100x faster, consistent; uses semantic routing for flexibility |
| Cross-encoder threshold replaces Micro-LLM filter | Second Update | Zero API cost, lower latency, equivalent accuracy at score > 0.85 |
| Open-source embeddings (bge-large-en-v1.5) | Second Update | Free, private, top MTEB performance, locally hosted |
| Multi-layer Redis caching | Second Update | Cache retrieval, reranking, and final output; cuts repeat latency to ~200ms |
| Multi-chunk semantic ingestion | Ingestion Strategy | Separate summary/guidance/examples chunks improve retrieval precision by 20-30% |
| Threat category tagging at ingest time | Ingestion Strategy | Enables filtered retrieval, reduces noise |
| Observability (LangSmith + Prometheus + Grafana) | Second Update | Mandatory for production debugging, cost tracking, accuracy monitoring |

---

## 2. Cross-Document Analysis & Design Decisions

### What was kept from the First Draft
- FastAPI + Celery + Redis async architecture
- Qdrant as the primary vector database
- PostgreSQL for state, cache, and structured storage
- Docling for deep-learning PDF extraction (Dockerized with pre-baked models)
- Adversarial Critic Agent for automated QA (no Human-in-the-Loop)
- Finding hash-based persistent cache
- Prompt caching for LLM cost optimization
- Local reranker via Hugging Face TEI (bge-reranker-v2-m3)

### What was upgraded from the Second Update
- **Replaced:** Single Qdrant collection → One collection per framework with parallel fan-out retrieval
- **Added:** Per-framework independent reranking → combined context → single LLM call
- **Replaced:** LLM-based query decomposition (Agent 1) → Deterministic taxonomy classification with semantic routing
- **Removed:** Micro-LLM YES/NO filter (Agent 3.5) → Cross-encoder threshold (score > 0.85) handles this
- **Replaced:** OpenAI text-embedding-3-large → Open-source bge-large-en-v1.5 (local, free, private)
- **Added:** Multi-layer Redis caching (retrieval + reranking + final output)
- **Added:** Full observability stack (LangSmith, Prometheus, Grafana)

### What was adopted from the Ingestion Strategy
- Hierarchical structure extraction (Domain → Control → Description/Guidance/Examples)
- Multi-chunk semantic strategy per control (3 chunk types instead of 1)
- Threat category tagging during ingestion
- Comprehensive payload indexing in Qdrant (6+ indexed fields per collection)
- Framework versioning: drop and re-create the collection (clean, simple)

### What was added for Enterprise readiness
- Multi-tenancy with tenant isolation
- RBAC and OAuth2/OIDC authentication
- End-to-end encryption (at-rest + in-transit)
- Audit logging and compliance trails
- High availability, horizontal scaling, and disaster recovery
- CI/CD pipeline with blue/green deployments
- API versioning and rate limiting
- Circuit breakers and dead-letter queues
- Secrets management via HashiCorp Vault
- Comprehensive testing strategy (unit, integration, load, chaos)

---

## 3. Final Technology Stack

### Core Infrastructure

| Layer | Technology | Purpose |
|---|---|---|
| API Gateway | **FastAPI** | Async REST API, request validation, rate limiting |
| Task Queue | **Celery + Redis** (broker) | Distributed async task processing |
| Streaming (future) | **Kafka** | Event-driven architecture for high-throughput ingestion |
| Vector Database | **Qdrant** | One collection per framework; hybrid search (dense + sparse), payload filtering |
| Relational Database | **PostgreSQL** | State management, cache persistence, metadata, audit logs |
| In-Memory Cache | **Redis** | Multi-layer caching (retrieval, reranking, final output) |
| Containerization | **Docker + Docker Compose** | All services containerized with pre-baked models |
| Orchestration | **Kubernetes** | Production scaling, health checks, rolling deployments |

### AI & ML

| Layer | Technology | Purpose |
|---|---|---|
| Agent Framework | **LangGraph** (Python) | Stateful multi-agent workflow orchestration |
| Document Parsing | **Docling** (Dockerized) | Deep-learning PDF layout/table extraction → Markdown |
| Primary LLM | **GPT-4o** or **Claude 3.5 Sonnet** | Mapper and Critic agents (with Prompt Caching) |
| Embeddings | **bge-large-en-v1.5** (local) | Open-source, hosted via HF TEI Docker container |
| Reranker | **bge-reranker-v2-m3** (local) | Cross-encoder, hosted via HF TEI Docker container |
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

## 4. Enterprise Architecture Overview

```
                        ┌──────────────────────────────────────────────┐
                        │           ENTERPRISE BOUNDARY                │
                        │                                              │
  External              │   ┌──────────────┐    ┌──────────────┐      │
  Security    ─────────▶│   │  API Gateway  │───▶│  Auth/RBAC   │      │
  Tools                 │   │  (FastAPI)    │    │  (OAuth2)    │      │
  (Tenable,             │   └──────┬───────┘    └──────────────┘      │
   Qualys,              │          │                                    │
   AWS SH)              │          ▼                                    │
                        │   ┌──────────────┐    ┌──────────────┐      │
                        │   │ Request Hash  │───▶│   Redis      │      │
                        │   │ + Cache Check │    │  (L1 Cache)  │      │
                        │   └──────┬───────┘    └──────────────┘      │
                        │          │                                    │
                        │          ▼                                    │
                        │   ┌──────────────┐                           │
                        │   │  Celery Queue │                           │
                        │   │  (Redis       │                           │
                        │   │   Broker)     │                           │
                        │   └──────┬───────┘                           │
                        │          │                                    │
                        │          ▼                                    │
                        │   ┌──────────────────────────────────┐      │
                        │   │   LangGraph Worker Orchestrator   │      │
                        │   │                                    │      │
                        │   │  ┌────────┐  ┌─────────────┐    │      │
                        │   │  │Taxonomy│  │  Fan-Out     │    │      │
                        │   │  │Classif.│─▶│  Retriever   │    │      │
                        │   │  └────────┘  └──────┬──────┘    │      │
                        │   │                     │            │      │
                        │   │        ┌────────────┼────────┐   │      │
                        │   │        ▼            ▼        ▼   │      │
                        │   │   ┌─────────┐ ┌─────────┐ ┌─────────┐│ │
                        │   │   │ Qdrant  │ │ Qdrant  │ │ Qdrant  ││ │
                        │   │   │ISO27001 │ │ PCI-DSS │ │  NIST   ││ │
                        │   │   └────┬────┘ └────┬────┘ └────┬────┘│ │
                        │   │        │           │           │     │ │
                        │   │        ▼           ▼           ▼     │ │
                        │   │  ┌──────────────────────────────┐│      │
                        │   │  │  Per-Framework Reranker       ││      │
                        │   │  │  (bge-reranker-v2-m3, local)  ││      │
                        │   │  │  Independent threshold > 0.85 ││      │
                        │   │  └──────────────┬───────────────┘│      │
                        │   │             ▼                    │      │
                        │   │  ┌─────────────────────┐         │      │
                        │   │  │  Mapper LLM          │         │      │
                        │   │  │  (GPT-4o / Claude)   │         │      │
                        │   │  └──────────┬──────────┘         │      │
                        │   │             ▼                    │      │
                        │   │  ┌─────────────────────┐         │      │
                        │   │  │  Critic Agent        │         │      │
                        │   │  │  (Adversarial QA)    │         │      │
                        │   │  └──────────┬──────────┘         │      │
                        │   │             │                    │      │
                        │   └─────────────┼────────────────────┘      │
                        │                 ▼                            │
                        │   ┌──────────────────────────────────┐      │
                        │   │  PostgreSQL                       │      │
                        │   │  (Final Storage + Cache + Audit)  │      │
                        │   └──────────────────────────────────┘      │
                        │                                              │
                        │   ┌──────────────────────────────────┐      │
                        │   │  Observability                    │      │
                        │   │  LangSmith │ Prometheus │ Grafana │      │
                        │   └──────────────────────────────────┘      │
                        └──────────────────────────────────────────────┘
```

---

## 5. Phase 1 — Ingestion & Knowledge Pipeline

> Offline process. Executed only when adding or updating a GRC framework.

### Step 1.1: Document Extraction

```
Framework PDF → Docling Container → Structured Markdown
```

- Use Dockerized Docling with **pre-baked HuggingFace models** (downloaded during `docker build`, not at runtime)
- Outputs clean Markdown preserving tables, headers, and hierarchical structure
- Supports: ISO 27001, PCI-DSS, NIST 800-53, SOC 2, HIPAA, custom policies

### Step 1.2: Hierarchical Structure Extraction

Parse the Markdown into structured JSON preserving the document hierarchy:

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

### Step 1.3: Multi-Chunk Semantic Splitting

Each control produces **3 chunk types** (not 1 monolithic chunk):

| Chunk Type | Content | Why |
|---|---|---|
| `control_summary` | Control ID + title + description | Primary semantic match for findings |
| `implementation_guidance` | How-to guidance text | Matches findings that describe missing implementations |
| `examples` | Best practices, configuration examples | Matches specific technical findings |

Each chunk carries full metadata:

```json
{
  "framework": "ISO27001",
  "framework_version": "2022",
  "control_id": "8.20",
  "domain": "Network Security",
  "chunk_type": "implementation_guidance",
  "threat_categories": ["network_security", "service_exposure", "firewall_rules"],
  "is_latest": true
}
```

### Step 1.4: Threat Category Tagging

Map every control to a **predefined security threat taxonomy** during ingestion:

```
Network Security | Access Control | Data Protection | Cryptography
Monitoring | Incident Response | Asset Management | Configuration
Identity | Physical Security | Supply Chain | Business Continuity
```

This enables **filtered vector search** at query time — dramatically reducing noise.

### Step 1.5: Embedding Generation & Qdrant Storage (Per-Framework Collections)

Each GRC framework gets its **own Qdrant collection** (e.g., `iso27001`, `pci_dss_v4`, `nist_800_53`).

- Generate embeddings using **bge-large-en-v1.5** (local HF TEI container)
- Upsert to the framework's dedicated collection with full payload metadata
- Create **Payload Indexes** per collection on: `domain`, `control_id`, `chunk_type`, `threat_category`

**Benefits of separate collections:**
- Framework re-ingestion is trivial — drop and recreate the collection
- No cross-framework noise during vector search
- Each collection can be independently sized and optimized
- Clean parallel fan-out at query time

### Step 1.6: PostgreSQL Metadata Storage

Store in relational tables:
- `frameworks` — ID, name, version, qdrant_collection_name, ingestion date, status
- `ingestion_audit_log` — timestamp, framework, chunks_created, status

### Step 1.7: Framework Versioning

When updating a framework:
1. Create new Qdrant collection (e.g., `iso27001_v2025`)
2. Ingest new version into the new collection
3. Swap the active collection name in `frameworks` table
4. Optionally retain old collection for audit/rollback, or drop it

---

## 6. Phase 2 — Online API & Async Processing

> Handles production traffic without blocking HTTP requests.

### Step 2.1: API Gateway (FastAPI)

**Endpoints:**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/mappings` | Submit single finding or batch (up to 5,000) |
| GET | `/api/v1/mappings/{batch_id}` | Poll batch job status |
| GET | `/api/v1/mappings/{batch_id}/results` | Retrieve completed results |
| POST | `/api/v1/frameworks/ingest` | Trigger framework ingestion (admin) |
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/metrics` | Prometheus metrics endpoint |

**Request payload:**

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

**Response (202 Accepted):**

```json
{
  "batch_id": "batch-uuid-here",
  "status": "queued",
  "total_findings": 1,
  "poll_url": "/api/v1/mappings/batch-uuid-here"
}
```

### Step 2.2: Request Validation & Rate Limiting

- Pydantic schema validation on all inputs
- Rate limiting per tenant (configurable): default 100 req/min, burst 500
- Input sanitization — strip injection vectors from finding text
- Max batch size: 5,000 findings per request

### Step 2.3: Multi-Layer Cache Check

Before queuing, check caches in order:

```
L1: Redis (in-memory)     → finding_hash → full result JSON (TTL: 24h)
L2: PostgreSQL             → finding_hash → full result JSON (persistent)
```

**Cache key computation:**

```python
cache_key = SHA256(finding_text + asset_type + sorted(target_frameworks) + tenant_id)
```

If cache hit AND `confidence_score >= threshold` → return immediately (no queue).

### Step 2.4: Queue Dispatch

- Push uncached findings to **Celery** via Redis broker
- Each finding becomes an independent task
- Batch tracking in PostgreSQL: `batch_jobs` table with status per finding
- Support priority queues: `critical`, `normal`, `bulk`

### Step 2.5: Dead-Letter Queue

Failed tasks (after 3 retries with exponential backoff) routed to DLQ:
- Log failure reason
- Notify via webhook/alert
- Manual review queue for operations team

---

## 7. Phase 3 — LangGraph Multi-Agent Workflow

> The intelligent core — 4 agents with per-framework fan-out retrieval and independent reranking.

### Flow

```
Finding + target_frameworks: ["ISO27001", "PCI-DSS", "NIST"]
  │
  ▼
Agent 1: Taxonomy Classifier (Semantic Routing, ~10ms)
  │
  ▼
Agent 2: Fan-Out Retriever (Parallel queries to per-framework Qdrant collections)
  │
  ├── iso27001 collection → top chunks
  ├── pci_dss collection   → top chunks
  └── nist_800_53 collection → top chunks
  │
  ▼
Agent 3: Per-Framework Reranker (Independent reranking per framework, threshold > 0.85)
  │
  ├── ISO chunks reranked independently → survivors
  ├── PCI chunks reranked independently → survivors
  └── NIST chunks reranked independently → survivors
  │
  ▼
Combine all survivors (labeled by framework) into single context
  │
  ▼
Agent 4: Compliance Mapper (Single LLM call, framework-sectioned prompt, Prompt Cached)
  │
  ▼
Agent 5: Adversarial Critic (Validates each framework mapping independently)
  │
  ▼
Final Output (unified JSON with all frameworks)
```

### Agent 1: Taxonomy Classifier

**Method:** Semantic Routing (NOT LLM-based)

1. Pre-embed the threat taxonomy categories into a separate Qdrant collection (`taxonomy_vectors`)
2. When a finding arrives, embed it and do a fast vector match against taxonomy
3. Return top 2-3 matching threat categories

**Example:**
```
Input:  "MySQL port 3306 is open to 0.0.0.0/0"
Output: ["network_security", "database_exposure", "access_control"]
```

**Performance:** ~10ms (vs 1-2s for LLM decomposition)

**Fallback:** If no taxonomy match scores above 0.7, fall back to a lightweight LLM call for classification.

### Agent 2: Fan-Out Retriever (Per-Framework Collections)

For each target framework, query its **dedicated Qdrant collection** in parallel:

```
Finding embedding → parallel fan-out:
  ├── qdrant.search(collection="iso27001",    filter={threat_category IN [...]}) → top 10
  ├── qdrant.search(collection="pci_dss_v4",  filter={threat_category IN [...]}) → top 10
  └── qdrant.search(collection="nist_800_53", filter={threat_category IN [...]}) → top 10
```

- **Same embedding** used for all collections (no per-framework query variation)
- Filter by `threat_category IN [matched_categories]` from Agent 1
- Retrieve **Top 10 chunks per collection** using hybrid search (dense + sparse)
- Each collection returns results independently — no cross-framework interference

**Cache:** Store each collection's retrieval result in Redis keyed by `(finding_hash + collection_name)` with 1-hour TTL.

### Agent 3: Per-Framework Reranker

Rerank each framework's results **independently** — this is the key design decision:

```
ISO chunks  + finding → rerank → drop score < 0.85 → ISO survivors
PCI chunks  + finding → rerank → drop score < 0.85 → PCI survivors
NIST chunks + finding → rerank → drop score < 0.85 → NIST survivors
```

- Uses local **bge-reranker-v2-m3** (HF TEI microservice)
- **Independent thresholding per framework** — a strong ISO match at 0.95 cannot push out a valid PCI match at 0.86
- Each framework is guaranteed fair representation in the final LLM context
- No fixed "Top K" limit — all chunks above 0.85 per framework survive

**Cache:** Store per-framework reranked results in Redis keyed by `(finding_hash + collection_name + retrieval_hash)` with 1-hour TTL.

### Agent 4: Compliance Mapper (Single LLM Call)

The primary LLM agent that generates structured mapping output. **One LLM call handles all frameworks** — survivors from per-framework reranking are combined into a single, framework-labeled prompt.

**Input structure (framework-sectioned):**

```
Finding: "MySQL port 3306 is open to 0.0.0.0/0"

=== ISO 27001 Controls (Reranked) ===
[Chunk 1: Control 8.20 - Network Security - score 0.94]
[Chunk 2: Control 8.22 - Segregation of networks - score 0.88]

=== PCI-DSS v4.0 Controls (Reranked) ===
[Chunk 1: Req 1.2.1 - Restrict traffic - score 0.91]
[Chunk 2: Req 1.3.1 - Inbound traffic restricted - score 0.87]

=== NIST 800-53 Controls (Reranked) ===
[Chunk 1: AC-4 - Information Flow Enforcement - score 0.89]
[Chunk 2: SC-7 - Boundary Protection - score 0.86]

Generate mappings for EACH framework separately.
```

**Why single LLM call:** 3 frameworks = 1 call. 5 frameworks = still 1 call. A batch of 1,000 findings × 5 frameworks = 1,000 LLM calls (not 5,000). Cost and latency stay constant regardless of framework count.

**Context window math:** Even 6 frameworks × 3 chunks × 500 tokens = ~9K tokens — well within limits.

**Prompt Caching:** System instructions + framework context chunks are cached via provider-specific prompt caching (Anthropic's cache_control or OpenAI's automatic caching). Saves 50-90% on input tokens for repeat patterns.

**Output format:**

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

### Agent 5: Adversarial Critic

Performs strict automated QA on the Mapper's output. **Single pass, no loops.**

**Checks per mapping:**

| Check | Criteria | On Failure |
|---|---|---|
| Citation Grounding | Citation text must exist verbatim in the source chunk | Mark mapping as `FAILED` |
| Logical Soundness | Control must plausibly mitigate the described finding | Mark mapping as `FAILED` |
| Confidence Threshold | Score must be ≥ configured threshold (default 60) | Mark mapping as `FAILED` |
| Schema Validity | All required fields present and correctly typed | Mark mapping as `FAILED` |

**On failure:** The Critic does NOT loop back. It overwrites the failed mapping:

```json
{
  "framework": "ISO 27001",
  "status": "FAILED",
  "reason": "Citation not grounded in source text",
  "original_confidence": 45
}
```

**On success:** Mapping passes through unchanged with `status: "APPROVED"`.

---

## 8. Phase 4 — Storage, Cache & Handoff

### Step 4.1: Write to Backend Database

The Celery worker writes the Critic-approved JSON to PostgreSQL:

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
| `overall_confidence` | INTEGER | Average confidence across mappings |
| `status` | ENUM | `completed`, `partial`, `failed` |
| `processing_time_ms` | INTEGER | End-to-end latency |
| `created_at` | TIMESTAMP | Record creation time |
| `created_by` | VARCHAR | Service account / API key reference |

### Step 4.2: Update Caches

1. **Redis L1 cache:** Set `finding_hash → result_json` with 24h TTL
2. **PostgreSQL L2 cache:** Upsert into `finding_mapping_cache` table (persistent)

### Step 4.3: Batch Job Completion

- Update `batch_jobs` table with per-finding status
- When all findings in a batch are complete → mark batch as `completed`
- Fire webhook notification to the calling system (if configured)
- Emit Prometheus metric for batch completion

### Step 4.4: Audit Trail

Every operation is logged to `audit_log`:

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

## 9. Enterprise-Grade Requirements

### 9.1 Multi-Tenancy

- **Tenant isolation** at the data layer: all tables include `tenant_id` column
- API keys scoped to tenants
- Qdrant: framework collections are shared (frameworks are global); tenant isolation is at the results/storage layer
- Redis: key prefixed with `tenant:{id}:`

### 9.2 Authentication & Authorization

- **OAuth2 / OIDC** via Keycloak (self-hosted) or Auth0
- API key authentication for service-to-service calls
- RBAC roles:

| Role | Permissions |
|---|---|
| `viewer` | Read mapping results |
| `analyst` | Submit findings, read results |
| `admin` | Ingest frameworks, manage tenants, view audit logs |
| `service` | Automated system-to-system integration |

### 9.3 Encryption

- **In transit:** TLS 1.3 for all service-to-service communication
- **At rest:** AES-256 encryption for PostgreSQL (via transparent data encryption or disk-level)
- **Secrets:** All API keys, DB credentials, LLM keys stored in HashiCorp Vault
- **PII handling:** Finding text may contain asset names — classify and mask in logs

### 9.4 Network Security

- All services deployed within a private VPC/network
- Only the FastAPI gateway exposed via load balancer
- Internal services communicate via private DNS
- mTLS between critical services (LangGraph workers ↔ LLM APIs)

### 9.5 High Availability

| Component | HA Strategy |
|---|---|
| FastAPI | 3+ replicas behind load balancer |
| Celery Workers | Auto-scaling (3 min, 20 max based on queue depth) |
| PostgreSQL | Primary-replica with automatic failover |
| Redis | Redis Sentinel or Redis Cluster (3 nodes) |
| Qdrant | Distributed mode with replication factor 2 |
| HF TEI (Reranker/Embeddings) | 2+ replicas |

### 9.6 Disaster Recovery

- PostgreSQL: Daily automated backups, 30-day retention, point-in-time recovery
- Qdrant: Snapshot backups to object storage (daily)
- RPO: 1 hour | RTO: 4 hours
- Runbook documentation for recovery procedures

### 9.7 Rate Limiting & Throttling

- Per-tenant configurable limits
- Default: 100 requests/minute, 5,000 findings/batch
- LLM provider rate limit awareness: queue backpressure when approaching provider limits
- Circuit breaker on LLM APIs: if error rate > 50% for 60s → open circuit, return degraded response

---

## 10. Infrastructure & Deployment

### 10.1 Docker Services

```yaml
services:
  # Core Application
  api-gateway:           # FastAPI (3 replicas)
  celery-worker:         # LangGraph workers (auto-scaled)
  celery-beat:           # Periodic task scheduler

  # Databases
  postgresql:            # Primary relational DB
  qdrant:                # Vector database (one collection per framework)
  redis:                 # Cache + message broker

  # ML Models (Local)
  tei-embeddings:        # bge-large-en-v1.5 via HF TEI
  tei-reranker:          # bge-reranker-v2-m3 via HF TEI
  docling:               # PDF extraction (pre-baked models)

  # Observability
  prometheus:            # Metrics collection
  grafana:               # Dashboards
  # LangSmith is SaaS — no container needed

  # Security
  keycloak:              # OAuth2/OIDC identity provider
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
| TEI Embeddings | 2 cores | 4 GB | Optional | 5 GB (model) |
| TEI Reranker | 2 cores | 4 GB | Optional | 5 GB (model) |
| Docling | 2 cores | 4 GB | Optional | 10 GB (models) |

### 10.3 CI/CD Pipeline

```
Code Push → Lint/Type Check → Unit Tests → Build Docker Images
    → Integration Tests (docker-compose) → Security Scan (Trivy/Snyk)
    → Push to Registry → Deploy to Staging → Smoke Tests
    → Manual Approval Gate → Blue/Green Deploy to Production
```

### 10.4 Environment Strategy

| Environment | Purpose | LLM Provider |
|---|---|---|
| `local` | Developer machine (docker-compose) | Mock LLM / local Ollama |
| `staging` | Pre-production validation | Real LLM with low rate limits |
| `production` | Live traffic | Real LLM with production rate limits |

---

## 11. Testing Strategy

### 11.1 Unit Tests

- Agent logic (taxonomy classifier, cache key generation, schema validation)
- Pydantic models and request validation
- Utility functions (hashing, text sanitization)
- Target: >80% code coverage

### 11.2 Integration Tests

- Full pipeline: finding → cache check → LangGraph → storage → retrieval
- Qdrant ingestion and per-framework collection search
- Redis cache hit/miss scenarios
- Celery task lifecycle
- Run via docker-compose in CI

### 11.3 Accuracy Tests (Golden Dataset)

Maintain a **golden dataset** of 200+ finding-to-mapping pairs reviewed by GRC experts:

| Test | Criteria |
|---|---|
| Mapping correctness | Mapped control is relevant to the finding |
| Citation grounding | Citation exists in source document |
| Cross-framework coverage | If ISO maps, PCI-DSS equivalent also maps |
| False positive rate | No hallucinated control IDs |
| Confidence calibration | High-confidence mappings are correct; low-confidence are uncertain |

Run on every PR that touches agent logic or prompts. Target: **>97% accuracy on golden set**.

### 11.4 Load Tests

- Tool: Locust or k6
- Simulate 1,000 concurrent findings
- Measure: p50, p95, p99 latency; throughput; error rate
- Target: p95 < 3s for single finding, p95 < 60s for 1,000-finding batch

### 11.5 Chaos Tests

- Kill a Celery worker mid-task → verify task retry and completion
- Simulate Redis outage → verify graceful degradation (skip cache, proceed to LangGraph)
- Simulate LLM API timeout → verify circuit breaker activates
- Simulate Qdrant unavailability → verify error propagation

---

## 12. Step-by-Step Implementation Roadmap

### Sprint 1 (Weeks 1-2): Foundation & Infrastructure

| # | Task | Details | Deliverable |
|---|---|---|---|
| 1 | Project scaffolding | Python project with FastAPI, Poetry/uv, pre-commit hooks, linting (Ruff) | Repo with CI running |
| 2 | Docker Compose setup | All services (PostgreSQL, Redis, Qdrant, TEI containers) | `docker-compose.yml` boots cleanly |
| 3 | Docling container | Dockerfile with pre-baked HF models (dummy run in build step) | `docling` container extracts PDFs |
| 4 | HF TEI containers | Embeddings (bge-large-en-v1.5) + Reranker (bge-reranker-v2-m3) | Both containers respond to HTTP |
| 5 | Database schemas | PostgreSQL tables: `frameworks`, `finding_mappings`, `finding_mapping_cache`, `batch_jobs`, `audit_log` | Alembic migrations ready |

### Sprint 2 (Weeks 3-4): Ingestion Pipeline

| # | Task | Details | Deliverable |
|---|---|---|---|
| 6 | Docling extraction script | PDF → structured Markdown | Script processes ISO 27001 PDF |
| 7 | Structure parser | Markdown → hierarchical JSON (domain/control/chunks) | JSON output for ISO 27001 |
| 8 | Multi-chunk splitter | Generate 3 chunk types per control with full metadata | Chunks with metadata JSON |
| 9 | Threat taxonomy definition | Define master taxonomy (12-15 categories) | `taxonomy.json` file |
| 10 | Threat category tagger | Semantic matching to tag each chunk | Tagged chunks |
| 11 | Qdrant ingestion (per-framework collections) | Embed chunks via TEI + upsert to framework-specific Qdrant collection with payload indexes | Per-framework collections with indexed data |
| 12 | PostgreSQL metadata | Store framework metadata (name, version, collection_name) | Tables populated |
| 13 | Framework versioning | Implement collection swap logic (create new → update metadata → optionally drop old) | Version update works cleanly |
| 14 | Ingest 3 frameworks | ISO 27001, PCI-DSS v4.0, NIST 800-53 | Knowledge base populated |

### Sprint 3 (Weeks 5-6): Core Agent Pipeline

| # | Task | Details | Deliverable |
|---|---|---|---|
| 15 | Taxonomy classifier (Agent 1) | Semantic routing via taxonomy embeddings in Qdrant | Classifier returns categories in <20ms |
| 16 | Fan-out retriever | Parallel queries to per-framework Qdrant collections with threat_category filtering | Returns top chunks per framework collection |
| 17 | Per-framework reranker (Agent 3) | Call TEI reranker independently per framework, apply 0.85 threshold | Survivors per framework |
| 18 | Mapper agent (Agent 4) | Single LLM call with framework-sectioned prompt, prompt caching, structured JSON output | Unified mapping JSON per finding |
| 19 | Critic agent (Agent 5) | Adversarial validation per framework mapping, citation grounding, schema check | Approved/failed mappings |
| 20 | LangGraph wiring | Connect all agents into a single LangGraph workflow with fan-out/fan-in | End-to-end finding → mapping |
| 21 | Unit + integration tests | Test each agent individually + full pipeline | Test suite passing |

### Sprint 4 (Weeks 7-8): API, Queue & Caching

| # | Task | Details | Deliverable |
|---|---|---|---|
| 22 | FastAPI endpoints | All 6 endpoints with Pydantic validation | API responds correctly |
| 23 | Celery task setup | Worker config, task definition, retry logic | Tasks process from queue |
| 24 | Redis multi-layer cache | L1 cache for retrieval, reranking, and final results | Cache hit returns in <200ms |
| 25 | PostgreSQL L2 cache | Persistent finding_mapping_cache | Persistent cache working |
| 26 | Batch processing | Batch job tracking, per-finding status, completion webhooks | 1,000-finding batch completes |
| 27 | Dead-letter queue | Failed task routing, alerting, manual review | Failed tasks captured |
| 28 | Priority queues | Critical/normal/bulk queue support | Priority routing works |

### Sprint 5 (Weeks 9-10): Enterprise Security & Auth

| # | Task | Details | Deliverable |
|---|---|---|---|
| 29 | OAuth2/OIDC integration | Keycloak setup, JWT validation middleware | Authenticated API calls |
| 30 | RBAC implementation | Role-based endpoint access (viewer/analyst/admin/service) | Unauthorized requests blocked |
| 31 | Multi-tenancy | tenant_id propagation, data isolation, key prefixing | Tenants can't see each other's data |
| 32 | Input sanitization | Sanitize finding text, prevent injection | Security tests passing |
| 33 | Secrets management | Vault integration for all credentials | No hardcoded secrets |
| 34 | Audit logging | Structured audit log for all operations | Audit trail queryable |
| 35 | TLS + network config | TLS for all internal communication, private networking | Encrypted comms verified |

### Sprint 6 (Weeks 11-12): Observability, Testing & Hardening

| # | Task | Details | Deliverable |
|---|---|---|---|
| 36 | LangSmith integration | Trace all LangGraph agent runs | Agent traces visible in LangSmith |
| 37 | Prometheus metrics | Expose key metrics (latency, cache hit rate, token cost, queue depth) | Metrics endpoint working |
| 38 | Grafana dashboards | Operational dashboard, accuracy dashboard, cost dashboard | 3 dashboards deployed |
| 39 | Alerting rules | Alert on: high error rate, queue backup, LLM cost spike, low accuracy | Alerts fire correctly |
| 40 | Golden dataset creation | 200+ expert-reviewed finding/mapping pairs | Test dataset ready |
| 41 | Accuracy test suite | Automated accuracy testing against golden dataset | >97% accuracy verified |
| 42 | Load testing | 1,000 concurrent findings via Locust/k6 | p95 < 3s confirmed |
| 43 | Chaos testing | Failure injection for all critical components | Graceful degradation verified |

### Sprint 7 (Weeks 13-14): Production Deployment

| # | Task | Details | Deliverable |
|---|---|---|---|
| 44 | Kubernetes manifests | Deployments, services, ingress, HPA, PDB | K8s configs ready |
| 45 | CI/CD pipeline | Full pipeline: test → build → scan → stage → prod | Pipeline runs end-to-end |
| 46 | Blue/green deployment | Zero-downtime deployment strategy | Deployment tested |
| 47 | Staging environment | Full environment running with real LLM | Staging verified |
| 48 | Production deployment | Deploy to production with monitoring | System live |
| 49 | Runbooks | Operational procedures for incidents, scaling, recovery | Documented runbooks |
| 50 | Ingest production frameworks | All target frameworks ingested | Knowledge base complete |

---

## 13. Risk Register & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | LLM API outage (OpenAI/Anthropic) | System cannot process new findings | Medium | Circuit breaker + fallback to cached results + support multiple providers |
| 2 | Reranker model accuracy drift | Relevant chunks filtered out | Low | Monitor via golden dataset tests; retrain/update model quarterly |
| 3 | Docling fails on complex PDFs | Incomplete framework ingestion | Medium | Manual review step; support alternative parsers (PyMuPDF, Marker) |
| 4 | Qdrant scaling limits (many collections) | Slow retrieval at high volume | Low | Qdrant distributed mode; optimize payload indexes; merge small frameworks |
| 5 | Prompt injection via finding text | LLM produces incorrect mapping | Medium | Input sanitization; Critic agent validates output; structured output parsing |
| 6 | High LLM costs at scale | Budget overrun | Medium | Prompt caching, multi-layer caching, open-source models where possible |
| 7 | Per-framework reranking latency | Increased total reranking time | Low | Rerank calls are parallelized; local TEI model is fast (~50ms per framework) |

---

## 14. Target KPIs

| Metric | Target | Measurement |
|---|---|---|
| Mapping accuracy | ≥ 97% | Golden dataset test suite |
| Citation grounding rate | ≥ 99% | Critic agent pass rate |
| Single finding latency (p95) | < 3 seconds | Prometheus histogram |
| Batch 1,000 findings (p95) | < 60 seconds | Load test |
| Cache hit rate (steady state) | > 60% | Redis metrics |
| LLM cost per finding (avg) | < $0.02 | Token tracking |
| System uptime | 99.9% | Prometheus/Grafana |
| Critic rejection rate | < 10% | LangSmith metrics |
| Mean time to recovery | < 4 hours | Incident tracking |

---

## Appendix A: Key File/Module Structure

```
grc-mapping-system/
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.worker
├── Dockerfile.docling
├── pyproject.toml
├── alembic/                        # DB migrations
├── src/
│   ├── api/
│   │   ├── main.py                 # FastAPI app factory
│   │   ├── routes/
│   │   │   ├── mappings.py         # Finding mapping endpoints
│   │   │   ├── frameworks.py       # Framework ingestion endpoints
│   │   │   └── health.py           # Health + metrics
│   │   ├── middleware/
│   │   │   ├── auth.py             # OAuth2/JWT middleware
│   │   │   ├── rate_limit.py       # Rate limiting
│   │   │   └── tenant.py           # Tenant context
│   │   └── schemas/                # Pydantic models
│   ├── agents/
│   │   ├── graph.py                # LangGraph workflow definition
│   │   ├── taxonomy_classifier.py  # Agent 1
│   │   ├── fanout_retriever.py     # Agent 2 (per-framework collection queries)
│   │   ├── reranker.py             # Agent 3 (per-framework independent reranking)
│   │   ├── mapper.py               # Agent 4 (single LLM call, framework-sectioned)
│   │   └── critic.py               # Agent 5
│   ├── ingestion/
│   │   ├── docling_extractor.py    # PDF extraction
│   │   ├── structure_parser.py     # Markdown → JSON
│   │   ├── chunk_splitter.py       # Multi-chunk strategy
│   │   ├── threat_tagger.py        # Taxonomy tagging
│   │   ├── qdrant_loader.py        # Per-framework collection ingestion
│   │   └── versioning.py           # Framework version/collection management
│   ├── cache/
│   │   ├── redis_cache.py          # L1 cache operations
│   │   └── pg_cache.py             # L2 cache operations
│   ├── db/
│   │   ├── models.py               # SQLAlchemy models
│   │   └── session.py              # DB session management
│   ├── workers/
│   │   ├── celery_app.py           # Celery configuration
│   │   └── tasks.py                # Task definitions
│   └── config/
│       ├── settings.py             # Pydantic settings
│       └── taxonomy.json           # Threat taxonomy definition
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── accuracy/
│   │   └── golden_dataset.json     # Expert-reviewed test data
│   └── load/
├── k8s/                            # Kubernetes manifests
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
│       └── dashboards/
└── docs/
```

---

## Appendix B: Decision Log

| Decision | Alternatives Considered | Rationale |
|---|---|---|
| Qdrant over Pinecone/Weaviate | Pinecone (managed, expensive), Weaviate (complex) | Qdrant: Rust performance, native hybrid search, self-hosted, batch API |
| Per-framework collections over single collection | Single collection with payload filter | Clean isolation, trivial re-ingestion, no cross-framework noise, natural parallel fan-out |
| Per-framework reranking over combined reranking | Rerank all frameworks together (simpler, but competitive) | Each framework gets fair representation; strong matches in one framework can't drown out valid matches in another |
| Single LLM call over per-framework LLM calls | Separate LLM call per framework (expensive, slow) | 1 call regardless of framework count; context window easily accommodates 6+ frameworks |
| bge-large-en-v1.5 over OpenAI embeddings | text-embedding-3-large (high quality, API cost) | BGE: top MTEB rank, free, private, locally hosted |
| LangGraph over CrewAI/AutoGen | CrewAI (simpler), AutoGen (Microsoft) | LangGraph: most control over state, Python-native, composable, production-ready |
| Celery over Kafka | Kafka (high throughput, event sourcing) | Celery: simpler for task queues, sufficient scale; Kafka reserved for future event streaming |
| Semantic routing over LLM decomposition | LLM query generation (flexible, slower) | Deterministic, 100x faster, consistent; LLM fallback preserved for edge cases |
| Cross-encoder threshold over Micro-LLM filter | gpt-4o-mini YES/NO filter (flexible) | Zero API cost, lower latency, equivalent accuracy at threshold 0.85 |

---

*This document supersedes all prior drafts. All implementation should reference this plan as the authoritative source.*
