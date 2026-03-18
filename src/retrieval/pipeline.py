"""Retrieval pipeline orchestrator — end-to-end finding → compliance mappings.

Single-responsibility: composes the retrieval stages in the correct order.
Each stage (embed, search, rerank, map, critique) is delegated to its
dedicated module.  This module only owns sequencing and timing.

Flow:
  1. Embed finding  (GeminiEmbedder — RETRIEVAL_QUERY)
  2. Search Qdrant  (QdrantRetriever — parallel per-framework)
  3. Rerank         (Reranker — independent per-framework, threshold filter)
  4. Map            (ComplianceMapper — single Gemini call, all frameworks)
  5. Critique       (AdversarialCritic — validates citations + logic)
  6. Return         QueryResponse
"""

import logging
import threading
import time

from src.config.settings import AppSettings, get_settings
from src.ingestion.embedder import GeminiEmbedder
from src.retrieval.cache import RedisCache
from src.retrieval.critic import AdversarialCritic
from src.retrieval.mapper import ComplianceMapper
from src.retrieval.models import QueryRequest, QueryResponse, RankedChunk, TokenUsage
from src.retrieval.normalizer import build_cache_key
from src.retrieval.qdrant_retriever import QdrantRetriever
from src.retrieval.reranker import get_reranker

logger = logging.getLogger("retrieval.pipeline")

# ── Lazy Redis singleton ────────────────────────────────
_cache_instance: RedisCache | None = None
_cache_lock = threading.Lock()


def _get_cache(settings: AppSettings) -> RedisCache | None:
    """Return a shared RedisCache, or None if disabled / unreachable."""
    if not settings.redis.enabled:
        return None
    global _cache_instance
    if _cache_instance is not None:
        return _cache_instance
    with _cache_lock:
        if _cache_instance is not None:
            return _cache_instance
        candidate = RedisCache(settings)
        if not candidate.ping():
            logger.warning("Redis unreachable — caching disabled")
            return None
        _cache_instance = candidate
    return _cache_instance


