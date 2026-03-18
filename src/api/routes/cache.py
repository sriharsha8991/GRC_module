"""Cache stats route — exposes Redis cache metrics.

Single-responsibility: HTTP interface for cache observability.
"""

import logging

from fastapi import APIRouter

from src.config.settings import get_settings
from src.retrieval.pipeline import _get_cache

logger = logging.getLogger("api.cache")

router = APIRouter(prefix="/cache", tags=["cache"])


@router.get("/stats")
def cache_stats():
    """Return Redis cache metrics for observability.

    Provides real-time statistics on cache performance including hit/miss
    ratio, key count, and memory usage. Safe to poll frequently — uses
    the shared Redis connection singleton.

    **Response fields:**
    - `enabled` / `connected` — cache status flags.
    - `hits`, `misses`, `hit_ratio` — cache effectiveness.
    - `keys_count` — number of cached query responses.
    - `memory_used_mb`, `memory_max_mb`, `memory_pct` — Redis memory pressure.

    Returns a degraded payload (`enabled: false` or `connected: false`) if
    Redis is disabled or unreachable — never raises.
    """
    settings = get_settings()

    if not settings.redis.enabled:
        return {"enabled": False, "connected": False}

    cache = _get_cache(settings)
    if cache is None:
        return {"enabled": True, "connected": False}

    stats = cache.get_stats()
    return {"enabled": True, "connected": True, **stats}
