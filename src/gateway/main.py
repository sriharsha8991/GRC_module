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
        "Gateway starting — API=%s  Qdrant=%s",
        settings.api_url, settings.qdrant_url,
    )
    yield
    logger.info("Gateway shutting down")


app = FastAPI(
    title="GRC AI Gateway",
    version="0.1.0",
    description="API gateway — proxies requests to the GRC API and aggregates service health.",
    lifespan=lifespan,
)

app.include_router(router)
