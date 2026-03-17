"""Reranker backends — TEI (self-hosted) and Jina (cloud API).

Single-responsibility: takes scored chunks + query, returns ranked chunks
filtered by a confidence threshold.  Does NOT search or embed.

Strategy Pattern: both backends implement the same RerankerBackend protocol.
The pipeline calls get_reranker(settings) to obtain the active backend.
"""

import logging
from typing import Protocol

import httpx

from src.config.settings import IngestionSettings
from src.retrieval.models import RankedChunk, ScoredChunk

logger = logging.getLogger("retrieval.reranker")


# ── Shared helpers ───────────────────────────────────────


def _build_ranked_chunks(
    chunks: list[ScoredChunk],
    scored_items: list[tuple[int, float]],
    threshold: float,
) -> list[RankedChunk]:
    """Filter by threshold and build RankedChunk list, sorted descending."""
    ranked: list[RankedChunk] = []
    for idx, score in scored_items:
        if score >= threshold:
            chunk = chunks[idx]
            ranked.append(RankedChunk(
                text=chunk.text,
                metadata=chunk.metadata,
                qdrant_score=chunk.qdrant_score,
                rerank_score=score,
            ))
    ranked.sort(key=lambda r: r.rerank_score, reverse=True)
    return ranked


def _log_score_distribution(
    all_scores: list[float],
    n_input: int,
    n_output: int,
    threshold: float,
    backend: str,
) -> None:
    """Log score stats for observability."""
    sorted_scores = sorted(all_scores, reverse=True)
    logger.info(
        "[%s] Reranked %d → %d chunks (threshold=%.2f) | "
        "score distribution: min=%.4f, max=%.4f, median=%.4f | "
        "top_5=%s",
        backend,
        n_input,
        n_output,
        threshold,
        min(sorted_scores) if sorted_scores else 0.0,
        max(sorted_scores) if sorted_scores else 0.0,
        sorted_scores[len(sorted_scores) // 2] if sorted_scores else 0.0,
        [round(s, 4) for s in sorted_scores[:5]],
    )


# ── Protocol ─────────────────────────────────────────────


class RerankerBackend(Protocol):
    def rerank(
        self,
        query: str,
        chunks: list[ScoredChunk],
        threshold: float | None = None,
    ) -> list[RankedChunk]: ...


# ── TEI backend (self-hosted cross-encoder) ──────────────


class TEIReranker:
    """Reranks via TEI-hosted cross-encoder/ms-marco-MiniLM-L-12-v2."""

    def __init__(self, settings: IngestionSettings):
        self._url = f"{settings.reranker_url}/rerank"
        self._default_threshold = settings.rerank_threshold

    def rerank(
        self,
        query: str,
        chunks: list[ScoredChunk],
        threshold: float | None = None,
    ) -> list[RankedChunk]:
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

        scored_items = [(item["index"], item["score"]) for item in scores]
        all_scores = [s for _, s in scored_items]

        ranked = _build_ranked_chunks(chunks, scored_items, threshold)
        _log_score_distribution(all_scores, len(chunks), len(ranked), threshold, "TEI")
        return ranked


# ── Jina backend (cloud API) ────────────────────────────


class JinaReranker:
    """Reranks via Jina Reranker cloud API (https://api.jina.ai/v1/rerank)."""

    _BASE_URL = "https://api.jina.ai/v1/rerank"

    def __init__(self, settings: IngestionSettings):
        if not settings.jina_api_key:
            raise ValueError(
                "INGESTION_JINA_API_KEY must be set when reranker_backend='jina'"
            )
        self._api_key = settings.jina_api_key
        self._model = settings.jina_reranker_model
        self._default_threshold = settings.rerank_threshold

    def rerank(
        self,
        query: str,
        chunks: list[ScoredChunk],
        threshold: float | None = None,
    ) -> list[RankedChunk]:
        if not chunks:
            return []

        threshold = threshold if threshold is not None else self._default_threshold
        documents = [c.text for c in chunks]

        response = httpx.post(
            self._BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "query": query,
                "documents": documents,
                "return_documents": False,
            },
            timeout=90,
        )
        response.raise_for_status()
        results = response.json()["results"]

        scored_items = [(r["index"], r["relevance_score"]) for r in results]
        all_scores = [s for _, s in scored_items]

        ranked = _build_ranked_chunks(chunks, scored_items, threshold)
        _log_score_distribution(
            all_scores, len(chunks), len(ranked), threshold,
            f"Jina/{self._model}",
        )
        return ranked


# ── Factory ──────────────────────────────────────────────


def get_reranker(settings: IngestionSettings) -> RerankerBackend:
    """Return the configured reranker backend."""
    backend = settings.reranker_backend.lower()
    if backend == "tei":
        return TEIReranker(settings)
    if backend == "jina":
        return JinaReranker(settings)
    raise ValueError(
        f"Unknown reranker_backend={backend!r}. Expected 'tei' or 'jina'."
    )
