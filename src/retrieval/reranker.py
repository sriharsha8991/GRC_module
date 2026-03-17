"""Cross-encoder reranker client — calls TEI reranker via HTTP.

Single-responsibility: takes scored chunks + query, returns ranked chunks
filtered by a confidence threshold.  Does NOT search or embed.
"""

import logging

import httpx

from src.config.settings import IngestionSettings
from src.retrieval.models import RankedChunk, ScoredChunk

logger = logging.getLogger("retrieval.reranker")


class Reranker:
    """Reranks chunks using the TEI-hosted bge-reranker-v2-m3 model."""

    def __init__(self, settings: IngestionSettings):
        self._url = f"{settings.reranker_url}/rerank"
        self._default_threshold = settings.rerank_threshold

    def rerank(
        self,
        query: str,
        chunks: list[ScoredChunk],
        threshold: float | None = None,
    ) -> list[RankedChunk]:
        """Rerank chunks against the query and filter by threshold.

        Args:
            query: The finding / query text.
            chunks: Candidate chunks from Qdrant search.
            threshold: Minimum rerank score to keep (default from settings).

        Returns:
            Chunks that pass the threshold, sorted by rerank_score descending.
        """
        if not chunks:
            return []

        threshold = threshold if threshold is not None else self._default_threshold
        texts = [c.text for c in chunks]

        response = httpx.post(
            self._url,
            json={"query": query, "texts": texts},
            timeout=90,
        )
        response.raise_for_status()
        scores = response.json()

        ranked: list[RankedChunk] = []
        for item in scores:
            idx = item["index"]
            score = item["score"]
            if score >= threshold:
                chunk = chunks[idx]
                ranked.append(RankedChunk(
                    text=chunk.text,
                    metadata=chunk.metadata,
                    qdrant_score=chunk.qdrant_score,
                    rerank_score=score,
                ))

        ranked.sort(key=lambda r: r.rerank_score, reverse=True)

        all_scores = sorted(
            [item["score"] for item in scores], reverse=True
        )
        logger.info(
            "Reranked %d → %d chunks (threshold=%.2f) | "
            "score distribution: min=%.4f, max=%.4f, median=%.4f | "
            "top_5=%s",
            len(chunks),
            len(ranked),
            threshold,
            min(all_scores) if all_scores else 0.0,
            max(all_scores) if all_scores else 0.0,
            all_scores[len(all_scores) // 2] if all_scores else 0.0,
            [round(s, 4) for s in all_scores[:5]],
        )
        return ranked
