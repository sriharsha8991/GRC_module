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


async def check_api(settings: Settings) -> dict:
    return await _check_health(f"{settings.api_url}/health")


async def check_qdrant(settings: Settings) -> dict:
    return await _check_health(f"{settings.qdrant_url}/healthz")


async def check_redis(settings: Settings) -> dict:
    """Check Redis by hitting its TCP port (no HTTP endpoint)."""
    import socket
    start = time.perf_counter()
    try:
        # Parse host:port from redis://host:port
        url = settings.redis_url
        host = url.split("://")[1].split(":")[0] if "://" in url else "localhost"
        port_str = url.split(":")[-1].split("/")[0]
        port = int(port_str) if port_str.isdigit() else 6379
        sock = socket.create_connection((host, port), timeout=3)
        sock.sendall(b"PING\r\n")
        resp = sock.recv(16)
        sock.close()
        ok = b"PONG" in resp
        return {
            "status": "healthy" if ok else "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": str(e),
        }
