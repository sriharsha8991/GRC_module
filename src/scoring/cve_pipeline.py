"""CVE pipeline — orchestrator composing agent → details → evaluation.

Single-responsibility: sequences the CVE enrichment stages in the correct
order.  Each stage is delegated to its dedicated module.

Flow:
  1. Agent: Gemini function-calling agent classifies finding AND searches
     CVE databases (NVD, OSV, VulDB) via tool use
  2. If PURE_MISCONFIGURATION → return immediately (cve_ids=null)
  3. If 0 results → Google Search grounding fallback
  4. Fetch full CVE details from cve.org/NVD (cap at 20)
  5. LLM evaluation judge with full CVE context (batches of 5, parallel)
  6. Return approved CVEs with details
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.config.settings import AppSettings
from src.scoring.cve_agent import CveAgent
from src.scoring.cve_evaluator import CveEvaluator
from src.scoring.google_search_fallback import search_cves_via_google
from src.scoring.models import CveDetail, CveEnrichment, CveSearchResult

logger = logging.getLogger("scoring.cve_pipeline")

# Hard cap on candidates before detail fetch — prevents runaway API calls
_MAX_CANDIDATES = 20


def enrich_cves(
    finding_text: str,
    settings: AppSettings,
) -> tuple[CveEnrichment, dict]:
    """Run the full CVE enrichment pipeline for a security finding.

    This is a synchronous entry point that internally runs async code
    via asyncio, matching the ThreadPoolExecutor pattern used by the
    retrieval pipeline.

    Args:
        finding_text: The security finding text.
        settings: Application settings.

    Returns:
        (CveEnrichment, token_usage_dict)
    """
    if not settings.cve.enabled:
        logger.info("CVE enrichment disabled — skipping")
        return CveEnrichment(
            finding_type="SKIPPED",
            classification_reasoning="CVE enrichment disabled in settings",
        ), {"agent_prompt_tokens": 0, "agent_total_tokens": 0,
            "evaluator_prompt_tokens": 0, "evaluator_total_tokens": 0}

    start = time.time()

    enrichment, tokens = asyncio.run(
        _async_agent_and_evaluate(finding_text, settings),
    )
    enrichment.enrichment_duration_seconds = round(time.time() - start, 2)

    logger.info(
        "CVE enrichment complete: %s CVEs in %.1fs",
        len(enrichment.cve_ids) if enrichment.cve_ids else 0,
        enrichment.enrichment_duration_seconds,
    )

    return enrichment, tokens


async def _async_agent_and_evaluate(
    finding_text: str,
    settings: AppSettings,
) -> tuple[CveEnrichment, dict]:
    """Async portion: agent search → evaluate → fetch details."""

    agent = CveAgent(settings)

    # ── Step 1: Agent classifies + searches ───────────
    agent_result = await agent.run(finding_text)

    tokens = {
        "agent_prompt_tokens": agent_result.prompt_tokens,
        "agent_total_tokens": agent_result.total_tokens,
        "evaluator_prompt_tokens": 0,
        "evaluator_total_tokens": 0,
    }

    # ── Step 2: Short-circuit for pure misconfigurations ───
    if agent_result.finding_type == "PURE_MISCONFIGURATION":
        logger.info("Pure misconfiguration — no CVE search needed")
        return CveEnrichment(
            finding_type="PURE_MISCONFIGURATION",
            classification_reasoning=agent_result.reasoning,
            cve_ids=None,
            search_sources=agent_result.sources_queried,
        ), tokens

    # ── Step 3: No results from agent — try Google Search fallback ──
    if not agent_result.cve_results:
        logger.info(
            "No CVE candidates from database tools — "
            "trying Google Search grounding fallback",
        )
        google_results, g_prompt, g_total = await search_cves_via_google(
            finding_text=finding_text,
            software_component=agent_result.software_component,
            vendor=agent_result.vendor,
            version=agent_result.version,
            api_key=settings.gemini.api_key,
            model=settings.gemini.parse_model,
        )
        tokens["google_search_prompt_tokens"] = g_prompt
        tokens["google_search_total_tokens"] = g_total

        if google_results:
            logger.info(
                "Google Search fallback found %d CVE(s)",
                len(google_results),
            )
            agent_result.cve_results = google_results
            agent_result.sources_queried.append("GOOGLE_SEARCH")
        else:
            logger.info(
                "Google Search fallback returned no CVEs either",
            )
            return CveEnrichment(
                finding_type=agent_result.finding_type,
                classification_reasoning=agent_result.reasoning,
                software_component=agent_result.software_component,
                vendor=agent_result.vendor,
                version=agent_result.version,
                cve_ids=[],
                search_sources=agent_result.sources_queried
                + ["GOOGLE_SEARCH"],
            ), tokens

    # ── Step 4: Fetch full CVE details BEFORE evaluation ──
    # Cap candidates to prevent runaway API calls.
    candidates = agent_result.cve_results[:_MAX_CANDIDATES]
    logger.info(
        "Fetching full details for %d CVE candidates", len(candidates),
    )

    details_map = await _fetch_all_details(candidates, agent)

    # Build paired list: (search_result, detail_or_None)
    candidate_pairs: list[tuple[CveSearchResult, CveDetail | None]] = [
        (c, details_map.get(c.cve_id)) for c in candidates
    ]

    logger.info(
        "Details fetched: %d/%d have full records",
        sum(1 for _, d in candidate_pairs if d is not None),
        len(candidate_pairs),
    )

    # ── Step 5: Evaluate matches (batches of 5, parallel) ──
    evaluator = CveEvaluator(settings)
    eval_result, eval_tokens = evaluator.evaluate(
        finding_text,
        candidate_pairs,
        software_component=agent_result.software_component,
        vendor=agent_result.vendor,
        version=agent_result.version,
    )

    tokens["evaluator_prompt_tokens"] = eval_tokens["prompt_tokens"]
    tokens["evaluator_total_tokens"] = eval_tokens["total_tokens"]

    # ── Step 6: Collect approved CVE details ──────────
    max_cves = settings.cve.max_cves_per_finding
    final_ids = (eval_result.final_cve_ids or [])[:max_cves]

    approved_details = [
        details_map[cve_id]
        for cve_id in final_ids
        if cve_id in details_map
    ]

    return CveEnrichment(
        finding_type=agent_result.finding_type,
        classification_reasoning=agent_result.reasoning,
        software_component=agent_result.software_component,
        vendor=agent_result.vendor,
        version=agent_result.version,
        cve_ids=final_ids,
        cve_details=approved_details,
        evaluation_summary=eval_result.evaluations,
        search_sources=agent_result.sources_queried,
    ), tokens


# ── Helpers ─────────────────────────────────────────────


async def _fetch_all_details(
    candidates: list[CveSearchResult],
    agent: CveAgent,
) -> dict[str, CveDetail]:
    """Fetch full CVE details for all candidates in parallel.

    Returns a dict mapping cve_id → CveDetail for successful fetches.
    """
    unique_ids = list(dict.fromkeys(c.cve_id for c in candidates))
    details = await agent.fetch_details(unique_ids)
    return {d.cve_id: d for d in details}
