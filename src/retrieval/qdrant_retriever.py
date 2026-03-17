"""Qdrant vector search — tenant-filtered retrieval with parallel multi-framework support.

Single-responsibility: searches the grc_controls collection, returns ScoredChunks.
Does NOT embed queries or rerank — those are separate concerns.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from qdrant_client import QdrantClient, models

from src.config.settings import IngestionSettings
from src.retrieval.models import ScoredChunk

logger = logging.getLogger("retrieval.qdrant_retriever")


class QdrantRetriever:
    """Searches the grc_controls collection with per-framework tenant filtering."""

    def __init__(self, settings: IngestionSettings):
        self._settings = settings
        self._client = QdrantClient(url=settings.qdrant_url, timeout=30)
        self._collection = settings.collection_name

    def search(
        self,
        query_embedding: list[float],
        framework_key: str,
        limit: int | None = None,
    ) -> list[ScoredChunk]:
        """Search for chunks matching a single framework.

        Args:
            query_embedding: The query vector (1536-dim Gemini RETRIEVAL_QUERY).
            framework_key: Tenant filter value (e.g. 'iso_27001').
            limit: Max results (defaults to settings.retrieval_limit).

        Returns:
            Scored chunks sorted by cosine similarity descending.
        """
        limit = limit or self._settings.retrieval_limit

        results = self._client.search(
            collection_name=self._collection,
            query_vector=query_embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="framework",
                        match=models.MatchValue(value=framework_key),
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
        )

        chunks = []
        for hit in results:
            payload = hit.payload or {}
            text = payload.pop("text", "")
            chunks.append(ScoredChunk(
                text=text,
                metadata=payload,
                qdrant_score=hit.score,
            ))

        logger.info(
            "Qdrant search: framework=%s, hits=%d (limit=%d)",
            framework_key, len(chunks), limit,
        )
        return chunks

    def search_multi(
        self,
        query_embedding: list[float],
        framework_keys: list[str],
        limit: int | None = None,
    ) -> dict[str, list[ScoredChunk]]:
        """Search across multiple frameworks in parallel.

        Launches one search per framework via ThreadPoolExecutor (I/O-bound).

        Returns:
            Dict mapping framework_key → list of ScoredChunks.
        """
        if len(framework_keys) == 1:
            return {
                framework_keys[0]: self.search(
                    query_embedding, framework_keys[0], limit
                )
            }

        results: dict[str, list[ScoredChunk]] = {}

        with ThreadPoolExecutor(max_workers=len(framework_keys)) as executor:
            future_to_key = {
                executor.submit(
                    self.search, query_embedding, key, limit
                ): key
                for key in framework_keys
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                results[key] = future.result()

        logger.info(
            "Multi-framework search: %d frameworks, total %d chunks",
            len(framework_keys),
            sum(len(v) for v in results.values()),
        )
        return results
