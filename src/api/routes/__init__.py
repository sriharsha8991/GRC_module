"""API route aggregation."""

from fastapi import APIRouter

from src.api.routes.ingestion import router as ingestion_router

router = APIRouter(prefix="/api/v1")
router.include_router(ingestion_router)
