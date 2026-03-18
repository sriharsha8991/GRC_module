"""Qdrant loader — embed chunks via Gemini and upsert to single grc_controls collection."""

import logging
from typing import Any

from qdrant_client import QdrantClient, models

from src.config.settings import AppSettings
from src.ingestion.chunker import Chunk
from src.ingestion.embedder import GeminiEmbedder

logger = logging.getLogger("ingestion.qdrant_loader")


class QdrantLoader:
    """Handles collection lifecycle and chunk ingestion into Qdrant."""

    def __init__(self, settings: AppSettings):
        self._settings = settings
        self._qdrant = QdrantClient(url=settings.qdrant.url, timeout=30)
        self._embedder = GeminiEmbedder(settings)

    # ── Collection management ─────────────────────────────

    def ensure_collection(self) -> None:
        """Create the grc_controls collection if it doesn't exist."""
        name = self._settings.qdrant.collection_name
        if self._qdrant.collection_exists(name):
            logger.info("Collection '%s' already exists", name)
            return

        self._qdrant.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self._settings.embedding.dimension,
                distance=models.Distance.COSINE,
            ),
        )

        # Tenant index on framework — MUST be before any inserts
        self._qdrant.create_payload_index(
            collection_name=name,
            field_name="framework",
            field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD,
                is_tenant=True,
            ),
        )

        logger.info(
            "Created collection '%s' (dim=%d, tenant index on 'framework')",
            name, self._settings.embedding.dimension,
        )

    def collection_info(self) -> dict[str, Any] | None:
        """Get collection stats."""
        name = self._settings.qdrant.collection_name
        if not self._qdrant.collection_exists(name):
            return None
        info = self._qdrant.get_collection(name)
        return {
            "name": name,
            "vectors_count": info.vectors_count or 0,
            "points_count": info.points_count or 0,
            "status": str(info.status),
        }

    # ── Upsert ────────────────────────────────────────────

    def ingest_chunks(self, chunks: list[Chunk]) -> int:
        """Embed chunks via Gemini and upsert to the grc_controls collection.

        Returns:
            Number of points upserted.
        """
        if not chunks:
            logger.warning("No chunks to ingest")
            return 0

        name = self._settings.qdrant.collection_name
        self.ensure_collection()

        texts = [c.text for c in chunks]
        logger.info("Embedding %d chunks via Gemini...", len(texts))
        embeddings = self._embedder.embed_documents(texts)

        # Build points with deterministic IDs
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            points.append(models.PointStruct(
                id=chunk.point_id,
                vector=embedding,
                payload={
                    "text": chunk.text,
                    **chunk.metadata,
                },
            ))

        # Upsert in batches of 100
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            self._qdrant.upsert(collection_name=name, points=batch)
            logger.debug("Upserted batch %d-%d / %d", i, i + len(batch), len(points))

        logger.info("Ingested %d points into '%s'", len(points), name)
        return len(points)
