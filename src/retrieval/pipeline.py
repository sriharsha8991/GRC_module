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
import time

from src.config.settings import IngestionSettings, get_ingestion_settings
from src.ingestion.embedder import GeminiEmbedder
from src.retrieval.critic import AdversarialCritic
from src.retrieval.mapper import ComplianceMapper
from src.retrieval.models import QueryRequest, QueryResponse, RankedChunk
from src.retrieval.qdrant_retriever import QdrantRetriever
from src.retrieval.reranker import Reranker

logger = logging.getLogger("retrieval.pipeline")


def query_finding(
    request: QueryRequest,
    settings: IngestionSettings | None = None,
) -> QueryResponse:
    """Run the full retrieval pipeline for a security finding.

    Args:
        request: The finding text and target frameworks.
        settings: Override settings (uses defaults if None).

    Returns:
        QueryResponse with control mappings, stats, and timing.
    """
    settings = settings or get_ingestion_settings()
    start = time.time()

    threshold = settings.rerank_threshold

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

    if settings.use_reranker:
        reranker = Reranker(settings)
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
                total_after_rerank, "on" if settings.use_reranker else "off")

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
    mappings = mapper.map_finding(
        finding=request.finding_text,
        framework_chunks=reranked_chunks,
    )

    # ── 5. Adversarial critique ─────────────────────────
    critic = AdversarialCritic(settings)
    mappings = critic.validate(
        finding=request.finding_text,
        mappings=mappings,
        framework_chunks=reranked_chunks,
    )

    duration = time.time() - start
    logger.info(
        "=== Retrieval complete: %d mappings (%.1fs) ===",
        len(mappings), duration,
    )

    return QueryResponse(
        finding_text=request.finding_text,
        mappings=mappings,
        frameworks_searched=request.target_frameworks,
        chunks_retrieved=total_retrieved,
        chunks_after_rerank=total_after_rerank,
        duration_seconds=round(duration, 2),
    )
