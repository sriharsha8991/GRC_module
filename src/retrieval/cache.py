"""Non-blocking Redis cache client for the retrieval pipeline.

Single-responsibility: encapsulates ALL Redis interaction.  No other
module in the codebase touches Redis directly.

Every public method is wrapped in try/except — a Redis failure NEVER
propagates to the caller.  On any error the method returns a safe
default (None / False / empty dict) and logs a warning.
"""

import logging

import redis

from src.config.settings import IngestionSettings
from src.retrieval.models import QueryResponse

logger = logging.getLogger("retrieval.cache")


class RedisCache:
    """Cache-around client with stampede protection and LFU eviction."""

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
        """Retrieve a cached QueryResponse.  Returns None on miss or error."""
        try:
            raw = self._client.get(cache_key)
            stats_key = f"{self._prefix}:stats"
            if raw is None:
                self._client.hincrby(stats_key, "misses", 1)
                return None
            self._client.hincrby(stats_key, "hits", 1)
            self._client.zincrby(f"{self._prefix}:freq", 1, cache_key)
            return QueryResponse.model_validate_json(raw)
        except Exception:
            logger.warning("Redis GET failed for %s", cache_key, exc_info=True)
            return None

    # ── Write ───────────────────────────────────────────

    def set(self, cache_key: str, response: QueryResponse) -> None:
        """Store a QueryResponse.  Silent no-op on error."""
        try:
            self._client.set(cache_key, response.model_dump_json())
            self._client.zadd(f"{self._prefix}:freq", {cache_key: 0}, nx=True)
            self._client.hincrby(f"{self._prefix}:stats", "keys_written", 1)
            self._check_memory_and_evict()
        except Exception:
            logger.warning("Redis SET failed for %s", cache_key, exc_info=True)

    # ── Lock (stampede protection) ──────────────────────

    def acquire_lock(self, cache_key: str) -> bool:
        """Try to acquire a stampede lock.  Returns False on contention or error."""
        try:
            return bool(
                self._client.set(
                    f"{cache_key}:lock",
                    "1",
                    nx=True,
                    ex=self._settings.redis_lock_timeout,
                )
            )
        except Exception:
            return False

    def release_lock(self, cache_key: str) -> None:
        """Release a stampede lock.  Silent on error (lock auto-expires)."""
        try:
            self._client.delete(f"{cache_key}:lock")
        except Exception:
            logger.warning("Redis release_lock failed for %s", cache_key, exc_info=True)

    # ── Memory management ───────────────────────────────

    def _check_memory_and_evict(self) -> None:
        """Evict least-frequently-used keys if memory exceeds threshold."""
        try:
            info = self._client.info("memory")
            used = info.get("used_memory", 0)
            max_bytes = self._settings.redis_max_memory_mb * 1024 * 1024
            trigger = max_bytes * (self._settings.redis_eviction_trigger_pct / 100)

            if used < trigger:
                return

            freq_key = f"{self._prefix}:freq"
            total_keys = self._client.zcard(freq_key)
            if total_keys == 0:
                return

            evict_count = max(
                1,
                int(total_keys * self._settings.redis_eviction_target_pct / 100),
            )
            victims = self._client.zrange(freq_key, 0, evict_count - 1)

            if victims:
                self._client.delete(*victims)
                self._client.zrem(freq_key, *victims)
                logger.info(
                    "Evicted %d least-used cache keys (memory was %.1f%%)",
                    len(victims),
                    (used / max_bytes) * 100,
                )
        except Exception:
            logger.warning("Redis eviction check failed", exc_info=True)

    # ── Stats ───────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return cache metrics.  Returns empty dict on error."""
        try:
            stats = self._client.hgetall(f"{self._prefix}:stats")
            hits = int(stats.get("hits", 0))
            misses = int(stats.get("misses", 0))
            total = hits + misses
            info = self._client.info("memory")
            used_mb = info.get("used_memory", 0) / (1024 * 1024)
            max_mb = self._settings.redis_max_memory_mb
            return {
                "hits": hits,
                "misses": misses,
                "hit_ratio": round(hits / total, 4) if total > 0 else 0.0,
                "keys_count": self._client.zcard(f"{self._prefix}:freq"),
                "memory_used_mb": round(used_mb, 2),
                "memory_max_mb": max_mb,
                "memory_pct": round((used_mb / max_mb) * 100, 1) if max_mb else 0.0,
            }
        except Exception:
            logger.warning("Redis get_stats failed", exc_info=True)
            return {}

    # ── Health ──────────────────────────────────────────

    def ping(self) -> bool:
        """Check Redis connectivity.  Returns False on error."""
        try:
            return self._client.ping()
        except Exception:
            return False
