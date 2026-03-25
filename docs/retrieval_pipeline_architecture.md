# Retrieval Pipeline — Architecture

---

## Pipeline Overview

```
QueryRequest ──► Cache ──HIT──► QueryResponse (instant)
                   │
                  MISS
                   ▼
         ┌─────────────────┐
         │  1. Embed        │  Gemini embedding-001 (RETRIEVAL_QUERY)
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  2. Search       │  Qdrant vector search (parallel per-framework)
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  3. Map          │  Gemini LLM — structured ControlMapping output
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  4. Critique     │  Gemini LLM — adversarial validation (conditional)
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │  5. Aggregate    │  Merge mappings + token counts
         └────────┬────────┘
                  ▼
            QueryResponse ──► Cache WRITE ──► Return
```

---

## Stage Details

### Stage 0 — Cache Lookup

| Component | `RedisCache` |
|-----------|-------------|
| **Input** | `finding_text` + `target_frameworks` |
| **Key** | SHA-256 of normalized finding + sorted frameworks + model + collection |
| **HIT** | Return cached `QueryResponse` immediately — skip all stages |
| **MISS** | Acquire stampede lock, proceed to Stage 1 |

**Normalization** (for key stability):
- Lowercase, strip, collapse whitespace
- Remove filler prefixes ("please map", "find controls for", etc.)
- Preserve all punctuation (control IDs, protocol names)

**Stampede protection**: `SET key:lock 1 NX EX {lock_timeout}` — only one request computes while others wait.

**Eviction**: LFU-based. Frequency tracked via sorted set. Eviction triggers when memory exceeds configured threshold.

---

### Stage 1 — Embed Finding

| Component | `GeminiEmbedder` |
|-----------|------------------|
| **Model** | `gemini-embedding-001` |
| **Task type** | `RETRIEVAL_QUERY` (asymmetric — documents were embedded with `RETRIEVAL_DOCUMENT`) |
| **Output** | Query vector (`dimension` from settings) |

---

### Stage 2 — Vector Search

| Component | `QdrantRetriever` |
|-----------|-------------------|
| **Collection** | `grc_controls` (configured via `qdrant.collection_name`) |
| **Filter** | `framework == framework_key` (tenant isolation) |
| **Parallelism** | One thread per framework via `ThreadPoolExecutor` |
| **Limit** | `retrieval.limit` results per framework |
| **Output** | `dict[framework_key → list[ScoredChunk]]` |

Each `ScoredChunk` carries:
- `text` — chunk content
- `metadata` — framework, source_document, heading hierarchy (h1–h6)
- `qdrant_score` — cosine similarity
- `citation_source` — auto-built as `"Document Name, H1 > H2 > ..."`

---

### Stage 3 — Compliance Mapping (LLM)

| Component | `ComplianceMapper` |
|-----------|---------------------|
| **Model** | `gemini.parse_model` (from settings) |
| **Temperature** | `0.1` |
| **Output format** | JSON — `list[ControlMapping]` via structured output schema |
| **Parallelism** | One mapper call per framework (parallel with Stage 4 per framework) |

**User prompt construction**:
```
FINDING: {finding_text}

EVIDENCE

## {framework_key}
[1|Document, Section > Subsection]
{chunk text}
[2|Document, Section > Subsection]
{chunk text}
...
```

**Each `ControlMapping` contains**:
- `control_id`, `control_title`, `domain`, `framework`, `framework_version`
- `risk_mitigated` — why this control addresses the finding
- `citation` — verbatim excerpt from evidence
- `citation_source` — source path matching the evidence chunk
- `confidence_score` — 0–100

---

### Stage 4 — Adversarial Critique (Conditional LLM)

| Component | `AdversarialCritic` |
|-----------|----------------------|
| **Model** | `gemini.parse_model` (same as mapper) |
| **Temperature** | `0.0` |
| **Trigger** | Any mapping has `confidence_score < critic_confidence_threshold` |
| **Skipped when** | All mappings meet or exceed the threshold |
| **Output format** | JSON — `list[{index, is_valid, reason}]` |

**Critic prompt construction**:
```
FINDING: {finding_text}

MAPPINGS
[0] A.8.20 "Web filtering" confidence=72 citation="..." source=...
[1] A.5.1 "Policies for ..." confidence=90 citation="..." source=...

EVIDENCE
[1|Document, Section > Subsection]
{chunk text}
...
```

**Evidence scoping**: Only chunks whose `citation_source` was actually cited by the mapper are sent. Falls back to all chunks if no match.

**Result**: Each mapping stamped `APPROVED` or `FAILED` with a `critic_reason`.

---

### Stage 5 — Aggregate & Return

- Merge `ControlMapping` lists from all frameworks
- Sum token usage: `mapper_prompt + mapper_total + critic_prompt + critic_total`
- Build `QueryResponse` with timing, chunk count, and token breakdown
- If cache lock was acquired: write response to Redis, release lock

---

## Data Flow Summary

```
QueryRequest
  ├── finding_text: str
  └── target_frameworks: [fw_key, ...]
          │
          ▼
   ┌──────────────┐
   │  Embed        │ → query_embedding (float vector)
   └──────┬───────┘
          ▼
   ┌──────────────┐     Per framework (parallel):
   │  Search       │ → { fw_key: [ScoredChunk, ...], ... }
   └──────┬───────┘
          ▼
   ┌──────────────┐     Per framework (parallel):
   │  Map          │ → [ControlMapping, ...]  +  token counts
   └──────┬───────┘
          ▼
   ┌──────────────┐     Conditional, per framework:
   │  Critique     │ → [ControlMapping (APPROVED|FAILED), ...]  +  token counts
   └──────┬───────┘
          ▼
QueryResponse
  ├── mappings: [ControlMapping, ...]
  ├── frameworks_searched, chunks_retrieved
  ├── duration_seconds
  └── token_usage: TokenUsage
```

---

## LLM System Prompts

### Mapper System Prompt

```
You are a GRC compliance analyst. Map the security finding to {framework_name}
controls ONLY, using ONLY the provided evidence.

Rules:
- One mapping per distinct control.
- citation MUST be a verbatim excerpt from the evidence — never paraphrase.
- citation_source must match the source path provided with the evidence chunk.
- Do not fabricate mappings unsupported by evidence.
- confidence_score (0-100) reflects how directly the control addresses the finding.
```

> `{framework_name}` is replaced with the specific framework key (e.g. `iso_27001`) per call.
> When no framework key is provided, defaults to `"the target framework"`.

---

### Critic System Prompt

```
You are an adversarial reviewer for GRC compliance mappings. For each mapping verify:
1. Citation Grounding — citation text appears verbatim (or very close) in the evidence.
2. Logical Consistency — the cited control logically addresses the finding.
3. Confidence Calibration — score is reasonable given evidence strength.

FAIL any mapping that does not pass all three checks.
Return a JSON array of {index, is_valid, reason} per mapping.
```

> Always runs at `temperature=0.0`. No framework-specific templating.

---

## Configuration Reference

| Setting | Used by |
|---------|---------|
| `gemini.parse_model` | Mapper, Critic |
| `gemini.embedding_model` | Embedder |
| `embedding.dimension` | Embedder output size |
| `retrieval.limit` | Qdrant search limit per framework |
| `retrieval.critic_confidence_threshold` | Gate for running critic |
| `qdrant.collection_name` | Vector search collection |
| `redis.enabled` | Cache on/off |
| `redis.key_prefix` | Cache key namespace |
| `redis.lock_timeout` | Stampede lock TTL |
| `redis.max_memory_mb` | Eviction memory ceiling |
