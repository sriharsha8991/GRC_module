"""Shared infrastructure for CVE data source clients.

Provides: Protocol, rate limiter, HTTP retry logic, and common parsers
used across NVD, cve.org, and OSV.dev clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

import httpx

from src.scoring.models import CveDetail, CveSearchResult

logger = logging.getLogger("scoring.cve_client")

# ── Retry config ────────────────────────────────────────
MAX_RETRIES = 3
_BASE_DELAY = 2.0
_MAX_DELAY = 30.0
_BACKOFF_FACTOR = 2.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# ── Protocol for dependency inversion ───────────────────


class CveDataSource(Protocol):
    """Protocol that all CVE data source clients must satisfy."""

    async def search(
        self, product: str, version: str, **kwargs: str,
    ) -> list[CveSearchResult]: ...

    async def fetch_detail(self, cve_id: str) -> CveDetail | None: ...


# ── Rate limiter ────────────────────────────────────────


class RateLimiter:
    """Token-bucket rate limiter for API requests."""

    def __init__(self, max_tokens: int, window_seconds: float) -> None:
        self._max_tokens = max_tokens
        self._window = window_seconds
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._max_tokens,
                self._tokens + elapsed * (self._max_tokens / self._window),
            )
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) * (self._window / self._max_tokens)
                logger.debug("Rate limiter: waiting %.1fs", wait)
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# ── HTTP helpers ────────────────────────────────────────


def create_client(timeout: float = 10.0) -> httpx.AsyncClient:
    """Create an httpx async client with sensible defaults."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        headers={"User-Agent": "GRC-Module/1.0"},
        follow_redirects=True,
    )


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    limiter: RateLimiter | None = None,
    **kwargs: object,
) -> httpx.Response | None:
    """Execute an HTTP request with exponential backoff retry."""
    delay = _BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        if limiter:
            await limiter.acquire()
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS:
                logger.warning(
                    "Retryable %d from %s (attempt %d/%d)",
                    resp.status_code, url, attempt, MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)
                    continue
                return None
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError:
            logger.exception("HTTP error from %s", url)
            return None
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
            logger.warning(
                "Network error for %s (attempt %d/%d)", url, attempt, MAX_RETRIES,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)
            else:
                return None
    return None


# ── Shared parsing helpers ──────────────────────────────


def extract_english_description(descriptions: list[dict], max_len: int = 500) -> str:
    """Extract the first English description from a list of lang-tagged dicts."""
    for d in descriptions:
        lang = d.get("lang", "")
        if lang == "en" or lang.startswith("en"):
            return d.get("value", "")[:max_len]
    return ""


def extract_references(refs: list[dict], max_refs: int = 5) -> list[str]:
    """Extract up to *max_refs* URLs from a references list."""
    return [r["url"] for r in refs[:max_refs] if "url" in r]
