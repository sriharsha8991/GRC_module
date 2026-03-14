"""Health-check clients for downstream services."""

import time
import httpx
import logging

from .config import Settings

logger = logging.getLogger("gateway.clients")


async def _check_health(url: str, timeout: float = 5.0) -> dict:
    """Ping a service health endpoint and return status + latency."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return {
                "status": "healthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            }
    except Exception as e:
        logger.warning("Health check failed for %s: %s", url, e)
        return {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": str(e),
        }


async def check_qdrant(settings: Settings) -> dict:
    return await _check_health(f"{settings.qdrant_url}/healthz")


async def check_embedder(settings: Settings) -> dict:
    return await _check_health(f"{settings.embedder_url}/health")


async def check_reranker(settings: Settings) -> dict:
    return await _check_health(f"{settings.reranker_url}/health")
