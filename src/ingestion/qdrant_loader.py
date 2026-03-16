"""Qdrant loader — embed chunks via TEI and upsert to per-framework collections."""

import logging
import uuid
from typing import Any

import httpx
from qdrant_client import QdrantClient, models

from src.config.settings import IngestionSettings
from src.ingestion.chunker import Chunk

logger = logging.getLogger("ingestion.qdrant_loader")


class QdrantLoader:
    """Handles collection lifecycle and chunk ingestion into Qdrant."""

    def __init__(self, settings: IngestionSettings):
        self._settings = settings
        self._qdrant = QdrantClient(url=settings.qdrant_url, timeout=30)
        self._embedder_url = settings.embedder_url.rstrip("/")
        self._embed_batch_size = settings.embed_batch_size

    # ── Collection management ─────────────────────────────

    def create_collection(self, collection_name: str) -> None:
        """Create a Qdrant collection with the correct vector config + payload indexes."""
        if self._qdrant.collection_exists(collection_name):
            logger.info("Collection '%s' already exists — skipping creation", collection_name)
            return

        self._qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=self._settings.embedding_dimension,
                distance=models.Distance.COSINE,
            ),
        )

        # Payload indexes for optional filtering
        for field_name in ("domain", "control_id", "framework_category"):
            self._qdrant.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

        logger.info(
            "Created collection '%s' (dim=%d, distance=%s) with payload indexes",
            collection_name, self._settings.embedding_dimension, self._settings.qdrant_distance,
        )

    def drop_collection(self, collection_name: str) -> None:
        """Drop a collection if it exists."""
        if self._qdrant.collection_exists(collection_name):
            self._qdrant.delete_collection(collection_name)
            logger.info("Dropped collection '%s'", collection_name)

    def collection_info(self, collection_name: str) -> dict[str, Any] | None:
        """Get collection stats, or None if it doesn't exist."""
        if not self._qdrant.collection_exists(collection_name):
            return None
        info = self._qdrant.get_collection(collection_name)
        return {
            "name": collection_name,
            "vectors_count": info.vectors_count or 0,
            "points_count": info.points_count or 0,
            "status": str(info.status),
        }

    # ── Embedding ─────────────────────────────────────────

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Call the TEI embedder service to get embeddings for a batch of texts."""
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self._embedder_url}/embed",
                json={"inputs": texts},
            )
            resp.raise_for_status()
            return resp.json()

    def _embed_all(self, texts: list[str]) -> list[list[float]]:
        """Embed all texts in batches."""
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self._embed_batch_size):
            batch = texts[i : i + self._embed_batch_size]
            embeddings = self._embed_batch(batch)
            all_embeddings.extend(embeddings)
            logger.debug("Embedded batch %d-%d / %d", i, i + len(batch), len(texts))
        return all_embeddings

    # ── Upsert ────────────────────────────────────────────

    def ingest_chunks(self, collection_name: str, chunks: list[Chunk]) -> int:
        """Embed chunks and upsert them into the Qdrant collection.

        Args:
            collection_name: Target Qdrant collection.
            chunks: List of Chunk objects from the chunker.

        Returns:
            Number of points upserted.
        """
        if not chunks:
            logger.warning("No chunks to ingest into '%s'", collection_name)
            return 0

        texts = [c.text for c in chunks]
        logger.info("Embedding %d chunks for collection '%s'...", len(texts), collection_name)
        embeddings = self._embed_all(texts)

        # Build Qdrant points
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))
            points.append(models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "text": chunk.text,
                    "chunk_id": chunk.chunk_id,
                    **chunk.metadata,
                },
            ))

        # Upsert in batches of 100
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            self._qdrant.upsert(
                collection_name=collection_name,
                points=batch,
            )
            logger.debug("Upserted batch %d-%d / %d", i, i + len(batch), len(points))

        logger.info(
            "Ingested %d points into '%s'",
            len(points), collection_name,
        )
        return len(points)

    # ── Re-ingestion (drop + recreate + ingest) ───────────

    def reingest(self, collection_name: str, chunks: list[Chunk]) -> int:
        """Drop existing collection, recreate, and ingest fresh chunks."""
        self.drop_collection(collection_name)
        self.create_collection(collection_name)
        return self.ingest_chunks(collection_name, chunks)
