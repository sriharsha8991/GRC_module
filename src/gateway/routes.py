"""API Gateway routes — health checks only."""

from fastapi import APIRouter

from .clients import check_qdrant, check_embedder, check_reranker
from .config import get_settings
from .schemas import HealthResponse, ServiceHealth

settings = get_settings()
router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Aggregate health of all downstream services."""
    qdrant_h = await check_qdrant(settings)
    embedder_h = await check_embedder(settings)
    reranker_h = await check_reranker(settings)

    return HealthResponse(
        gateway="healthy",
        qdrant=ServiceHealth(**qdrant_h),
        embedder=ServiceHealth(**embedder_h),
        reranker=ServiceHealth(**reranker_h),
    )
