"""Retrieval pipeline orchestrator — end-to-end finding → compliance mappings.

Single-responsibility: composes the retrieval stages in the correct order.
Each stage (embed, search, map, critique, score) is delegated to its
dedicated module.  This module only owns sequencing and timing.

Flow:
  1. Embed finding  (GeminiEmbedder — RETRIEVAL_QUERY)
  2. Search Qdrant  (QdrantRetriever — parallel per-framework)
  3. Per-framework: Map + conditional Critique ─┐ (parallel)
     CVSS: Classify + Score (finding text only)  ┘
  4. Cumulate       Merge mappings + CVSS + tokens
  5. Return         QueryResponse
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config.settings import AppSettings, get_settings
from src.ingestion.embedder import GeminiEmbedder
from src.retrieval.cache import RedisCache
from src.retrieval.critic import AdversarialCritic
from src.retrieval.mapper import ComplianceMapper
from src.retrieval.models import ControlMapping, QueryRequest, QueryResponse, ScoredChunk, TokenUsage
from src.retrieval.normalizer import build_cache_key
from src.retrieval.qdrant_retriever import QdrantRetriever
from src.scoring.classifier import CVSSClassifier
from src.scoring.engine import compute_cvss
from src.scoring.cve_pipeline import enrich_cves
from src.scoring.models import CVSSResult, CveEnrichment

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

    if total_retrieved == 0:
        duration = time.time() - start
        logger.warning("No chunks retrieved — returning empty response")
        return QueryResponse(
            finding_text=request.finding_text,
            frameworks_searched=request.target_frameworks,
            chunks_retrieved=0,
            duration_seconds=round(duration, 2),
        )

    # ── 3. Per-framework: Map + conditional Critic (parallel) ──
    #    CVSS classification runs in parallel (only needs finding text)
    mapper = ComplianceMapper(settings)
    critic = AdversarialCritic(settings)
    cvss_classifier = CVSSClassifier(settings)
    threshold_val = settings.retrieval.critic_confidence_threshold

    def _process_framework(
        fw_key: str,
        chunks: list[ScoredChunk],
    ) -> tuple[list[ControlMapping], dict, dict, bool]:
        """Map a single framework's chunks + conditionally critique.

        Returns:
            (mappings, mapper_tokens, critic_tokens, critic_skipped)
        """
        _zero = {"prompt_tokens": 0, "total_tokens": 0}
        if not chunks:
            return [], _zero, _zero, True

        fw_mappings, m_tokens = mapper.map_finding(
            finding=request.finding_text,
            framework_chunks={fw_key: chunks},
            framework_key=fw_key,
        )

        c_tokens = {"prompt_tokens": 0, "total_tokens": 0}
        c_skipped = True

        if fw_mappings and any(
            m.confidence_score < threshold_val for m in fw_mappings
        ):
            c_skipped = False
            cited_sources = {m.citation_source for m in fw_mappings}
            relevant = [c for c in chunks if c.citation_source in cited_sources]
            evidence = {fw_key: relevant} if relevant else {fw_key: chunks}

            fw_mappings, c_tokens = critic.validate(
                finding=request.finding_text,
                mappings=fw_mappings,
                framework_chunks=evidence,
            )
            logger.info(
                "Critic ran for %s — %d mappings reviewed",
                fw_key, len(fw_mappings),
            )
        else:
            logger.info(
                "All %d mappings for %s >= %d confidence — skipping critic",
                len(fw_mappings), fw_key, threshold_val,
            )

        return fw_mappings, m_tokens, c_tokens, c_skipped

    # Fan-out: one thread per framework + one for CVSS
    all_mappings: list[ControlMapping] = []
    agg_mapper_prompt = 0
    agg_mapper_total = 0
    agg_critic_prompt = 0
    agg_critic_total = 0
    all_critic_skipped = True
    cvss_result: CVSSResult | None = None
    cvss_tokens: dict = {"prompt_tokens": 0, "total_tokens": 0}
    cve_enrichment: CveEnrichment | None = None
    cve_tokens: dict = {
        "agent_prompt_tokens": 0, "agent_total_tokens": 0,
        "evaluator_prompt_tokens": 0, "evaluator_total_tokens": 0,
    }

    def _classify_cvss() -> tuple[CVSSResult, dict]:
        """Run CVSS classification + scoring (parallel with mapper/critic)."""
        classification, tokens = cvss_classifier.classify(request.finding_text)
        result = compute_cvss(classification)
        return result, tokens

    def _enrich_cves() -> tuple[CveEnrichment, dict]:
        """Run CVE enrichment pipeline (parallel with mapper/critic + CVSS)."""
        return enrich_cves(request.finding_text, settings)

    max_workers = min(len(search_results) + 2, 6)  # +1 CVSS, +1 CVE
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit CVSS classification in parallel
        cvss_future = executor.submit(_classify_cvss)

        # Submit CVE enrichment in parallel
        cve_future = executor.submit(_enrich_cves)

        # Submit per-framework mapper/critic
        future_to_key = {
            executor.submit(_process_framework, fw_key, chunks): fw_key
            for fw_key, chunks in search_results.items()
        }
        for future in as_completed(future_to_key):
            fw_key = future_to_key[future]
            try:
                fw_maps, m_tok, c_tok, c_skip = future.result()
                all_mappings.extend(fw_maps)
                agg_mapper_prompt += m_tok["prompt_tokens"]
                agg_mapper_total += m_tok["total_tokens"]
                agg_critic_prompt += c_tok["prompt_tokens"]
                agg_critic_total += c_tok["total_tokens"]
                if not c_skip:
                    all_critic_skipped = False
            except Exception:
                logger.exception("Framework %s failed in mapper/critic", fw_key)

        # Collect CVSS result
        try:
            cvss_result, cvss_tokens = cvss_future.result()
        except Exception:
            logger.exception("CVSS classification failed — response will have cvss=null")

        # Collect CVE enrichment result
        try:
            cve_enrichment, cve_tokens = cve_future.result()
            # Feed best CVE ID into CVSSResult if found
            if cvss_result and cve_enrichment and cve_enrichment.cve_ids:
                cvss_result.cve = cve_enrichment.cve_ids[0]
        except Exception:
            logger.exception("CVE enrichment failed — response will have cve_enrichment=null")

    # ── 4. Token aggregation ─────────────────────────
    token_usage = TokenUsage(
        mapper_prompt_tokens=agg_mapper_prompt,
        mapper_total_tokens=agg_mapper_total,
        critic_prompt_tokens=agg_critic_prompt,
        critic_total_tokens=agg_critic_total,
        critic_skipped=all_critic_skipped,
        cvss_prompt_tokens=cvss_tokens["prompt_tokens"],
        cvss_total_tokens=cvss_tokens["total_tokens"],
        cve_agent_prompt_tokens=cve_tokens["agent_prompt_tokens"],
        cve_agent_total_tokens=cve_tokens["agent_total_tokens"],
        cve_evaluator_prompt_tokens=cve_tokens["evaluator_prompt_tokens"],
        cve_evaluator_total_tokens=cve_tokens["evaluator_total_tokens"],
        cve_google_search_prompt_tokens=cve_tokens.get("google_search_prompt_tokens", 0),
        cve_google_search_total_tokens=cve_tokens.get("google_search_total_tokens", 0),
        total_tokens=(
            agg_mapper_total + agg_critic_total
            + cvss_tokens["total_tokens"]
            + cve_tokens["agent_total_tokens"]
            + cve_tokens["evaluator_total_tokens"]
            + cve_tokens.get("google_search_total_tokens", 0)
        ),
    )

    duration = time.time() - start
    logger.info(
        "=== Retrieval complete: %d mappings across %d frameworks (%.1fs, %d tokens) ===",
        len(all_mappings), len(request.target_frameworks),
        duration, token_usage.total_tokens,
    )

    response = QueryResponse(
        finding_text=request.finding_text,
        cvss=cvss_result,
        cve_enrichment=cve_enrichment,
        mappings=all_mappings,
        frameworks_searched=request.target_frameworks,
        chunks_retrieved=total_retrieved,
        duration_seconds=round(duration, 2),
        token_usage=token_usage,
    )

    # ── 6. Cache write ──────────────────────────────────
    if cache and cache_key and lock_acquired:
        cache.set(cache_key, response)
        cache.release_lock(cache_key)
        logger.info("Cached response for key ...%s", cache_key[-12:])

    return response
