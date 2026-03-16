# GRC Module — Infrastructure Services

> **Date:** 2026-03-14  
> **Status:** Running  

---

## Overview

Four Docker containers providing the AI infrastructure backbone:

| Service | Container | Image | Port | Purpose |
|---|---|---|---|---|
| **Qdrant** | `grc-qdrant` | `qdrant/qdrant:v1.12.4` | `6333` (REST), `6334` (gRPC) | Vector database — one collection per GRC framework |
| **Embedder** | `grc-embedder` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | `8081` | Embedding model — `BAAI/bge-large-en-v1.5` (1024-dim) |
| **Reranker** | `grc-reranker` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | `8082` | Reranker model — `BAAI/bge-reranker-v2-m3` |
| **Gateway** | `grc-gateway` | Custom (Python 3.12 + FastAPI) | `8000` | Health-check aggregator for all services |

---

## Quick Start

```bash
# Start all services (first run downloads ~1.5GB of model files)
docker compose up -d

# Check status
docker compose ps

# Hit the gateway health endpoint
curl http://localhost:8000/health
```

**First run timing:** Qdrant starts in ~10s. TEI containers download models on first boot (~2-5 min depending on network). Subsequent starts use cached volumes and are fast.

---

## Service Details

### Qdrant (Vector Database)

- **Dashboard:** http://localhost:6333/dashboard
- **REST API:** http://localhost:6333
- **gRPC:** localhost:6334
- **Storage:** Docker volume `qdrant_data` (persistent across restarts)
- **Health endpoint:** `GET /healthz`

### Embedder (bge-large-en-v1.5)

- **API:** http://localhost:8081
- **Model:** `BAAI/bge-large-en-v1.5` — 1024-dimensional embeddings, top MTEB rank
- **Cache:** Docker volume `embedder_cache` (model files cached after first download)
- **Health endpoint:** `GET /health`
- Runs on CPU via ONNX runtime

### Reranker (bge-reranker-v2-m3)

- **API:** http://localhost:8082
- **Model:** `BAAI/bge-reranker-v2-m3` — cross-encoder reranker
- **Cache:** Docker volume `reranker_cache` (model files cached after first download)
- **Health endpoint:** `GET /health`
- Falls back to SafeTensors backend when ONNX weights are unavailable

### API Gateway

- **URL:** http://localhost:8000
- **Docs:** http://localhost:8000/docs (Swagger UI)
- **Single endpoint:** `GET /health` — aggregates health of Qdrant, Embedder, and Reranker

**Health response example:**

```json
{
  "gateway": "healthy",
  "qdrant": { "status": "healthy", "latency_ms": 64.5 },
  "embedder": { "status": "healthy", "latency_ms": 18.2 },
  "reranker": { "status": "healthy", "latency_ms": 11.7 }
}
```

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         API Gateway              │
                    │     localhost:8000                │
                    │     (FastAPI health aggregator)   │
                    └──────┬──────────┬──────────┬─────┘
                           │          │          │
              ┌────────────┘          │          └────────────┐
              ▼                       ▼                       ▼
   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │      Qdrant       │   │     Embedder      │   │     Reranker      │
   │  localhost:6333   │   │  localhost:8081   │   │  localhost:8082   │
   │  (Vector DB)      │   │  (bge-large)      │   │  (bge-reranker)   │
   └──────────────────┘   └──────────────────┘   └──────────────────┘
```

---

## Project Structure

```
GRC_module/
├── docker-compose.yml              # All 4 services defined here
├── src/
│   ├── __init__.py
│   └── gateway/
│       ├── __init__.py
│       ├── Dockerfile              # Gateway container image
│       ├── requirements.txt        # fastapi, uvicorn, httpx, pydantic
│       ├── main.py                 # FastAPI app entrypoint
│       ├── config.py               # Settings (env vars: QDRANT_URL, EMBEDDER_URL, RERANKER_URL)
│       ├── routes.py               # GET /health endpoint
│       ├── schemas.py              # HealthResponse, ServiceHealth models
│       ├── clients.py              # Async health-check functions
│       └── gateway_client.py       # Python SDK for other services to call the gateway
```

---

## Configuration

All settings come from environment variables (set in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant REST endpoint |
| `EMBEDDER_URL` | `http://localhost:8081` | TEI embedder endpoint |
| `RERANKER_URL` | `http://localhost:8082` | TEI reranker endpoint |
| `LOG_LEVEL` | `info` | Logging level |

---

## Common Operations

```bash
# Stop all services
docker compose down

# View logs
docker compose logs -f              # all services
docker compose logs -f embedder     # specific service

# Restart a single service
docker compose restart qdrant

# Rebuild gateway after code changes
docker compose up -d gateway --build

# Reset everything (delete volumes = re-download models)
docker compose down -v
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| TEI container in restart loop | Model download failed (network issue) | Check `docker logs grc-embedder`; ensure internet access |
| Qdrant healthcheck "unhealthy" | No `curl` in container; uses bash TCP probe | Already handled — healthcheck uses `/dev/tcp` |
| Gateway shows "unhealthy" for a service | Service hasn't finished starting | Wait for TEI model download; check `docker compose ps` |
| `relative URL without a base` in TEI logs | TEI image `cpu-1.5` has a URL builder bug | Use `cpu-1.6` (already set) |

---

## What's Next

This infrastructure layer provides the foundation. Upcoming services to add:

- **PostgreSQL** — state management, caching, audit logs
- **Redis** — L1 cache, Celery message broker
- **Celery workers** — async finding processing
- **Docling** — Dockerized PDF extraction

Operations (embed, rerank, search, upsert) will be added to the gateway as the ingestion pipeline is built.
