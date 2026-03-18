"""Cache stats route — exposes Redis cache metrics.

Single-responsibility: HTTP interface for cache observability.
"""

import logging

from fastapi import APIRouter

from src.config.settings import get_ingestion_settings
from src.retrieval.cache import RedisCache

logger = logging.getLogger("api.cache")

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
def cache_stats():
    """Return Redis cache metrics (hit ratio, memory, key count).

    Returns a degraded response if Redis is disabled or unreachable —
    never raises.
    """
    settings = get_ingestion_settings()

    if not settings.redis_enabled:
        return {"enabled": False, "connected": False}

    cache = RedisCache(settings)
    connected = cache.ping()

    if not connected:
        return {"enabled": True, "connected": False}

    stats = cache.get_stats()
    return {"enabled": True, "connected": True, **stats}
