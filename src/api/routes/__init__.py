"""API route aggregation."""

from fastapi import APIRouter

from src.api.routes.cache import router as cache_router
from src.api.routes.ingestion import router as ingestion_router
from src.api.routes.query import router as query_router

router = APIRouter(prefix="/api/v1")
router.include_router(ingestion_router)
router.include_router(query_router)
router.include_router(cache_router)
