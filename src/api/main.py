"""FastAPI application — GRC ingestion and query API."""

import logging

from fastapi import FastAPI

from src.api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s | %(levelname)s | %(message)s",
)

app = FastAPI(
    title="GRC APIs",
    description="Upload the GRC framework PDF to the ingestion endpoint, then query security findings against it.",
    version="0.1.0",
)

app.include_router(router)
