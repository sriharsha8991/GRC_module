"""Health-check clients for downstream services."""

import time
import httpx
import logging

from .config import Settings

logger = logging.getLogger("gateway.clients")


async def _check_health(url: str, timeout: float = 5.0) -> dict:
    """Ping a service health endpoint and return status + latency."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return {
                "status": "healthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            }
    except Exception as e:
        logger.warning("Health check failed for %s: %s", url, e)
        return {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": str(e),
        }


async def check_qdrant(settings: Settings) -> dict:
    return await _check_health(f"{settings.qdrant_url}/healthz")


async def check_embedder(settings: Settings) -> dict:
    return await _check_health(f"{settings.embedder_url}/health")


async def check_reranker(settings: Settings) -> dict:
    return await _check_health(f"{settings.reranker_url}/health")

    def delete_collection(self, name: str) -> dict:
        self._client.delete_collection(collection_name=name)
        return {"name": name, "status": "deleted"}

    # ── Points ───────────────────────────────────────────

    def upsert(self, collection: str, points: list[dict]) -> dict:
        qdrant_points = [
            models.PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p.get("payload", {}),
            )
            for p in points
        ]
        self._client.upsert(
            collection_name=collection,
            points=qdrant_points,
        )
        return {
            "collection": collection,
            "upserted_count": len(qdrant_points),
            "status": "ok",
        }

    def search(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 10,
        score_threshold: Optional[float] = None,
        filters: Optional[dict] = None,
    ) -> dict:
        qdrant_filter = None
        if filters:
            must_conditions = []
            for key, value in filters.items():
                if isinstance(value, list):
                    must_conditions.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchAny(any=value),
                        )
                    )
                else:
                    must_conditions.append(
                        models.FieldCondition(
                            key=key,
                            match=models.MatchValue(value=value),
                        )
                    )
            qdrant_filter = models.Filter(must=must_conditions)

        results = self._client.search(
            collection_name=collection,
            query_vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=qdrant_filter,
        )

        return {
            "collection": collection,
            "results": [
                {
                    "id": hit.id,
                    "score": round(hit.score, 6),
                    "payload": hit.payload or {},
                }
                for hit in results
            ],
        }

    def health(self) -> dict:
        start = time.perf_counter()
        try:
            self._client.get_collections()
            return {
                "status": "healthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}
