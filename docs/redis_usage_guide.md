# Redis Usage Guide — GRC Module

> **Date:** March 18, 2026  
> **Applies to:** `feature/Sriharsha/redis-addition` branch  
> **Redis version:** 7.x (Alpine Docker image)

---

## Table of Contents

1. [Starting & Stopping Redis](#1-starting--stopping-redis)
2. [Redis CLI Reference](#2-redis-cli-reference)
3. [Redis UI Tools](#3-redis-ui-tools)
4. [Verifying Data Flow](#4-verifying-data-flow)
5. [Configuration Changes](#5-configuration-changes)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Starting & Stopping Redis

### Start (Docker)

```bash
docker run -d \
  --name grc-redis \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --maxmemory 512mb --maxmemory-policy allkeys-lfu
```

| Flag | Purpose |
|---|---|
| `-d` | Run detached (background) |
| `--name grc-redis` | Container name for easy reference |
| `-p 6379:6379` | Map host port 6379 → container port 6379 |
| `--maxmemory 512mb` | Server-level memory cap (safety net) |
| `--maxmemory-policy allkeys-lfu` | Evict least-frequently-used keys if server hits limit |

### Stop & Remove

```bash
docker stop grc-redis
docker rm grc-redis
```

### Restart (preserves data if not removed)

```bash
docker restart grc-redis
```

### Check if Running

```bash
docker ps --filter name=grc-redis
```

### With Persistent Storage (data survives container removal)

```bash
docker run -d \
  --name grc-redis \
  -p 6379:6379 \
  -v grc-redis-data:/data \
  redis:7-alpine \
  redis-server --maxmemory 512mb --maxmemory-policy allkeys-lfu --appendonly yes
```

The `-v grc-redis-data:/data` creates a named Docker volume. `--appendonly yes` enables AOF persistence so data survives restarts.

---

## 2. Redis CLI Reference

### Connecting

```bash
# From Docker container
docker exec -it grc-redis redis-cli

# Or directly if redis-cli is installed locally
redis-cli -h localhost -p 6379
```

Once connected, you'll see the `127.0.0.1:6379>` prompt.

### Essential Commands

#### Health Check

```redis
PING
# → PONG
```

#### View All GRC Keys

```redis
KEYS grc:*
```

Example output:
```
1) "grc:query:a3f2b7c1d9e4f8a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5"
2) "grc:query:b7c1d9e4f8a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6"
3) "grc:stats"
4) "grc:freq"
```

#### View a Cached Response

```redis
# Get the JSON for a specific cache key
GET grc:query:<hash>

# Pretty-print it (pipe through python outside redis-cli)
```

From your shell (outside redis-cli):
```bash
docker exec grc-redis redis-cli GET "grc:query:<hash>" | python -m json.tool
```

#### View Cache Statistics

```redis
HGETALL grc:stats
```

Example output:
```
1) "hits"
2) "15"
3) "misses"
4) "8"
5) "keys_written"
6) "8"
```

#### View Access Frequency (LFU Tracking)

```redis
# All keys sorted by access frequency (ascending)
ZRANGE grc:freq 0 -1 WITHSCORES

# Top 5 most accessed keys
ZREVRANGE grc:freq 0 4 WITHSCORES

# Total cached query count
ZCARD grc:freq
```

#### Memory Usage

```redis
# Overall server memory
INFO memory

# Memory used by a specific key (bytes)
MEMORY USAGE grc:query:<hash>

# Total DB key count
DBSIZE
```

Key fields from `INFO memory`:
| Field | Meaning |
|---|---|
| `used_memory_human` | Total memory used (human-readable) |
| `used_memory_peak_human` | Peak memory ever used |
| `maxmemory_human` | Configured max memory |
| `maxmemory_policy` | Eviction policy |

#### Delete Specific Keys

```redis
# Delete one cached response
DEL grc:query:<hash>

# Delete all GRC keys (use with caution)
# From shell:
docker exec grc-redis redis-cli KEYS "grc:query:*" | xargs docker exec -i grc-redis redis-cli DEL

# Reset stats
DEL grc:stats

# Reset frequency tracker
DEL grc:freq
```

#### Flush Everything (nuclear option)

```redis
FLUSHDB
```

> **Warning:** This deletes ALL keys in the current database, not just GRC keys.

#### Monitor Commands in Real-Time

```redis
MONITOR
```

This streams every command Redis receives. Very useful for debugging — you'll see `GET`, `SET`, `HINCRBY`, `ZINCRBY` commands flowing in real-time as the API processes queries. Press `Ctrl+C` to stop.

---

## 3. Redis UI Tools

### Option A: RedisInsight (Recommended — Free, by Redis Inc.)

**Install:**
```bash
docker run -d --name redisinsight -p 5540:5540 redis/redisinsight:latest
```

**Use:**
1. Open browser → `http://localhost:5540`
2. Click **"Add Redis Database"**
3. Enter:
   - **Host:** `host.docker.internal` (or `localhost` if on Linux)
   - **Port:** `6379`
   - **Database Alias:** `GRC Cache`
4. Click **Connect**

**Features:**
- Browse all keys with a tree view (filter by `grc:*`)
- View/edit JSON values inline
- Built-in CLI terminal
- Memory analysis tool
- Slow log viewer
- Real-time key monitoring

### Option B: Another Redis Desktop Manager (ARDM)

Free open-source GUI. Download from: https://goanother.com/

1. Install and open
2. **Connection Settings:**
   - Name: `GRC Local`
   - Host: `127.0.0.1`
   - Port: `6379`
3. Connect → browse keys in the left panel
4. Click any key to view its type, TTL, and value

### Option C: VS Code Extension

Search for **"Redis"** in VS Code Extensions marketplace. The **"Redis Explorer"** or **"Redis Client"** extensions let you browse keys directly in the editor sidebar.

---

## 4. Verifying Data Flow

### Step-by-Step Verification

#### 1. Start monitoring Redis

Open a terminal and run:
```bash
docker exec -it grc-redis redis-cli MONITOR
```

Leave this running — it shows every Redis command in real-time.

#### 2. Send a query to the API

In another terminal:
```bash
curl -s -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"finding_text": "MySQL remote login successful", "target_frameworks": ["iso_27001"]}' | python -m json.tool
```

#### 3. What you should see in the MONITOR output

**First request (cache MISS):**
```
"GET" "grc:query:a3f2..."              ← cache lookup (returns nil)
"HINCRBY" "grc:stats" "misses" "1"     ← increment miss counter
"SET" "grc:query:a3f2...:lock" "1" "NX" "EX" "30"  ← acquire stampede lock
... (pipeline runs) ...
"SET" "grc:query:a3f2..." "{...json...}"  ← store result
"ZADD" "grc:freq" "NX" "0" "grc:query:a3f2..."  ← init frequency
"HINCRBY" "grc:stats" "keys_written" "1"   ← increment write counter
"INFO" "memory"                            ← check memory for eviction
"DEL" "grc:query:a3f2...:lock"             ← release lock
```

**Second identical request (cache HIT):**
```
"GET" "grc:query:a3f2..."              ← cache lookup (returns data)
"HINCRBY" "grc:stats" "hits" "1"       ← increment hit counter
"ZINCRBY" "grc:freq" "1" "grc:query:a3f2..."  ← bump frequency
```

No `SET`, no pipeline stages — just a fast read.

#### 4. Verify via the stats endpoint

```bash
curl -s http://localhost:8000/api/v1/cache/stats | python -m json.tool
```

Expected after 1 miss + 1 hit:
```json
{
    "enabled": true,
    "connected": true,
    "hits": 1,
    "misses": 1,
    "hit_ratio": 0.5,
    "keys_count": 1,
    "memory_used_mb": 1.23,
    "memory_max_mb": 1024,
    "memory_pct": 0.1
}
```

#### 5. Verify normalization is working

```bash
# This should also be a HIT (filler stripped + lowercased = same key)
curl -s -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"finding_text": "Please map MySQL Remote Login Successful", "target_frameworks": ["iso_27001"]}'
```

Check stats again — `hits` should increment to `2`, `keys_count` stays at `1`.

#### 6. Verify key size

```bash
docker exec grc-redis redis-cli KEYS "grc:query:*" | head -1 | xargs -I{} docker exec grc-redis redis-cli MEMORY USAGE "{}"
```

Typical response is 3000–5000 bytes (~3-5 KB per cached `QueryResponse`).

---

## 5. Configuration Changes

### Application-Level Settings (`.env` file)

All Redis settings live in your `.env` under the `GRC_REDIS__` prefix:

```env
GRC_REDIS__URL=redis://localhost:6379/0
GRC_REDIS__ENABLED=true
GRC_REDIS__SOCKET_TIMEOUT=1.0
GRC_REDIS__MAX_MEMORY_MB=1024
GRC_REDIS__EVICTION_TRIGGER_PCT=80
GRC_REDIS__EVICTION_TARGET_PCT=30
GRC_REDIS__LOCK_TIMEOUT=30
GRC_REDIS__KEY_PREFIX=grc
```

> **After any `.env` change, restart the API** (`uvicorn`) to pick up new values.

#### Increase Application Memory Budget

Change in `.env`:
```env
GRC_REDIS__MAX_MEMORY_MB=2048
```

This controls when the **application-level eviction** kicks in (at 80% of this value). It does NOT change the Redis server's own limit.

#### Disable Caching Entirely

```env
GRC_REDIS__ENABLED=false
```

The pipeline runs exactly as before — zero Redis dependency.

#### Change Eviction Aggressiveness

```env
# Trigger eviction when memory hits 90% instead of 80%
GRC_REDIS__EVICTION_TRIGGER_PCT=90

# Evict only 20% of keys instead of 30%
GRC_REDIS__EVICTION_TARGET_PCT=20
```

#### Connect to a Remote Redis

```env
GRC_REDIS__URL=redis://username:password@redis-prod.internal:6379/0
```

For Redis with TLS:
```env
GRC_REDIS__URL=rediss://username:password@redis-prod.internal:6380/0
```

Note the `rediss://` (double-s) scheme for TLS connections.

---

### Server-Level Settings (Redis itself)

These are set on the Redis server, not in `.env`.

#### Increase Redis Server Memory (running container)

```bash
# Check current setting
docker exec grc-redis redis-cli CONFIG GET maxmemory
# → "maxmemory" "536870912" (512MB in bytes)

# Increase to 1GB (takes effect immediately, no restart needed)
docker exec grc-redis redis-cli CONFIG SET maxmemory 1gb

# Verify
docker exec grc-redis redis-cli CONFIG GET maxmemory
# → "maxmemory" "1073741824"
```

> **Important:** This is a runtime change. It resets when the container restarts. To make it permanent, include it in the `docker run` command:

```bash
docker run -d --name grc-redis -p 6379:6379 \
  redis:7-alpine redis-server --maxmemory 1gb --maxmemory-policy allkeys-lfu
```

#### Check Current Server Configuration

```bash
docker exec grc-redis redis-cli CONFIG GET maxmemory
docker exec grc-redis redis-cli CONFIG GET maxmemory-policy

# All config in one shot
docker exec grc-redis redis-cli CONFIG GET "*"
```

#### Change Eviction Policy on Running Server

```bash
docker exec grc-redis redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

Options: `noeviction`, `allkeys-lru`, `allkeys-lfu`, `volatile-lru`, `volatile-lfu`, `allkeys-random`

**Recommended for GRC:** `allkeys-lfu` (matches our application-level LFU strategy).

---

### Keeping App and Server Settings in Sync

| Setting | `.env` (App) | Redis Server | Relationship |
|---|---|---|---|
| Memory limit | `GRC_REDIS__MAX_MEMORY_MB=1024` | `--maxmemory 1gb` | App triggers eviction at 80% of its value. Server is the hard cap. **Set server ≥ app value.** |
| Eviction policy | N/A (app handles it) | `--maxmemory-policy allkeys-lfu` | Server policy is a safety net if app eviction fails. |

**Example:** If you set `GRC_REDIS__MAX_MEMORY_MB=2048` in `.env`, also increase the server:
```bash
docker exec grc-redis redis-cli CONFIG SET maxmemory 2gb
```

---

## 6. Troubleshooting

### `/cache/stats` returns `{"enabled": true, "connected": false}`

Redis is enabled but unreachable. Check:
```bash
docker ps --filter name=grc-redis        # Is container running?
docker exec grc-redis redis-cli PING     # Can it respond?
```

If container is stopped:
```bash
docker start grc-redis
```

### `/cache/stats` returns `{"enabled": true, "connected": true}` but no metrics

The `get_stats()` method hit an error and returned `{}`. Check API logs for `Redis get_stats failed` warnings.

### Cache never hits (hit_ratio stays at 0)

1. Verify with `MONITOR` that `GET` commands are reaching Redis
2. Check that findings are textually similar (normalization is conservative — `"mysql login"` ≠ `"MySQL remote login successful"`)
3. Check framework lists match exactly (order doesn't matter, but the set must be identical)

### High memory but no eviction

The app checks memory after every `SET`. If `INFO memory → used_memory` is below `GRC_REDIS__MAX_MEMORY_MB × 0.80`, no eviction runs. Possible causes:
- `GRC_REDIS__MAX_MEMORY_MB` is set too high relative to actual data
- Memory reported by `INFO` includes Redis overhead, not just your keys

### API slow even with cache enabled

```bash
# Check Redis latency
docker exec grc-redis redis-cli --latency
# Should show < 1ms. If > 5ms, check Docker networking
```

### Reset everything and start fresh

```bash
docker exec grc-redis redis-cli FLUSHDB
```

Or remove and recreate the container:
```bash
docker stop grc-redis; docker rm grc-redis
docker run -d --name grc-redis -p 6379:6379 redis:7-alpine redis-server --maxmemory 1gb --maxmemory-policy allkeys-lfu
```
