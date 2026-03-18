"""API Gateway routes — health aggregation + reverse proxy to GRC API."""

import logging

import httpx
from fastapi import APIRouter, Request, Response

from .clients import check_api, check_qdrant, check_redis
from .config import get_settings
from .schemas import HealthResponse, ServiceHealth

logger = logging.getLogger("gateway.routes")
settings = get_settings()
router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Aggregate health of all downstream services."""
    api_h = await check_api(settings)
    qdrant_h = await check_qdrant(settings)
    redis_h = await check_redis(settings)

    return HealthResponse(
        gateway="healthy",
        api=ServiceHealth(**api_h),
        qdrant=ServiceHealth(**qdrant_h),
        redis=ServiceHealth(**redis_h),
    )


# ── Reverse proxy: forward /api/v1/* to the backend API service ──────────


@router.api_route(
    "/api/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    tags=["Proxy"],
)
async def proxy_api(path: str, request: Request):
    """Forward requests to the GRC API backend."""
    target = f"{settings.api_url}/api/v1/{path}"
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "transfer-encoding")
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        proxy_resp = await client.request(
            method=request.method,
            url=target,
            headers=headers,
            content=body,
            params=request.query_params,
        )

    return Response(
        content=proxy_resp.content,
        status_code=proxy_resp.status_code,
        headers=dict(proxy_resp.headers),
    )