def query_finding(
    request: QueryRequest,
    settings: AppSettings | None = None,
) -> QueryResponse:
    """Run the full retrieval pipeline for a security finding.

    Args:
        request: The finding text and target frameworks.
        settings: Override settings (uses defaults if None).

    Returns:
        QueryResponse with control mappings, stats, and timing.
    """
    settings = settings or get_settings()
    start = time.time()

    # ── 0. Cache lookup ─────────────────────────────────
    cache = _get_cache(settings)
    cache_key: str | None = None
    lock_acquired = False

    if cache:
        cache_key = build_cache_key(
            request.finding_text, request.target_frameworks, settings,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            cached.duration_seconds = round(time.time() - start, 4)
            cached.token_usage = TokenUsage()
            logger.info("Cache HIT for key ...%s", cache_key[-12:])
            return cached

        lock_acquired = cache.acquire_lock(cache_key)
        logger.info(
            "Cache MISS — lock %s",
            "acquired" if lock_acquired else "busy (another request caching)",
        )

    threshold = settings.reranker.threshold

    # ── 1. Embed the finding ────────────────────────────
    embedder = GeminiEmbedder(settings)
    query_embedding = embedder.embed_query(request.finding_text)
    logger.info("Embedded finding (%d dims)", len(query_embedding))

    # ── 2. Search Qdrant per-framework in parallel ──────
    retriever = QdrantRetriever(settings)
    search_results = retriever.search_multi(
        query_embedding=query_embedding,
        framework_keys=request.target_frameworks,
    )
    total_retrieved = sum(len(v) for v in search_results.values())
    logger.info("Retrieved %d chunks across %d frameworks",
                total_retrieved, len(request.target_frameworks))

    # ── 3. Rerank (or pass-through) per-framework ───────
    reranked_chunks: dict[str, list[RankedChunk]] = {}

    if settings.retrieval.use_reranker:
        reranker = get_reranker(settings)
        for fw_key, scored in search_results.items():
            ranked = reranker.rerank(
                query=request.finding_text,
                chunks=scored,
                threshold=threshold,
            )
            if ranked:
                reranked_chunks[fw_key] = ranked
    else:
        logger.info("Reranker disabled — passing Qdrant results through")
        for fw_key, scored in search_results.items():
            reranked_chunks[fw_key] = [
                RankedChunk(
                    text=c.text,
                    metadata=c.metadata,
                    qdrant_score=c.qdrant_score,
                    rerank_score=c.qdrant_score,
                )
                for c in scored
            ]

    total_after_rerank = sum(len(v) for v in reranked_chunks.values())
    logger.info("After rerank: %d chunks (reranker=%s)",
                total_after_rerank, "on" if settings.retrieval.use_reranker else "off")

    if total_after_rerank == 0:
        duration = time.time() - start
        logger.warning("No chunks survived reranking — returning empty response")
        return QueryResponse(
            finding_text=request.finding_text,
            frameworks_searched=request.target_frameworks,
            chunks_retrieved=total_retrieved,
            chunks_after_rerank=0,
            duration_seconds=round(duration, 2),
        )

    # ── 4. Map finding to controls (single Gemini call) ─
    mapper = ComplianceMapper(settings)
    mappings, mapper_tokens = mapper.map_finding(
        finding=request.finding_text,
        framework_chunks=reranked_chunks,
    )

    # ── 5. Conditional adversarial critique ─────────
    critic_tokens = {"prompt_tokens": 0, "total_tokens": 0}
    critic_skipped = False
    threshold_val = settings.retrieval.critic_confidence_threshold

    if mappings and all(
        m.confidence_score >= threshold_val for m in mappings
    ):
        # All mappings meet confidence threshold — skip critic
        critic_skipped = True
        logger.info(
            "All %d mappings >= %d confidence — skipping critic",
            len(mappings), threshold_val,
        )
    elif mappings:
        # Filter evidence to only chunks cited by the mapper
        cited_sources = {m.citation_source for m in mappings}
        filtered_chunks: dict[str, list[RankedChunk]] = {}
        for fw_key, chunks in reranked_chunks.items():
            relevant = [c for c in chunks if c.citation_source in cited_sources]
            if relevant:
                filtered_chunks[fw_key] = relevant

        critic = AdversarialCritic(settings)
        mappings, critic_tokens = critic.validate(
            finding=request.finding_text,
            mappings=mappings,
            framework_chunks=filtered_chunks if filtered_chunks else reranked_chunks,
        )

    # ── Token aggregation ────────────────────────────
    token_usage = TokenUsage(
        mapper_prompt_tokens=mapper_tokens["prompt_tokens"],
        mapper_total_tokens=mapper_tokens["total_tokens"],
        critic_prompt_tokens=critic_tokens["prompt_tokens"],
        critic_total_tokens=critic_tokens["total_tokens"],
        critic_skipped=critic_skipped,
        total_tokens=mapper_tokens["total_tokens"] + critic_tokens["total_tokens"],
    )

    duration = time.time() - start
    logger.info(
        "=== Retrieval complete: %d mappings (%.1fs, %d tokens) ===",
        len(mappings), duration, token_usage.total_tokens,
    )

    response = QueryResponse(
        finding_text=request.finding_text,
        mappings=mappings,
        frameworks_searched=request.target_frameworks,
        chunks_retrieved=total_retrieved,
        chunks_after_rerank=total_after_rerank,
        duration_seconds=round(duration, 2),
        token_usage=token_usage,
    )

    # ── 6. Cache write ──────────────────────────────────
    if cache and cache_key and lock_acquired:
        cache.set(cache_key, response)
        cache.release_lock(cache_key)
        logger.info("Cached response for key ...%s", cache_key[-12:])

    return response
