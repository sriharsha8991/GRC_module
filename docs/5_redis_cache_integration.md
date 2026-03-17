# GRC Module — Redis Cache Integration

> **Version:** 1.0  
> **Date:** March 17, 2026  
> **Status:** Planned  
> **Branch:** `feature/Sriharsha/redis-addition`  
> **Reference:** [Redis Caching in RAG — Mahak Faheem](https://dev.to/mahakfaheem/redis-caching-in-rag-normalized-queries-semantic-traps-what-actually-worked-59nn)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Design Decisions](#2-design-decisions)
3. [Architecture](#3-architecture)
4. [Query Normalization Strategy](#4-query-normalization-strategy)
5. [Cache Key Construction](#5-cache-key-construction)
6. [Redis Cache Client](#6-redis-cache-client)
7. [Pipeline Integration](#7-pipeline-integration)
8. [Memory Management & Eviction](#8-memory-management--eviction)
9. [Cache Stampede Protection](#9-cache-stampede-protection)
10. [Stats & Observability](#10-stats--observability)
11. [Configuration Reference](#11-configuration-reference)
12. [File Manifest](#12-file-manifest)
13. [Implementation Steps](#13-implementation-steps)
14. [Testing Strategy](#14-testing-strategy)
15. [Operational Notes](#15-operational-notes)

---

## 1. Overview

The retrieval pipeline is the most expensive path in the GRC system. Every query executes five stages:

```
Embed (Gemini API) → Search (Qdrant) → Rerank (TEI) → Map (Gemini API) → Critique (Gemini API)
```

Two of these stages are Gemini LLM calls, each costing tokens and adding latency. For repeated or near-identical security findings — common in enterprise environments where scanners produce the same findings across hosts — the pipeline produces identical results every time.

**Redis caches the full `QueryResponse`** at the pipeline boundary. On a cache hit, all five stages are skipped entirely, returning a response in sub-millisecond time.

### Core Guarantees

| Guarantee | How |
|---|---|
| **Redis never blocks the API** | All Redis operations wrapped in `try/except` with 1-second socket timeout. On any failure → pipeline runs normally. |
| **An answer is always returned** | Cache miss, Redis down, lock contention — all cases fall through to the full pipeline. No request ever stalls waiting for Redis. |
| **No false cache hits** | Conservative normalization preserves all technical tokens. Framework combination is a first-class key component. |
| **No stale data by TTL accident** | No TTL. Entries persist until memory-pressure eviction removes least-frequently-used keys. |

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **What to cache** | Final `QueryResponse` only | Maximum latency/cost savings (skips all 5 stages). Per-stage caching adds complexity with marginal benefit since the LLM calls dominate cost. |
| **Normalization level** | Conservative / GRC-aware | Findings are terse technical strings (`"MySQL remote login successful"`), not conversational. Every token matters. Aggressive normalization risks false cache hits — worse than cache misses in GRC. |
| **TTL** | None | Compliance frameworks rarely change. Eviction is memory-pressure driven via LFU (least-frequently-used). |
| **Eviction strategy** | Custom LFU via Redis sorted set | At 80% memory → evict bottom 30% by access frequency. Does not depend on Redis server `maxmemory-policy`. |
| **Cache invalidation** | None on ingestion | Multiple PDFs can map to the same framework. Flushing cache on ingestion would be too aggressive. Rely on memory-pressure eviction for natural turnover. |
| **Redis deployment** | External (configurable URL) | Not bundled in `docker-compose.yml`. Allows using managed Redis (ElastiCache, Azure Cache, etc.) in production. |
| **Stampede handling** | Cache + Lock (fail-open) | `SET NX EX` lock with 30s auto-expiry. If lock is already held, the request runs the full pipeline but does not cache (the lock holder caches). Never waits. |
| **Semantic caching** | Explicitly rejected | Semantic similarity is probabilistic. `"SQL injection in login form"` and `"SQL injection in payment form"` are semantically similar but map to different controls. In GRC, wrong cache hits break trust. |

---

## 3. Architecture

### Data Flow (cache-around pattern)

```
                         ┌─────────────────────────┐
   POST /query           │     query_finding()      │
   ──────────────────►   │                          │
                         │  1. normalize_finding()  │
                         │  2. build_cache_key()    │
                         │  3. cache.get(key)       │
                         │          │               │
                         │    ┌─────┴──────┐        │
                         │    │            │        │
                         │   HIT         MISS       │
                         │    │            │        │
                         │    │     4. acquire_lock  │
                         │    │         │      │    │
                         │    │      LOCKED  BUSY   │
                         │    │         │      │    │
                         │    │    [pipeline]  [pipeline]
                         │    │         │      │    │
                         │    │   cache.set  (skip) │
                         │    │   release    cache   │
                         │    │         │      │    │
                         │    └────┬────┘──────┘    │
                         │         │                │
                         │    QueryResponse         │
                         └─────────┼────────────────┘
                                   │
   ◄───────────────────────────────┘
   200 OK
```

### Where Redis Sits

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│  FastAPI     │───►│  Retrieval   │───►│  Redis       │
│  /query      │    │  Pipeline    │    │  (external)  │
│  /cache/stats│    │  pipeline.py │    │              │
└─────────────┘    └──────┬───────┘    └──────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         ┌────────┐ ┌─────────┐ ┌─────────┐
         │ Qdrant │ │ Gemini  │ │ TEI     │
         │        │ │ API     │ │ Reranker│
         └────────┘ └─────────┘ └─────────┘
```

Redis is accessed **only** from `src/retrieval/cache.py`. No other module touches Redis directly. The gateway and ingestion pipeline are untouched.

---

## 4. Query Normalization Strategy

### Why GRC Findings Need Special Treatment

Unlike chatbots or search engines where users ask conversational questions, GRC findings are terse technical statements from vulnerability scanners and auditors:

```
"MySQL remote login successful"
"Unencrypted PII in transit on port 443"
"SSH weak cipher suites detected"
"Default admin credentials on web console"
```

**Every token carries meaning.** Standard NLP normalization (stopword removal, stemming, synonym expansion) is dangerous here:

| Normalization | Why It's Dangerous in GRC |
|---|---|
| Remove stopwords | `"remote login"` → `"login"` (completely different finding) |
| Remove numbers | `"ISO 27001"` → `"ISO"` (which standard?) |
| Stem words | `"encrypted"` → `"encrypt"` (audit state vs. action — different meanings) |
| Replace synonyms | `"vulnerability"` → `"weakness"` (different GRC terminology) |
| Remove punctuation | `"PCI-DSS"` → `"PCIDSS"`, `"A.8.20"` → `"A820"` (control IDs destroyed) |

### What `normalize_finding()` Does

Only safe, minimal transformations:

| Step | Before | After | Why Safe |
|---|---|---|---|
| Lowercase | `"MySQL Remote Login"` | `"mysql remote login"` | Case never distinguishes GRC meaning |
| Trim whitespace | `"  mysql  remote  "` | `"mysql remote"` | Whitespace is presentation noise |
| Strip leading filler | `"Please map MySQL remote login"` | `"mysql remote login"` | `"Please map"` is conversational noise, not part of finding |
| **Keep all punctuation** | `"PCI-DSS v4.0 A.8.20"` | `"pci-dss v4.0 a.8.20"` | Hyphens, dots, slashes, colons all carry meaning |

### Filler Prefixes (removed only at start of text)

```python
FILLER_PREFIXES = [
    "can you map",
    "please map",
    "map this",
    "find controls for",
    "what controls apply to",
]
```

These are stripped **only when they appear as the leading phrase**. The word "map" appearing mid-sentence (e.g., `"network map exposed"`) is never touched.

---

## 5. Cache Key Construction

### Composite Key Structure

```
SHA-256( "{normalized_finding}||{sorted_frameworks}||{model_name}||{collection}" )
→ "grc:query:{hex_digest}"
```

| Segment | Purpose | Example |
|---|---|---|
| `normalized_finding` | The finding after normalization | `mysql remote login successful` |
| `sorted_frameworks` | Alphabetically sorted, pipe-separated | `iso_27001\|iso_27002` |
| `model_name` | Gemini model used for mapping | `gemini-2.5-flash` |
| `collection_name` | Qdrant collection queried | `grc_controls` |

**Double-pipe `||`** separates segments (never appears in findings or framework keys).

### Why Frameworks Are in the Key

The retrieval pipeline fans out one Qdrant search + one rerank per framework. The mapper receives evidence from **all** target frameworks in a single LLM call. Changing the framework list changes the evidence pool, which changes the mappings.

```
"MySQL remote login successful" + ["iso_27001"]
  → mapper sees only ISO 27001 evidence
  → maps to: A.8.5, A.8.20

"MySQL remote login successful" + ["iso_27001", "iso_27002"]
  → mapper sees ISO 27001 + ISO 27002 evidence
  → maps to: A.8.5, A.8.20, 8.5, 8.20 (both standards)
  → potentially different confidence scores due to richer evidence
```

These are **fundamentally different results**. The cache key must distinguish them.

### Determinism Examples

| Input Finding | Input Frameworks | Cache Key | Match? |
|---|---|---|---|
| `"MySQL remote login successful"` | `["iso_27001", "iso_27002"]` | `grc:query:a3f2...` | **baseline** |
| `"mysql remote login successful"` | `["iso_27001", "iso_27002"]` | `grc:query:a3f2...` | ✓ same |
| `"MySQL Remote Login Successful"` | `["iso_27002", "iso_27001"]` | `grc:query:a3f2...` | ✓ same (lowercased + sorted) |
| `"Please map MySQL remote login successful"` | `["iso_27001", "iso_27002"]` | `grc:query:a3f2...` | ✓ same (filler stripped) |
| `"MySQL remote login successful"` | `["iso_27001"]` | `grc:query:b7c1...` | ✗ different (1 framework) |
| `"MySQL remote login successful"` | `["iso_27001", "iso_27002", "pci_dss_v4"]` | `grc:query:d9e4...` | ✗ different (3 frameworks) |
| `"PostgreSQL remote login successful"` | `["iso_27001", "iso_27002"]` | `grc:query:f1a8...` | ✗ different (different DB) |

---

## 6. Redis Cache Client

### Class: `RedisCache` (`src/retrieval/cache.py`)

Single point of contact for all Redis operations. Every method is wrapped in `try/except` — no Redis exception ever propagates to the caller.

### Method Reference

#### `__init__(self, settings: IngestionSettings)`

```python
redis.Redis.from_url(
    settings.redis_url,
    socket_timeout=settings.redis_socket_timeout,       # 1.0s
    socket_connect_timeout=settings.redis_socket_timeout, # 1.0s
    decode_responses=True,
)
```

#### `get(self, cache_key: str) -> QueryResponse | None`

```
1. self._client.get(cache_key)
2. → HIT:
     HINCRBY {prefix}:stats hits 1
     ZINCRBY {prefix}:freq 1 {cache_key}     # bump access frequency
     Deserialize JSON → QueryResponse.model_validate_json()
     return QueryResponse
3. → MISS:
     HINCRBY {prefix}:stats misses 1
     return None
4. → EXCEPTION:
     log warning
     return None
```

#### `set(self, cache_key: str, response: QueryResponse) -> None`

```
1. response.model_dump_json() → serialized
2. self._client.set(cache_key, serialized)      # no TTL
3. ZADD {prefix}:freq NX 0 {cache_key}          # init frequency at 0
4. HINCRBY {prefix}:stats keys_written 1
5. check_memory_and_evict()
6. → EXCEPTION: log warning, do nothing
```

#### `acquire_lock(self, cache_key: str) -> bool`

```
SET {cache_key}:lock "1" NX EX 30               # atomic set-if-not-exists, 30s TTL
→ True if acquired, False if already held
→ EXCEPTION: return False (fail-open)
```

#### `release_lock(self, cache_key: str) -> None`

```
DEL {cache_key}:lock
→ EXCEPTION: log warning (lock auto-expires via TTL)
```

#### `get_stats(self) -> dict`

```
HGETALL {prefix}:stats         → hits, misses, keys_written
ZCARD {prefix}:freq            → key count
INFO MEMORY                    → used_memory bytes
→ Compute: hit_ratio, memory_used_mb, memory_pct
```

#### `ping(self) -> bool`

```
PING → True / EXCEPTION → False
```

---

## 7. Pipeline Integration

### Modified `query_finding()` in `src/retrieval/pipeline.py`

Cache-around wrapper inserted at the top of the function, before any pipeline stage:

```python
def query_finding(request, settings=None):
    settings = settings or get_ingestion_settings()
    start = time.time()

    # ── Cache lookup ────────────────────────────────────
    cache = _get_cache(settings)
    cache_key = None
    lock_acquired = False

    if cache:
        normalized = normalize_finding(request.finding_text)
        cache_key = build_cache_key(normalized, request.target_frameworks, settings)

        cached = cache.get(cache_key)
        if cached is not None:
            cached.duration_seconds = round(time.time() - start, 4)
            logger.info("Cache HIT — returning cached response")
            return cached

        lock_acquired = cache.acquire_lock(cache_key)

    # ── Full pipeline (unchanged) ───────────────────────
    # 1. Embed  →  2. Search  →  3. Rerank  →  4. Map  →  5. Critique
    response = ...  # existing pipeline code

    # ── Cache write ─────────────────────────────────────
    if cache and cache_key and lock_acquired:
        cache.set(cache_key, response)
        cache.release_lock(cache_key)

    return response
```

### Lazy Singleton Pattern

```python
_cache_instance: RedisCache | None = None

def _get_cache(settings: IngestionSettings) -> RedisCache | None:
    if not settings.redis_enabled:
        return None
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RedisCache(settings)
        if not _cache_instance.ping():
            logger.warning("Redis unreachable — caching disabled")
            return None
    return _cache_instance
```

- Instantiated once per process lifetime
- If Redis is unreachable at startup, returns `None` — pipeline runs without cache
- No retry loop — if Redis comes back, a process restart picks it up

---

## 8. Memory Management & Eviction

### Strategy: Application-Level LFU

Redis entries have **no TTL**. Memory is managed via a custom least-frequently-used eviction strategy using a Redis sorted set.

### Data Structures

```
grc:freq    → Sorted Set  (member=cache_key, score=access_count)
grc:stats   → Hash         (hits, misses, keys_written)
grc:query:* → String keys  (serialized QueryResponse JSON)
```

### Access Frequency Tracking

| Event | Redis Command |
|---|---|
| Cache `set()` | `ZADD grc:freq NX 0 {key}` — initialize at frequency 0 |
| Cache `get()` hit | `ZINCRBY grc:freq 1 {key}` — increment frequency |

### Eviction Trigger

After every `set()`, `check_memory_and_evict()` runs:

```
1. INFO MEMORY → used_memory_bytes
2. if used_memory < (max_memory_mb * 0.80 * 1024 * 1024):
     return  # under threshold, nothing to do
3. # Over 80% — evict bottom 30%
   total_keys = ZCARD grc:freq
   evict_count = int(total_keys * 0.30)
   victims = ZRANGE grc:freq 0 (evict_count - 1)  # lowest frequency first
   DEL {victims...}                                 # delete cached responses
   ZREM grc:freq {victims...}                       # remove from frequency tracker
   log: "Evicted {evict_count} keys, freed ~{bytes} memory"
```

### Why Not Rely on Redis Server Eviction?

The Redis server's `maxmemory-policy` (e.g., `allkeys-lfu`) could do this automatically. However:

1. We want to evict **only `grc:query:*` keys**, not stats or frequency tracking data
2. We want explicit logging of what was evicted and why
3. We cannot assume control over the Redis server configuration (external/managed Redis)

**Recommendation**: Set `maxmemory-policy allkeys-lfu` on the Redis server as a safety net, but do not depend on it.

---

## 9. Cache Stampede Protection

### The Problem

When 10 identical requests arrive simultaneously and the cache is cold, all 10 execute the full pipeline (2 Gemini calls each = 20 LLM calls). Only 1 result is needed.

### The Solution: Lock + Fail-Open

```
Request 1:  cache MISS → acquire_lock → ✓ LOCKED → run pipeline → cache.set → release
Request 2:  cache MISS → acquire_lock → ✗ BUSY  → run pipeline → skip cache.set
Request 3:  cache MISS → acquire_lock → ✗ BUSY  → run pipeline → skip cache.set
   ...
Request 10: cache HIT  → return cached (Request 1 finished and cached by now)
```

**Key properties:**

- No request ever **waits** for a lock. If the lock is held, the request immediately runs the pipeline.
- Only the lock holder writes to cache, preventing duplicate writes and race conditions.
- The lock auto-expires after 30 seconds (safeguard against crashes).
- In practice, requests 2-3 may still run the pipeline (they arrived before Request 1 finished). Request 4+ likely hits the cache. Cost is reduced, not eliminated, which is the correct trade-off for never-blocking.

### Worst Case

Redis is completely down: all requests run the pipeline normally. No impact. No blocking. Zero downside.

---

## 10. Stats & Observability

### Endpoint: `GET /cache/stats`

**File:** `src/api/routes/cache.py`

Returns real-time cache metrics:

```json
{
  "enabled": true,
  "connected": true,
  "hits": 1234,
  "misses": 456,
  "hit_ratio": 0.73,
  "keys_count": 890,
  "memory_used_mb": 156.2,
  "memory_max_mb": 512,
  "memory_pct": 30.5
}
```

| Field | Source | Notes |
|---|---|---|
| `enabled` | `settings.redis_enabled` | Config flag |
| `connected` | `cache.ping()` | Live connectivity check |
| `hits` | `HGET grc:stats hits` | Total cache hits since Redis start |
| `misses` | `HGET grc:stats misses` | Total cache misses |
| `hit_ratio` | Computed | `hits / (hits + misses)` |
| `keys_count` | `ZCARD grc:freq` | Number of cached queries |
| `memory_used_mb` | `INFO MEMORY` | Redis used memory in MB |
| `memory_max_mb` | `settings.redis_max_memory_mb` | Configured max |
| `memory_pct` | Computed | `(used / max) * 100` |

When Redis is disabled or unreachable:

```json
{
  "enabled": false,
  "connected": false,
  "hits": null,
  "misses": null,
  "hit_ratio": null,
  "keys_count": null,
  "memory_used_mb": null,
  "memory_max_mb": null,
  "memory_pct": null
}
```

---

## 11. Configuration Reference

All settings are added to `IngestionSettings` in `src/config/settings.py` and inherit the `INGESTION_` env prefix.

| Setting | Env Variable | Default | Description |
|---|---|---|---|
| `redis_url` | `INGESTION_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `redis_enabled` | `INGESTION_REDIS_ENABLED` | `True` | Master switch for caching |
| `redis_socket_timeout` | `INGESTION_REDIS_SOCKET_TIMEOUT` | `1.0` | Socket timeout in seconds — caps how long any Redis call can block |
| `redis_max_memory_mb` | `INGESTION_REDIS_MAX_MEMORY_MB` | `512` | Max memory budget for 80% threshold calculation |
| `redis_eviction_trigger_pct` | `INGESTION_REDIS_EVICTION_TRIGGER_PCT` | `80` | Memory % that triggers eviction |
| `redis_eviction_target_pct` | `INGESTION_REDIS_EVICTION_TARGET_PCT` | `30` | % of keys to evict when triggered |
| `redis_lock_timeout` | `INGESTION_REDIS_LOCK_TIMEOUT` | `30` | Stampede lock TTL in seconds |
| `redis_key_prefix` | `INGESTION_REDIS_KEY_PREFIX` | `grc` | Namespace prefix for all Redis keys |

### Example `.env`

```env
INGESTION_REDIS_URL=redis://redis-prod.internal:6379/0
INGESTION_REDIS_ENABLED=true
INGESTION_REDIS_MAX_MEMORY_MB=1024
```

---

## 12. File Manifest

### New Files

| File | Purpose |
|---|---|
| `src/retrieval/normalizer.py` | `normalize_finding()` + `build_cache_key()` — query normalization and deterministic key generation |
| `src/retrieval/cache.py` | `RedisCache` class — all Redis interaction (get/set/lock/evict/stats/ping) |
| `src/api/routes/cache.py` | `GET /cache/stats` endpoint |

### Modified Files

| File | Change |
|---|---|
| `src/config/settings.py` | Add `redis_*` fields to `IngestionSettings` |
| `src/retrieval/pipeline.py` | Wrap `query_finding()` with cache-around pattern + lazy `_get_cache()` singleton |
| `src/api/routes/__init__.py` | Register cache stats router |
| `requirements.txt` | Add `redis>=5.0.0,<6.0.0` |

### Untouched

- `src/gateway/` — no changes (Redis health not added to gateway)
- `docker-compose.yml` — no changes (Redis is external)
- `src/ingestion/` — no changes
- `src/retrieval/models.py` — no schema changes (QueryResponse is cached as-is)

---

## 13. Implementation Steps

### Step 1 — Add Dependency

**File:** `requirements.txt`

```diff
+ redis>=5.0.0,<6.0.0
```

```bash
pip install redis>=5.0.0,<6.0.0
```

---

### Step 2 — Add Redis Configuration

**File:** `src/config/settings.py`

Add to `IngestionSettings`:

```python
# Redis cache
redis_url: str = "redis://localhost:6379/0"
redis_enabled: bool = True
redis_socket_timeout: float = 1.0
redis_max_memory_mb: int = 512
redis_eviction_trigger_pct: int = 80
redis_eviction_target_pct: int = 30
redis_lock_timeout: int = 30
redis_key_prefix: str = "grc"
```

---

### Step 3 — Create Query Normalizer

**New file:** `src/retrieval/normalizer.py`

```python
"""GRC-aware query normalizer and deterministic cache key builder."""

import hashlib
import re

from src.config.settings import IngestionSettings

_FILLER_PREFIXES = [
    "can you map",
    "please map",
    "map this",
    "find controls for",
    "what controls apply to",
]

_FILLER_PATTERN = re.compile(
    r"^(" + "|".join(re.escape(p) for p in _FILLER_PREFIXES) + r")\s*",
    re.IGNORECASE,
)


def normalize_finding(text: str) -> str:
    """Normalize a security finding for cache key generation.

    Conservative: lowercase, trim, collapse whitespace, strip leading
    filler phrases.  Preserves ALL punctuation and technical tokens.
    """
    text = text.lower().strip()
    text = _FILLER_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_cache_key(
    finding: str,
    frameworks: list[str],
    settings: IngestionSettings,
) -> str:
    """Build a deterministic SHA-256 cache key."""
    normalized = normalize_finding(finding)
    fw_part = "|".join(sorted(frameworks))
    composite = f"{normalized}||{fw_part}||{settings.gemini_parse_model}||{settings.collection_name}"
    digest = hashlib.sha256(composite.encode()).hexdigest()
    return f"{settings.redis_key_prefix}:query:{digest}"
```

---

### Step 4 — Create Redis Cache Client

**New file:** `src/retrieval/cache.py`

```python
"""Non-blocking Redis cache client for the retrieval pipeline."""

import logging
import redis

from src.config.settings import IngestionSettings
from src.retrieval.models import QueryResponse

logger = logging.getLogger("retrieval.cache")


class RedisCache:

    def __init__(self, settings: IngestionSettings):
        self._settings = settings
        self._prefix = settings.redis_key_prefix
        self._client = redis.Redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_timeout,
            decode_responses=True,
        )

    # ── Read ────────────────────────────────────────────

    def get(self, cache_key: str) -> QueryResponse | None:
        try:
            raw = self._client.get(cache_key)
            if raw is None:
                self._client.hincrby(f"{self._prefix}:stats", "misses", 1)
                return None
            self._client.hincrby(f"{self._prefix}:stats", "hits", 1)
            self._client.zincrby(f"{self._prefix}:freq", 1, cache_key)
            return QueryResponse.model_validate_json(raw)
        except Exception:
            logger.warning("Redis get failed for %s", cache_key, exc_info=True)
            return None

    # ── Write ───────────────────────────────────────────

    def set(self, cache_key: str, response: QueryResponse) -> None:
        try:
            self._client.set(cache_key, response.model_dump_json())
            self._client.zadd(f"{self._prefix}:freq", {cache_key: 0}, nx=True)
            self._client.hincrby(f"{self._prefix}:stats", "keys_written", 1)
            self._check_memory_and_evict()
        except Exception:
            logger.warning("Redis set failed for %s", cache_key, exc_info=True)

    # ── Lock (stampede protection) ──────────────────────

    def acquire_lock(self, cache_key: str) -> bool:
        try:
            return bool(
                self._client.set(
                    f"{cache_key}:lock", "1",
                    nx=True, ex=self._settings.redis_lock_timeout,
                )
            )
        except Exception:
            return False

    def release_lock(self, cache_key: str) -> None:
        try:
            self._client.delete(f"{cache_key}:lock")
        except Exception:
            logger.warning("Redis release_lock failed", exc_info=True)

    # ── Memory management ───────────────────────────────

    def _check_memory_and_evict(self) -> None:
        try:
            info = self._client.info("memory")
            used = info.get("used_memory", 0)
            max_bytes = self._settings.redis_max_memory_mb * 1024 * 1024
            trigger = max_bytes * (self._settings.redis_eviction_trigger_pct / 100)

            if used < trigger:
                return

            total_keys = self._client.zcard(f"{self._prefix}:freq")
            if total_keys == 0:
                return

            evict_count = max(1, int(total_keys * self._settings.redis_eviction_target_pct / 100))
            victims = self._client.zrange(f"{self._prefix}:freq", 0, evict_count - 1)

            if victims:
                self._client.delete(*victims)
                self._client.zrem(f"{self._prefix}:freq", *victims)
                logger.info("Evicted %d least-used cache keys (memory was %.1f%%)",
                            len(victims), (used / max_bytes) * 100)
        except Exception:
            logger.warning("Redis eviction check failed", exc_info=True)

    # ── Stats ───────────────────────────────────────────

    def get_stats(self) -> dict:
        try:
            stats = self._client.hgetall(f"{self._prefix}:stats")
            hits = int(stats.get("hits", 0))
            misses = int(stats.get("misses", 0))
            total = hits + misses
            info = self._client.info("memory")
            used_mb = info.get("used_memory", 0) / (1024 * 1024)
            return {
                "hits": hits,
                "misses": misses,
                "hit_ratio": round(hits / total, 4) if total > 0 else 0.0,
                "keys_count": self._client.zcard(f"{self._prefix}:freq"),
                "memory_used_mb": round(used_mb, 2),
                "memory_max_mb": self._settings.redis_max_memory_mb,
                "memory_pct": round((used_mb / self._settings.redis_max_memory_mb) * 100, 1),
            }
        except Exception:
            logger.warning("Redis get_stats failed", exc_info=True)
            return {}

    # ── Health ──────────────────────────────────────────

    def ping(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False
```

---

### Step 5 — Integrate into Pipeline

**File:** `src/retrieval/pipeline.py`

Add imports:

```python
from src.retrieval.cache import RedisCache
from src.retrieval.normalizer import build_cache_key
```

Add lazy singleton:

```python
_cache_instance: RedisCache | None = None

def _get_cache(settings: IngestionSettings) -> RedisCache | None:
    if not settings.redis_enabled:
        return None
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RedisCache(settings)
        if not _cache_instance.ping():
            logger.warning("Redis unreachable — caching disabled")
            return None
    return _cache_instance
```

Wrap `query_finding()` — add at the top of the function, before Stage 1:

```python
# ── Cache lookup ────────────────────────────────────
cache = _get_cache(settings)
cache_key = None
lock_acquired = False

if cache:
    cache_key = build_cache_key(request.finding_text, request.target_frameworks, settings)
    cached = cache.get(cache_key)
    if cached is not None:
        cached.duration_seconds = round(time.time() - start, 4)
        logger.info("Cache HIT for key %s", cache_key[-12:])
        return cached
    lock_acquired = cache.acquire_lock(cache_key)
    logger.info("Cache MISS — lock %s", "acquired" if lock_acquired else "busy (another request caching)")
```

After the pipeline completes (before `return`):

```python
# ── Cache write ─────────────────────────────────────
if cache and cache_key and lock_acquired:
    cache.set(cache_key, response)
    cache.release_lock(cache_key)
    logger.info("Cached response for key %s", cache_key[-12:])
```

---

### Step 6 — Add Stats Endpoint

**New file:** `src/api/routes/cache.py`

```python
"""Cache stats route."""

import logging
from fastapi import APIRouter
from src.config.settings import get_ingestion_settings
from src.retrieval.cache import RedisCache

logger = logging.getLogger("api.cache")
router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
def cache_stats():
    settings = get_ingestion_settings()
    if not settings.redis_enabled:
        return {"enabled": False, "connected": False}

    cache = RedisCache(settings)
    connected = cache.ping()
    if not connected:
        return {"enabled": True, "connected": False}

    stats = cache.get_stats()
    return {"enabled": True, "connected": True, **stats}
```

---

### Step 7 — Register Route

**File:** `src/api/routes/__init__.py`

```python
from src.api.routes.cache import router as cache_router
router.include_router(cache_router)
```

---

### Step 8 — Verify

```bash
# Start Redis (if not already running)
redis-cli ping   # expect: PONG

# Run the API
uvicorn src.api.main:app --reload

# First query — should log "Cache MISS"
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "finding_text": "MySQL remote login successful",
    "target_frameworks": ["iso_27001", "iso_27002"]
  }'

# Same query — should log "Cache HIT"
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "finding_text": "MySQL remote login successful",
    "target_frameworks": ["iso_27001", "iso_27002"]
  }'

# Check stats
curl http://localhost:8000/cache/stats
# → {"enabled":true,"connected":true,"hits":1,"misses":1,"hit_ratio":0.5,...}
```

---

## 14. Testing Strategy

### Unit Tests — `tests/test_normalizer.py`

| Test Case | Input | Expected Output |
|---|---|---|
| Lowercase only | `"MySQL Remote Login Successful"` | `"mysql remote login successful"` |
| Whitespace collapse | `"  MySQL  remote   login  "` | `"mysql remote login"` |
| Filler prefix strip | `"Please map MySQL remote login successful"` | `"mysql remote login successful"` |
| Punctuation preserved | `"PCI-DSS v4.0 control A.8.20"` | `"pci-dss v4.0 control a.8.20"` |
| Mid-sentence "map" preserved | `"network map exposed on port 80"` | `"network map exposed on port 80"` |
| Key determinism | Same finding, different framework order | Same cache key |
| Key differentiation | Same finding, different framework count | Different cache key |
| Key model sensitivity | Same query, flash vs pro model | Different cache key |

### Unit Tests — `tests/test_cache.py`

| Test Case | Behavior |
|---|---|
| `get`/`set` round-trip | Serialize `QueryResponse`, store, retrieve, validate equality |
| `get` on missing key | Returns `None`, increments misses |
| Redis `ConnectionError` on `get` | Returns `None`, no exception propagates |
| Redis `ConnectionError` on `set` | Silent failure, no exception propagates |
| `acquire_lock` when free | Returns `True` |
| `acquire_lock` when held | Returns `False` |
| Redis `ConnectionError` on `acquire_lock` | Returns `False` (fail-open) |

### Integration Tests — `tests/test_cache_integration.py`

| Test | Validation |
|---|---|
| Two identical queries | Second returns faster; stats show 1 miss + 1 hit |
| Different frameworks | Same finding + different framework list → cache miss |
| `/cache/stats` endpoint | Returns valid JSON with all expected fields |
| Redis down | Pipeline completes normally; response is valid |
| Stampede (5 concurrent) | Only 1 writes to cache; none block |
| Eviction | Set `redis_max_memory_mb=1`; flood queries; verify eviction logged and keys removed |

---

## 15. Operational Notes

### Redis Server Recommendations

```
# redis.conf (recommended, not required)
maxmemory 512mb
maxmemory-policy allkeys-lfu
```

Our application handles eviction independently, but the server policy acts as a safety net if application-level eviction fails.

### Monitoring Checklist

| Metric | Healthy Range | Action if Outside |
|---|---|---|
| `hit_ratio` | > 0.5 after warm-up | Check if findings are diverse (low ratio is normal for unique findings) |
| `memory_pct` | < 80% | Eviction should handle this automatically; increase `redis_max_memory_mb` if eviction is too aggressive |
| `connected` | `true` | Check Redis server health; API continues without cache |
| Response time (cached) | < 10ms | Check Redis network latency |
| Response time (uncached) | < 5s | Normal pipeline performance (not a cache issue) |

### Disabling Cache

```env
INGESTION_REDIS_ENABLED=false
```

The pipeline runs exactly as before — zero Redis dependency. Useful for debugging or when Redis is being migrated.

### Cache Serialization Size

A typical `QueryResponse` with 5 `ControlMapping` objects serializes to ~3-5 KB. At 512 MB max memory:

- ~100,000 unique cached queries
- Far exceeds typical enterprise finding diversity (<10,000 unique findings per year)
