"""
GRC Gateway Client — SDK for checking service health.

Usage:
    from src.gateway.gateway_client import GatewayClient

    async with GatewayClient() as gw:
        health = await gw.health()
        print(health)
"""

import httpx
from typing import Any, Optional


class GatewayClient:
    """Async client for the GRC API Gateway."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Use 'async with GatewayClient() as gw:'")
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()
            
