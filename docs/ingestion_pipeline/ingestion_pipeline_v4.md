# Ingestion Pipeline v4 — Single Collection, Gemini Parsing, Per-Framework Retrieval

> **Date:** 2026-03-16
> **Status:** Approved — Ready for Implementation
> **Supersedes:** v3 (per-framework collections + heuristic extractors)

---

## 1. Executive Summary

A single Qdrant collection (`grc_controls`) stores all framework data with `framework` as the **only indexed payload field** (tenant-optimized). **Gemini structured output** replaces all regex heuristic extractors for uniform parsing across 50+ GRC frameworks. At query time, per-framework batch queries retrieve 30 chunks each, a reranker filters dynamically by score (≥ 0.9), and all qualifying chunks across all requested frameworks are collected **before** making a **single LLM call** to generate the full cross-framework compliance report.

### Architecture at a Glance

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           INGESTION (offline)                                │
│                                                                              │
│  PDF ──► PyMuPDF4LLM ──► Markdown ──► Gemini Parser ──► ParsedSections      │
│                              │              │                                │
│                      (specialized parser    │                                │
│                       for ISO 27001)        │                                │
│                                             ▼                                │
│                    RecursiveCharacterTextSplitter (512 tokens, markdown)      │
│                                             │                                │
│                                             ▼                                │
│              Gemini Embedding (gemini-embedding-001, 1536-dim)               │
│              Fallback: TEI Embedder (bge-large-en-v1.5, 1024-dim)           │
│                                             │                                │
│                                             ▼                                │
│                     Qdrant: grc_controls (single collection)                 │
│                     Index: framework (keyword, is_tenant: true)              │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                          RETRIEVAL (runtime)                                 │
│                                                                              │
│  (finding, requested_frameworks[]) ──► Embed finding                         │
│                         (Gemini embedding-001 / TEI fallback)                │
│                                             ▼                                │
│             Qdrant Batch Query (1 HTTP call, N sub-queries)                  │
│             Per framework → top-30 chunks, filtered by framework             │
│                                             │                                │
│                                             ▼                                │
│              Per-Framework Rerank (bge-reranker-v2-m3)                       │
│              Keep chunks where rerank_score ≥ 0.9 (dynamic)                  │
│                                             │                                │
│                                             ▼                                │
│             Collect ALL qualifying chunks from ALL frameworks                │
│                                             │                                │
│                                             ▼                                │
│                  Single LLM Call (Gemini 2.5 Flash / Pro)                     │
│                  Input: finding + all qualifying chunks                      │
│                  Output: cross-framework compliance report                   │
│                  (failure clause mapped per framework)                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### What Changed from v3

| Aspect | v3 | v4 |
|---|---|---|
| Collections | N collections (one per framework) | **Single `grc_controls`** |
| Framework filter | Fan-out across N collections + merge | **Payload filter** (`framework` = tenant) |
| Parsing | 5 regex heuristic extractors + LLM fallback | **Gemini 2.5 Flash structured output** (single path) |
| Embedding | TEI bge-large-en-v1.5 (1024-dim) | **Gemini embedding-001** (1536-dim MRL, asymmetric task_type) |
| Reranking | Not specified | **BGE Reranker v2-m3** via TEI (568M, multilingual, sigmoid scores) |
| Payload | 12+ fields, 4 indexed | **7 fields, 1 indexed** |
| Retrieval | `match.any` mixed across frameworks | **Per-framework** (30 per fw → rerank → dynamic threshold) |
| LLM calls | 1 per framework | **1 total** (all frameworks collected first) |
| SDK | Mixed (openai, anthropic, etc.) | **google-genai** (unified for parsing, embedding, report LLM) |

---

## 2. Qdrant Design Decisions (Research-Backed)

