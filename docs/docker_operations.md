# Docker Compose — Operations Guide

## Prerequisites

- Docker Desktop running
- Terminal open at the project root (`GRC_module/`)
- `.env` file present with all `GRC_*` variables

---

## Quick Reference

| Action | Command |
|---|---|
| Start everything | `docker compose up -d` |
| Stop everything | `docker compose down` |
| Rebuild & start | `docker compose up -d --build` |
| View logs | `docker compose logs -f` |
| Check status | `docker compose ps` |

---

## Starting Services

```bash
# Start all containers in background
docker compose up -d

# Start and force rebuild images
docker compose up -d --build

# Start only specific services
docker compose up -d api redis qdrant
```

Startup order is handled automatically via `depends_on`:
```
qdrant + redis  →  api (waits for both healthy)  →  gateway (waits for api healthy)
```

---

## Stopping Services

```bash
# Stop and remove containers (keeps volumes/data)
docker compose down

# Stop and remove containers + delete all data volumes
docker compose down -v

# Stop without removing (can resume with `up`)
docker compose stop

# Stop a single service
docker compose stop api
```

> **Warning:** `docker compose down -v` deletes Qdrant vector data and Redis cache. Only use when you want a full reset.

---

## Viewing Logs

```bash
# All services, follow mode
docker compose logs -f

# Single service
docker compose logs -f api
docker compose logs -f gateway

# Last 100 lines only
docker compose logs --tail 100 api

# Since a specific time
docker compose logs --since 5m api
```

---

## Rebuilding

```bash
# Rebuild a single service (e.g. after code changes)
docker compose build api
docker compose up -d api

# Rebuild from scratch (no cache)
docker compose build --no-cache api

# Rebuild everything from scratch
docker compose build --no-cache
docker compose up -d
```

> **Tip:** After editing Python code in `src/`, you must rebuild the affected container. Code changes are not hot-reloaded inside Docker.

---

## Checking Health

```bash
# Container status
docker compose ps

# Gateway aggregated health (API + Qdrant + Redis)
curl http://localhost:8000/health

# Direct API health
curl http://localhost:8080/health

# PowerShell equivalent
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

---

## Service Ports

| Service | Internal | Host | URL |
|---|---|---|---|
| Qdrant | 6333 | — | Internal only |
| Redis | 6379 | — | Internal only |
| API | 8080 | 8080 | `http://localhost:8080` |
| Gateway | 8000 | 8000 | `http://localhost:8000` |

---

## Common Scenarios

### Full reset (wipe all data)
```bash
docker compose down -v
docker compose up -d --build
```

### Update code and redeploy API only
```bash
docker compose build api
docker compose up -d api
```

### Update gateway only
```bash
docker compose build --no-cache gateway
docker compose up -d gateway
```

### Restart a single service
```bash
docker compose restart api
```

### Shell into a running container
```bash
docker compose exec api bash
docker compose exec gateway bash
docker compose exec redis redis-cli
```

### Check resource usage
```bash
docker stats
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `dependency failed to start: unhealthy` | Qdrant or Redis healthcheck failing | Check `docker compose logs qdrant` / `redis` |
| API returns 500 | Missing env vars or Qdrant not ready | Verify `.env` has all `GRC_*` vars; check `docker compose logs api` |
| Gateway returns 502 | API container not healthy yet | Wait for API healthcheck to pass; check `docker compose ps` |
| Code changes not reflected | Stale Docker image | Rebuild: `docker compose build --no-cache api` |
| Port already in use | Another process on 8080/8000 | Stop the conflicting process or change ports in `docker-compose.yml` |
