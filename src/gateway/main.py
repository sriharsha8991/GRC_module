"""GRC Gateway — lightweight health-check aggregator for AI services."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .routes import router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Gateway starting — Qdrant=%s  Embedder=%s  Reranker=%s",
        settings.qdrant_url, settings.embedder_url, settings.reranker_url,
    )
    yield
    logger.info("Gateway shutting down")


app = FastAPI(
    title="GRC AI Gateway",
    version="0.1.0",
    description="Health-check aggregator for Qdrant, Embedding, and Reranker services.",
    lifespan=lifespan,
)

app.include_router(router)