All decisions below are grounded in deep research of Qdrant documentation: [Collections](https://qdrant.tech/documentation/concepts/collections/), [Indexing](https://qdrant.tech/documentation/concepts/indexing/), [Filtering](https://qdrant.tech/documentation/concepts/filtering/), [Hybrid Queries](https://qdrant.tech/documentation/concepts/hybrid-queries/), [Search](https://qdrant.tech/documentation/concepts/search/).

### 2.1 Single Collection over Per-Framework Collections

Qdrant docs explicitly recommend: *"In most cases, you should only use a single collection with payload-based partitioning (multitenancy). Creating numerous collections may result in resource overhead."*

Benefits:
- Shared HNSW graph — single index to maintain, not 50+
- Shared filter optimization in batch queries — Qdrant detects same filter structure and shares intermediate results
- No fan-out logic in application code
- Simpler backup, migration, and monitoring

### 2.2 Tenant Index (`is_tenant: true`)

Available since Qdrant v1.11.0 for keyword/uuid fields. Tells Qdrant to **localize tenant-specific data closer on disk** → fewer disk reads during filtered search. Each framework = a tenant.

```python
client.create_payload_index(
    collection_name="grc_controls",
    field_name="framework",
    field_schema={"type": "keyword", "is_tenant": True},
)
```

This means searching `framework = "iso_27001"` only reads the disk pages where ISO 27001 data lives — not the entire collection.

### 2.3 Filterable HNSW

Qdrant extends the HNSW graph with **extra edges based on indexed payload values**. This is critical for filtered search:
- High-selectivity (weak) filters → HNSW index traversal with filter
- Low-selectivity (strict) filters → payload index + full rescore
- Qdrant's query planner auto-selects the optimal strategy per segment

**Important:** Payload indexes must be created **immediately after collection creation, before any inserts** — otherwise HNSW won't build extra edges for those fields.

### 2.4 Batch Query API

Instead of N sequential HTTP calls, Qdrant's batch query endpoint accepts all N queries in a single request:

```
POST /collections/grc_controls/points/query/batch
```

Qdrant optimizes internally — shared filter evaluation, single network round-trip.

### 2.5 Future-Ready for Hybrid Search

The single collection design supports adding **named sparse vectors** later (SPLADE/BM25) alongside the dense vector:
- Dense + sparse in same collection via named vectors
- Reciprocal Rank Fusion (RRF) or Distribution-Based Score Fusion (DBSF) to merge results
- Weighted RRF (v1.17.0) allows boosting semantic over keyword or vice versa
- **No collection re-creation needed** — add sparse vector as a new named vector

---

## 3. Model Stack

All model choices are driven by the GRC compliance domain: dense regulatory text, precise control-level semantics, and multi-jurisdictional (multilingual) framework coverage.

### 3.1 SDK: `google-genai`

All Gemini interactions use the **unified `google-genai` SDK** (not the legacy `google-generativeai` package).

```bash
pip install google-genai
```

```python
from google import genai
from google.genai import types

client = genai.Client()  # Reads GEMINI_API_KEY from environment
```

### 3.2 Parsing Model: Gemini 2.5 Flash

| Property | Value |
|---|---|
| Model ID | `gemini-2.5-flash` |
| Role | Structured extraction of controls/sections from framework markdown |
| Why this model | Best price-performance ratio. Supports structured output (JSON schema / Pydantic). 1M token context window handles entire framework documents. |
| Alternatives considered | `gemini-2.5-pro` (more capable but 5× cost, unnecessary for extraction), `gemini-3-flash-preview` (latest preview but not yet stable) |

**Structured output pattern:**
```python
from pydantic import BaseModel

class ParsedSection(BaseModel):
    section_id: str
    title: str
    body: str
    domain: str

class FrameworkSections(BaseModel):
    sections: list[ParsedSection]

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=markdown_window,
    config={
        "response_mime_type": "application/json",
        "response_json_schema": FrameworkSections.model_json_schema(),
    },
)
```

### 3.3 Primary Embedder: Gemini `gemini-embedding-001`

| Property | Value |
|---|---|
| Model ID | `gemini-embedding-001` |
| Native dimension | 3072 |
| Selected dimension | **1536** (via Matryoshka Representation Learning) |
| Distance metric | Cosine |
| Task types | `RETRIEVAL_DOCUMENT` (ingestion), `RETRIEVAL_QUERY` (search) |
| Batch limit | 100 texts per API call |

**Why 1536 dimensions (not 3072 or 768):**

Gemini `embedding-001` supports Matryoshka Representation Learning (MRL) — the model is trained so that truncating the vector to a smaller prefix retains most of the semantic quality. MTEB benchmark scores:

| Dimension | MTEB Score | Relative to 3072 |
|---|---|---|
| 768 | 67.99 | −0.25% |
| 1536 | 68.17 | +0.01% |
| 3072 | 68.16 | baseline |

1536 dimensions deliver **100% of full quality** (actually slightly better than 3072 on MTEB) at **2× less storage and faster HNSW search**. The sweet spot: highest benchmark score with half the memory footprint of full-dimension vectors.

**Asymmetric search with task_type:**

Gemini embeddings support `task_type` to optimize for asymmetric retrieval:
- `RETRIEVAL_DOCUMENT` — used at **ingestion time** for control/section chunks. Optimizes the embedding for being **found**.
- `RETRIEVAL_QUERY` — used at **query time** for the finding text. Optimizes the embedding for **finding** relevant documents.

This is a significant advantage over symmetric models like bge-large-en-v1.5 where the same embedding is used for both documents and queries.

**Embedding API:**
```python
# Ingestion (documents)
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=["Control text here..."],
    config=types.EmbedContentConfig(
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=1536,
    ),
)
vectors = [e.values for e in result.embeddings]

# Query (finding)
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=["Server XYZ allows admin access without MFA"],
    config=types.EmbedContentConfig(
        task_type="RETRIEVAL_QUERY",
        output_dimensionality=1536,
    ),
)
query_vector = result.embeddings[0].values
```

### 3.4 Fallback Embedder: TEI `bge-large-en-v1.5`

| Property | Value |
|---|---|
| Model ID | `BAAI/bge-large-en-v1.5` |
| Dimension | 1024 |
| Runtime | TEI container (already in Docker Compose, port 8081) |
| Activation | When Gemini embedding API is unavailable (rate limit, outage, network) |

**CRITICAL: Dual-dimension collection design**

Because the primary embedder (768-dim) and fallback embedder (1024-dim) produce different vector sizes, the system uses **Qdrant named vectors** with two vector spaces:

```python
client.create_collection(
    collection_name="grc_controls",
    vectors_config={
        "gemini": models.VectorParams(size=1536, distance=models.Distance.COSINE),
        "tei": models.VectorParams(size=1024, distance=models.Distance.COSINE),
    },
)
```

During ingestion, **only the active embedder's vector is populated**. At query time, the system queries the vector space matching the embedder used for the data. In practice, Gemini is always primary — the TEI fallback only activates if Gemini is unreachable, and those ingested points would be queryable via the `tei` vector name.

> **Simpler alternative (recommended for v4):** Use Gemini-only with 768-dim. If Gemini is down, **queue the ingestion job** and retry later rather than embedding with a different model. This avoids mixed vector spaces entirely.
>
> ```python
> # Simplified single-vector collection (recommended)
> client.create_collection(
>     collection_name="grc_controls",
>     vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
> )
> ```
>
> The TEI container remains running for health checks and as an emergency fallback if needed in the future. The system logs a warning and retries with backoff if Gemini is temporarily unavailable.

### 3.5 Reranker: BGE Reranker v2-m3

| Property | Value |
|---|---|
| Model ID | `BAAI/bge-reranker-v2-m3` |
| Parameters | 568M |
| Architecture | XLM-RoBERTa fine-tuned for reranking |
| Runtime | TEI container (already in Docker Compose, port 8082) |
| Score normalization | Sigmoid → `[0, 1]` |
| Languages | Multilingual (100+ languages) |

**Why BGE Reranker v2-m3 over CrossEncoder ms-marco-MiniLM-L6-v2:**

Both were evaluated for the GRC compliance domain. BGE Reranker is the clear choice:

| Criterion | BGE Reranker v2-m3 | CrossEncoder MiniLM-L6-v2 |
|---|---|---|
| **Parameters** | 568M | 22.7M |
| **Training data** | Diverse multilingual retrieval tasks (BEIR, MIRACL, multilingual datasets) | MS Marco passage ranking (English web search queries) |
| **Domain fit for GRC** | **Strong** — trained on technical, legal, and scientific text across BEIR tasks (SciFact, TREC-COVID, FiQA) which share linguistic patterns with compliance/regulatory text | **Weak** — trained on general web search queries ("what is the capital of France") that don't match regulatory control language |
| **Language support** | **Multilingual** (100+ languages) — critical for GDPR (multi-jurisdiction), ISO standards (published in multiple languages), regional regulations | **English only** — cannot handle non-English regulatory text |
| **Semantic depth** | 568M params captures nuanced relationships between a finding ("admin access without MFA") and dense regulatory control text ("authentication information SHALL be allocated and managed") | 22.7M params is optimized for speed but lacks capacity for deep semantic matching on specialized vocabulary |
| **Deployment** | **Already running** in Docker Compose as TEI container on port 8082 — zero additional infrastructure | Requires loading via `sentence-transformers` in-process or deploying a new container |
| **Score range** | Sigmoid-normalized `[0, 1]` — natural threshold at 0.9 | Raw logits — requires calibration for threshold-based filtering |
| **BEIR benchmark** | Top-tier across SciFact, FiQA, TREC-COVID (domains closest to GRC) | Not benchmarked on BEIR; MS Marco focused |
| **Speed** | ~100-200 qps (adequate for 30 chunks × N frameworks) | ~1800 qps on V100 (faster but unnecessary for our scale) |

**Bottom line:** CrossEncoder MiniLM-L6-v2 optimizes for speed on English web search. BGE Reranker v2-m3 optimizes for **semantic quality on diverse, specialized text** — exactly what GRC compliance mapping requires. The 568M parameter budget gives it the capacity to understand that "authentication information SHALL be allocated and managed" is semantically related to "admin access without MFA" even though they share almost no keywords.

**Reranker API (via TEI):**
```python
import httpx

async def rerank(query: str, documents: list[str], top_n: int = 30) -> list[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8082/rerank",
            json={"query": query, "texts": documents, "truncate": True},
        )
        results = response.json()
        # TEI returns scores already sigmoid-normalized for bge-reranker-v2-m3
        return sorted(results, key=lambda x: x["score"], reverse=True)
```

### 3.6 Model Stack Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                        MODEL STACK                              │
│                                                                 │
│  PARSING:     Gemini 2.5 Flash (structured output)              │
│               └─ Pydantic schema → JSON extraction              │
│                                                                 │
│  EMBEDDING:   Gemini embedding-001 (1536-dim, MRL)              │
│               ├─ Ingestion: task_type=RETRIEVAL_DOCUMENT         │
│               ├─ Query: task_type=RETRIEVAL_QUERY                │
│               └─ Fallback: TEI bge-large-en-v1.5 (1024-dim)     │
│                                                                 │
│  RERANKING:   BGE Reranker v2-m3 (568M, multilingual)           │
│               ├─ TEI container on port 8082                      │
│               ├─ Sigmoid scores [0, 1]                           │
│               └─ Dynamic threshold ≥ 0.9                         │
│                                                                 │
│  REPORT LLM:  Gemini 2.5 Flash (single call, structured output) │
│               └─ Cross-framework compliance report               │
│                                                                 │
│  SDK:         google-genai (unified client for all Gemini calls) │
│               └─ GEMINI_API_KEY from environment                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Collection Schema

### 4.1 Collection Creation

```python
from qdrant_client import QdrantClient, models

client = QdrantClient(url="http://localhost:6333")

client.create_collection(
    collection_name="grc_controls",
    vectors_config=models.VectorParams(
        size=1536,                          # gemini-embedding-001 (MRL 1536-dim)
        distance=models.Distance.COSINE,
    ),
)

# MUST be created immediately after collection, before any inserts
client.create_payload_index(
    collection_name="grc_controls",
    field_name="framework",
    field_schema={"type": "keyword", "is_tenant": True},
)
```

### 4.2 Payload Schema (7 Fields)

| # | Field | Indexed | Type | Purpose |
|---|---|---|---|---|
| 1 | `framework` | **Yes** (keyword, tenant) | string | **Only query filter.** Per-framework search. Tenant-optimized for disk locality. |
| 2 | `text` | No | string | Chunk text. Fed to reranker + LLM for reasoning and quoting failure clauses. |
| 3 | `section_id` | No | string | Official control/requirement/article ID (e.g., "AC-4", "5.1", "Article 25"). LLM cites this in the final report. |
| 4 | `title` | No | string | Section title. LLM uses for readable output headings. |
| 5 | `domain` | No | string | Parent grouping from document structure (e.g., "Access Control", "Organizational controls"). LLM uses for categorization in the final report. |
| 6 | `framework_version` | No | string | Framework version (e.g., "2022", "Rev. 5"). LLM includes in the report for precision. |
| 7 | `chunk_index` | No | integer | 0-based position within a multi-chunk section. Provides ordering context if the LLM needs to reason across chunks of the same section. |

### 4.3 Why Only 1 Indexed Field

Every indexed field adds:
- Memory overhead (payload index data structure)
- Extra edges in HNSW graph (build-time + storage cost)
- Maintenance load during upserts

The CISO agent's query pattern only ever filters by `framework`. All other fields (`section_id`, `domain`, `title`, `framework_version`) are metadata consumed **after** retrieval — by the reranker and LLM. Indexing them would cost resources with zero query benefit.

### 4.4 Fields Deliberately Excluded

| Field | Why Excluded |
|---|---|
| `framework_category` | Redundant with `domain` — same concept, different name |
| `section_type` ("control"/"article"/"technique") | LLM infers type from context. Never filtered on. |
| `extraction_method` / `confidence` | Debugging info. Log during ingestion, don't persist in Qdrant. |
| `display_name` / `source_org` | Derivable at query time via `frameworks.json` registry lookup. |
| `total_chunks` | Was in v3 for reassembly. Unnecessary — the LLM works with individual chunks. |
| `source_document` | Not queried. Can add later if audit trail needed. |

---

## 5. Ingestion Pipeline

### 5.1 Overview

```
PDF Upload + framework_key
        │
        ▼
┌─── 1. Validate ──────────────────────────────────────────┐
│   framework_key must exist in frameworks.json             │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 2. Store PDF ─────────────────────────────────────────┐
│   LocalPDFStorage: data/pdfs/{framework_key}/{file}.pdf   │
│   (S3-ready interface for future migration)               │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 3. Extract ───────────────────────────────────────────┐
│   PyMuPDF4LLM: PDF → Markdown                            │
│   Preserves tables, headings, hierarchy                   │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 4. Parse ─────────────────────────────────────────────┐
│   Specialized parser exists? → Use it (e.g., ISO 27001)  │
│   No specialized parser? → Gemini structured output       │
│   Output: list[ParsedSection]                             │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 5. Chunk ─────────────────────────────────────────────┐
│   ≤ 512 tokens → single chunk                            │
│   > 512 tokens → RecursiveCharacterTextSplitter           │
│   Text format: "{section_id} — {title}\n\n{body}"        │
│   Metadata: framework, framework_version, section_id,     │
│             title, domain, chunk_index                    │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 6. Embed + Upsert ───────────────────────────────────┐
│   Gemini embedding-001 (1536-dim, task=RETRIEVAL_DOCUMENT)│
│   Fallback: TEI bge-large-en-v1.5 (1024-dim)             │
│   Target: grc_controls (single collection)                │
│   Point ID: uuid5(framework + section_id + chunk_index)   │
│   Deterministic → idempotent re-ingestion                 │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌─── 7. Cleanup ───────────────────────────────────────────┐
│   Delete stored PDF if configured                         │
└───────────────────────────────────────────────────────────┘
```

### 5.2 Framework Registry

**File:** `src/config/frameworks.json`

```json
{
  "iso_27001": {
    "display_name": "ISO/IEC 27001:2022",
    "version": "2022",
    "source_org": "ISO/IEC"
  },
  "nist_800_53": {
    "display_name": "NIST SP 800-53 Rev. 5",
    "version": "Rev. 5",
    "source_org": "NIST"
  },
  "pci_dss_v4": {
    "display_name": "PCI-DSS v4.0",
    "version": "4.0",
    "source_org": "PCI SSC"
  }
}
```

- **Key** = slug used as `framework` payload value. Immutable once data is ingested into Qdrant.
- **Lean schema**: only 3 metadata fields needed. No `document_type`, `tags`, `expected_controls_range` — Gemini handles all doc types uniformly.

**Module:** `src/config/registry.py`
- `load_frameworks()` → load and cache the JSON dict
- `get_framework(key)` → return metadata or raise `ValueError`
- `list_framework_keys()` → all available keys (for API dropdown)
- `validate_framework_key(key)` → bool

**Replaces:** `src/config/framework_categories.yaml` (deleted)

### 5.3 Gemini Structured Parser

**File:** `src/ingestion/gemini_parser.py`

**Why Gemini over regex heuristics:**
- v3 planned 5 regex extractors (table, numbered, family, article, heading) + strategy selection + LLM fallback
- Each heuristic handles one document pattern; new frameworks need new heuristics
- Regex is brittle across PDF extraction artifacts (merged cells, broken lines, encoding issues)
- Gemini structured output: **one prompt handles ALL framework types**, reliable JSON schema enforcement, no per-framework maintenance

**Approach:**
1. Receive full markdown + framework metadata
2. Window markdown into ~30K-token windows (with 1K overlap)
   - Gemini 2.5 Flash handles 1M token context, but windowing keeps cost predictable and output focused
3. For each window, call Gemini with structured output via `google-genai` SDK:

```
You are a GRC compliance expert parsing a {framework_display_name} ({version}) document.

Extract ALL controls, requirements, articles, criteria, or sections from the text below.

Return a JSON array. Each item MUST have:
- section_id: The official identifier exactly as written in the document
              (e.g., "5.1", "AC-4", "Article 25", "CC6.1", "A01:2021")
- title: Short descriptive title of this section
- text: Complete text of this section (all description, guidance, requirements)
- domain: The parent grouping/category this belongs to, derived from the document's
          own structure (table headers, chapter titles, family names).
          If no clear grouping exists, use "General".

Rules:
- Extract every identifiable section, including sub-controls/enhancements
- Preserve exact section_id as written in the document
- Do NOT invent or hallucinate sections not present in the text
- If a section spans multiple paragraphs, include all of them in text

TEXT:
{markdown_window}
```

4. Merge results across windows, deduplicate by `section_id` (keep the longest `text` if duplicated at window boundaries)
5. Return `list[ParsedSection]`

**ParsedSection dataclass:**
```python
@dataclass
class ParsedSection:
    framework: str          # "iso_27001" (from registry key)
    framework_version: str  # "2022" (from registry)
    domain: str             # "Organizational controls" (extracted by Gemini)
    section_id: str         # "5.1" (extracted by Gemini)
    title: str              # "Policies for information security" (extracted by Gemini)
    text: str               # Full section text (extracted by Gemini)
```

**Parser resolution order:**
1. Specialized parser registered for this `framework_key`? → Use it (zero API cost)
2. No specialized parser → Gemini structured output

The existing `ISO27001Parser` remains as a specialized parser — it's tested, free, and produces 93 controls reliably.

### 5.4 Chunking Strategy

Chunking is straightforward because there are no section-level grouping constraints in Qdrant.

**For each `ParsedSection`:**
1. Construct the embeddable text with context prefix:
   ```
   {section_id} — {title}

   {section_body}
   ```
   This ensures the embedding captures both the identity (e.g., "AC-4") and the semantic content.

2. Estimate tokens (~1.33 × word count)

3. If ≤ 512 tokens → single chunk
   If > 512 tokens → split with `RecursiveCharacterTextSplitter`:
   - Separators: `["\n## ", "\n### ", "\n#### ", "\n\n", "\n", " "]`
   - `chunk_size=512`, `chunk_overlap=50`
   - First chunk keeps the `{section_id} — {title}\n\n` prefix
   - Subsequent chunks get a shortened prefix: `{section_id} (cont.) — `

4. Stamp metadata:
   ```python
   payload = {
       "framework": "iso_27001",
       "text": chunk_text,
       "section_id": "5.1",
       "title": "Policies for information security",
       "domain": "Organizational controls",
       "framework_version": "2022",
       "chunk_index": 0,
   }
   ```

5. Point ID: `uuid5(framework + section_id + str(chunk_index))`
   - Deterministic: re-ingesting the same framework overwrites existing points (idempotent)
   - Unique across frameworks: `uuid5("iso_27001_5.1_0")` ≠ `uuid5("cis_v8_5.1_0")`

### 5.5 Section ID Strategy

`section_id` is always **extracted from the document itself** — never generated or computed. Each framework has its own official ID scheme:

| Framework | ID Pattern | Example |
|---|---|---|
| ISO 27001 | `\d+\.\d+` | `5.1`, `8.20` |
| NIST 800-53 | `[A-Z]{2}-\d+(\(\d+\))?` | `AC-4`, `SI-4(5)` |
| PCI-DSS v4 | `\d+\.\d+\.\d+` | `1.2.1`, `8.4.2` |
| GDPR | `Article \d+` | `Article 25` |
| SOC 2 (TSC) | `CC\d+\.\d+` | `CC6.1` |
| NIST CSF v2 | `XX.YY-\d+` | `GV.OC-01` |
| MITRE ATT&CK | `T\d{4}(\.\d{3})?` | `T1078.001` |
| CIS Controls v8 | `\d+\.\d+` | `4.6` |
| ISO 31000 / 22301 | `\d+\.\d+` (clauses) | `6.2`, `8.3` |
| OWASP Top 10 | `A\d{2}:\d{4}` | `A01:2021` |

**Granularity:** Each identifiable atomic unit gets its own `section_id`. Sub-controls and enhancements (e.g., `AC-4(1)`, `AC-4(5)`) are separate sections, not merged into the parent. This gives the CISO agent granular matching.

For narrative frameworks without per-section IDs, Gemini uses clause numbers or heading positions.

---

## 6. Query Flow (CISO Agent)

### 6.1 End-to-End Flow

```
API Input:
{
    "finding": "Server XYZ allows admin access without MFA",
    "requested_frameworks": ["iso_27001", "nist_800_53", "pci_dss_v4"]
}
```

**Step 1 — Embed the finding**
```python
finding_vector = embed(finding_text)  # gemini-embedding-001, task_type=RETRIEVAL_QUERY, 1536-dim
```

**Step 2 — Batch query Qdrant (single HTTP call)**
```python
POST /collections/grc_controls/points/query/batch
{
    "searches": [
        {
            "query": <finding_vector>,
            "filter": {"must": [{"key": "framework", "match": {"value": "iso_27001"}}]},
            "limit": 30,
            "with_payload": true
        },
        {
            "query": <finding_vector>,
            "filter": {"must": [{"key": "framework", "match": {"value": "nist_800_53"}}]},
            "limit": 30,
            "with_payload": true
        },
        {
            "query": <finding_vector>,
            "filter": {"must": [{"key": "framework", "match": {"value": "pci_dss_v4"}}]},
            "limit": 30,
            "with_payload": true
        }
    ]
}
```

Returns: 30 chunks × 3 frameworks = up to 90 candidate chunks.

**Step 3 — Per-framework rerank**
For each framework's 30 chunks, call bge-reranker-v2-m3 with the finding as the query.
Keep only chunks with rerank score ≥ 0.9.

```python
# Per framework
reranked = reranker.rerank(query=finding_text, documents=framework_chunks)
qualifying = [c for c in reranked if c.score >= 0.9]
```

Typically 3–8 chunks survive per framework. Some findings match 1, others match 10 — the threshold adapts naturally.

**Step 4 — Collect all qualifying chunks**
Aggregate across all frameworks into a single context block. **No LLM call until all frameworks are processed.**

```python
all_context = {}
for fw in requested_frameworks:
    all_context[fw] = qualifying_chunks[fw]
    # e.g., {"iso_27001": [chunk1, chunk2], "nist_800_53": [chunk3, chunk4, chunk5], ...}
```

**Step 5 — Single LLM call for final report**

```
You are a CISO agent performing compliance mapping.

FINDING:
"Server XYZ allows admin access without MFA"

MATCHED CONTROLS BY FRAMEWORK:

--- ISO/IEC 27001:2022 ---
[8.5] Authentication information
"Control: Authentication information shall be allocated and managed..."

[5.15] Access control
"Control: Rules to control physical and logical access..."

--- NIST SP 800-53 Rev. 5 ---
[IA-2] Identification and Authentication (Organizational Users)
"Uniquely identify and authenticate organizational users..."

[IA-2(1)] Multi-Factor Authentication
"Implement multi-factor authentication for access to privileged accounts..."

--- PCI-DSS v4.0 ---
[8.4.2] MFA for all access into the CDE
"MFA is implemented for all access into the cardholder data environment..."

TASK:
For each framework, identify the specific failure clause — the exact requirement
being violated by this finding. Return a structured report.
```

**Step 6 — Return structured response**

```json
{
  "finding": "Server XYZ allows admin access without MFA",
  "report": {
    "iso_27001": {
      "version": "2022",
      "matched_controls": [
        {
          "section_id": "8.5",
          "title": "Authentication information",
          "domain": "Technological controls",
          "failure_clause": "Authentication information SHALL be allocated and managed — the system fails to enforce multi-factor authentication for admin-level access."
        }
      ]
    },
    "nist_800_53": {
      "version": "Rev. 5",
      "matched_controls": [
        {
          "section_id": "IA-2(1)",
          "title": "Multi-Factor Authentication",
          "domain": "Identification and Authentication",
          "failure_clause": "The system does not implement multi-factor authentication for access to privileged accounts as required by enhancement IA-2(1)."
        }
      ]
    },
    "pci_dss_v4": {
      "version": "4.0",
      "matched_controls": [
        {
          "section_id": "8.4.2",
          "title": "MFA for CDE access",
          "domain": "Implement Strong Access Control Measures",
          "failure_clause": "MFA is not implemented for admin access into the cardholder data environment, violating requirement 8.4.2."
        }
      ]
    }
  }
}
```

### 6.2 Why Per-Framework Retrieval (Not Mixed `match.any`)

A single `match.any` across 3 frameworks returns top-30 **mixed** — the distribution is uncontrolled. You might get 25 ISO results and 1 PCI-DSS result because ISO embeddings happened to be closer. Per-framework queries guarantee **equal representation**: exactly 30 candidates per framework, independently reranked and thresholded.

### 6.3 Why Dynamic Threshold (Not Fixed Top-K)

| Approach | Problem |
|---|---|
| Fixed top-5 | Returns 5 even when only 2 are relevant (LLM noise). Misses the 6th hit that scored 0.95. |
| Dynamic ≥ 0.9 | Finding about "MFA bypass" matches 8 PCI-DSS controls → all 8 pass. Finding about "business continuity" matches 1 ISO control → only 1 passes. LLM gets exactly the right context. |

### 6.4 Why Single LLM Call (Not Per-Framework)

Collecting all qualifying chunks before the LLM call means:
- **1 LLM call total** regardless of N frameworks (not N calls)
- The LLM sees the full picture — can note cross-framework correlations
- Lower latency and cost
- Consistent report tone and structure

---

## 7. Implementation Phases

### Phase 1: Dependencies & SDK Setup

| Action | File | Details |
|---|---|---|
| Update | `requirements.txt` | Add `google-genai`, `qdrant-client`, `pymupdf4llm`, `langchain-text-splitters` |
| Create | `.env` | `GEMINI_API_KEY=...` (used by `google-genai` client) |
| Verify | SDK connectivity | `client = genai.Client()` → test `embed_content` and `generate_content` |

### Phase 2: Framework Registry

| Action | File | Details |
|---|---|---|
| Create | `src/config/frameworks.json` | 50+ framework entries, lean schema |
| Create | `src/config/registry.py` | `load_frameworks()`, `get_framework()`, `list_framework_keys()` |
| Delete | `src/config/framework_categories.yaml` | Replaced by `frameworks.json` |

### Phase 3: Gemini Parser

| Action | File | Details |
|---|---|---|
| Create | `src/ingestion/gemini_parser.py` | Windowed Gemini calls via `google-genai` structured output → `list[ParsedSection]`, dedup across windows. Model: `gemini-2.5-flash`. |

### Phase 4: Gemini Embedder

| Action | File | Details |
|---|---|---|
| Create | `src/ingestion/embedder.py` | `GeminiEmbedder` class: `embed_documents(texts)` with `task_type=RETRIEVAL_DOCUMENT`, `embed_query(text)` with `task_type=RETRIEVAL_QUERY`. 1536-dim via MRL. Batch up to 100 texts. Retry with exponential backoff on API errors. |
| Keep | TEI container (port 8081) | Remains as infrastructure fallback. Not used in hot path unless Gemini is unavailable. |

### Phase 5: Module Updates

| Action | File | Changes |
|---|---|---|
| Modify | `src/ingestion/parser.py` | Rename `ParsedControl` → `ParsedSection`. Remove `load_framework_config()`, `resolve_category()`, YAML import. Keep `ISO27001Parser`. Add `GeminiParser` dispatch in `get_parser()`. |
| Modify | `src/ingestion/chunker.py` | Update to `ParsedSection`. Metadata: framework, framework_version, section_id, title, domain, chunk_index. Embed text prefixed with `{section_id} — {title}\n\n`. |
| Modify | `src/ingestion/qdrant_loader.py` | Single collection `grc_controls` with 1536-dim vectors. One `is_tenant` index on `framework`. Point ID = `uuid5(framework + section_id + chunk_index)`. Use `GeminiEmbedder` for embedding. |
| Modify | `src/ingestion/pipeline.py` | Use `registry.get_framework()` for validation. Target single collection. Dispatch to specialized parser → Gemini fallback. |
| Modify | `src/config/settings.py` | Full rewrite per Section 8.1 — Gemini-centric config, TEI as fallback only. |

### Phase 6: Unchanged

| File | Status |
|---|---|
| `src/ingestion/storage.py` | No changes — already uses `framework_key` for directory organization |
| `src/ingestion/extractor.py` | No changes — PyMuPDF4LLM extraction is framework-agnostic |
| Docker Compose | TEI embedder (8081) and reranker (8082) containers unchanged. Reranker (bge-reranker-v2-m3) remains the active reranking service. |

---

## 8. Configuration

### 8.1 New Settings (`src/config/settings.py`)

```python
class IngestionSettings(BaseSettings):
    # --- Infrastructure ---
    qdrant_url: str = "http://localhost:6333"
    reranker_url: str = "http://localhost:8082"     # TEI bge-reranker-v2-m3
    storage_backend: str = "local"
    local_pdf_dir: Path = Path("data/pdfs")
    delete_pdf_after_ingestion: bool = True

    # --- Gemini (google-genai SDK) ---
    gemini_api_key: str = ""                        # From env: INGESTION_GEMINI_API_KEY
    gemini_parse_model: str = "gemini-2.5-flash"    # Structured output for parsing
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_window_size: int = 30000                 # Tokens per Gemini parser window

    # --- Embedding ---
    embedding_dimension: int = 1536                 # MRL dimension (768/1536/3072)
    embed_batch_size: int = 32

    # --- Fallback embedder (TEI) ---
    tei_embedder_url: str = "http://localhost:8081"  # TEI bge-large-en-v1.5
    tei_embedding_dimension: int = 1024

    # --- Chunking ---
    chunk_size: int = 512
    chunk_overlap: int = 50

    # --- Qdrant ---
    collection_name: str = "grc_controls"           # Single collection
    qdrant_distance: str = "Cosine"

    # --- Retrieval ---
    rerank_threshold: float = 0.9                   # Dynamic rerank score cutoff
    retrieval_limit: int = 30                       # Chunks per framework from Qdrant
```

---

## 9. Verification Plan

| # | Test | What It Validates |
|---|---|---|
| 1 | Gemini parser on ISO 27001 markdown | ~93 sections extracted, matching specialized parser output |
| 2 | E2E ISO 27001 (specialized parser path) | Points in `grc_controls` with all 7 payload fields correct |
| 3 | E2E second framework (Gemini parser path) | Extraction quality on a non-ISO framework |
| 4 | Per-framework filtered query | Only requested framework's chunks returned |
| 5 | Tenant index applied | Confirm `is_tenant: true` via Qdrant collection info API |
| 6 | Re-ingestion idempotency | Ingest same framework twice → no duplicate points (deterministic uuid5) |
| 7 | Dynamic rerank threshold | score ≥ 0.9 yields variable result count per framework |
| 8 | Batch query | N frameworks retrieved in single HTTP call |
| 9 | Single LLM call | All frameworks collected before LLM; 1 call produces complete report |
| 10 | Registry validation | Unknown key raises error; `list_framework_keys()` returns all 50+ |

---

## 10. Key Decisions & Rationale

| Decision | Rationale |
|---|---|
| Single collection `grc_controls` | Qdrant-recommended multitenancy pattern. Faster filtered search, shared index, no fan-out. |
| `framework` as only indexed field | Only field used for query filtering. All other fields are LLM metadata — indexing them wastes resources. |
| `is_tenant: true` on `framework` | Disk locality optimization for tenant-filtered searches. |
| Per-framework retrieval (not `match.any`) | Guarantees equal representation per framework — every framework gets its own 30 candidates. |
| 30 initial chunks per framework | Generous recall pool for the reranker without being expensive (reranker is fast on 30 short chunks). |
| Dynamic threshold ≥ 0.9 (not fixed top-K) | Adapts to finding specificity — returns 1-10 controls based on actual relevance, not arbitrary cutoff. |
| All frameworks collected before LLM | Single LLM call produces the full cross-framework report. Fewer calls, lower cost, the LLM sees the complete picture. |
| Gemini structured output (not regex heuristics) | One prompt handles all 50+ framework document types uniformly. No per-framework regex maintenance. |
| Keep `ISO27001Parser` as specialized | Zero API cost, tested (93 controls), reliable. Specialized parsers get priority over Gemini. |
| Text embedding includes `{section_id} — {title}` prefix | Embedding captures both identity and semantics — improves retrieval relevance for control-specific queries. |
| `uuid5(framework + section_id + chunk_index)` point IDs | Deterministic = idempotent re-ingestion. Unique across frameworks even with shared section_id strings (e.g., "5.1"). |
| **Gemini embedding-001 at 1536-dim** | MRL gives 100% of 3072-dim quality (actually +0.01% on MTEB) at 2× less storage. Asymmetric task_type (RETRIEVAL_DOCUMENT/QUERY) is a major advantage over symmetric bge-large. |
| **BGE Reranker v2-m3 over CrossEncoder MiniLM** | 568M params for deep semantic matching on regulatory text. Multilingual for cross-jurisdiction frameworks. Already deployed in TEI container. CrossEncoder ms-marco-MiniLM is English-only, trained on web search queries — poor fit for GRC compliance language. |
| **google-genai SDK (unified)** | Single SDK for all Gemini operations (parsing, embedding, report LLM). Cleaner than mixing `google-generativeai` + `google-cloud-aiplatform`. |
| **TEI as fallback (not dual-vector)** | Simpler than maintaining two vector spaces. If Gemini is temporarily down, queue and retry rather than embedding with a different model that creates query-time complexity. |

---

## 11. Future Enhancements (Not in v4 Scope)

| Enhancement | Description | When to Add |
|---|---|---|
| Hybrid search (dense + sparse) | Add SPLADE/BM25 as named sparse vector. Fuse with weighted RRF. Benefits: exact term matching ("AC-4") alongside semantic similarity. | When exact control ID lookup becomes a frequent query pattern. |
| Full-text index on `text` | Enable exact phrase/keyword search within chunk text using Qdrant's full-text index. | When users want to search by exact phrases ("multi-factor authentication"). |
| Adaptive rerank threshold | Learn per-framework optimal thresholds from user feedback. Some frameworks may need 0.85, others 0.95. | After collecting usage data from CISO agent interactions. |
| More specialized parsers | Build zero-cost regex parsers for high-volume frameworks (PCI-DSS, NIST 800-53) to reduce Gemini API costs. | When Gemini costs become significant at scale. |
| Sparse vector for BM25 | Add IDF-modified sparse vectors for keyword search. Combine with dense via RRF for hybrid retrieval. | When semantic-only retrieval misses keyword-dependent matches. |