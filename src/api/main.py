"""FastAPI application — GRC ingestion and query API."""

import logging
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from qdrant_client import QdrantClient

from src.api.routes import router
from src.config.genai_client import get_client
from src.config.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("api.startup")


def _check_gemini(settings) -> None:
    """Verify Gemini API key is valid by listing models."""
    client = get_client(settings)
    # A lightweight call that validates the API key
    next(iter(client.models.list()))
    logger.info("Gemini connection OK  (model: %s)", settings.gemini.parse_model)


def _check_qdrant(settings) -> None:
    """Verify Qdrant is reachable."""
    qc = QdrantClient(url=settings.qdrant.url, timeout=10)
    qc.get_collections()
    logger.info("Qdrant  connection OK  (url: %s)", settings.qdrant.url)


def _check_redis(settings) -> None:
    """Verify Redis is reachable."""
    r = redis.Redis.from_url(
        settings.redis.url,
        socket_timeout=settings.redis.socket_timeout,
        socket_connect_timeout=settings.redis.socket_timeout,
    )
    r.ping()
    r.close()
    logger.info("Redis   connection OK  (url: %s)", settings.redis.url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup connection checks, then yield to serve requests."""
    settings = get_settings()
    checks = [
        ("Gemini", _check_gemini),
        ("Qdrant", _check_qdrant),
        ("Redis", _check_redis),
    ]
    for name, check_fn in checks:
        try:
            check_fn(settings)
        except Exception as exc:
            logger.error("%s connection FAILED: %s", name, exc)
            raise SystemExit(f"{name} connection failed — cannot start server") from exc

    yield  # application serves requests


app = FastAPI(
    title="GRC APIs",
    description="Upload the GRC framework PDF to the ingestion endpoint, then query security findings against it.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root():
    """Root endpoint providing basic information about the API."""
    return {
        "service": "GRC APIs",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy"}


app.include_router(router)
