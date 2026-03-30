"""CVE pipeline — orchestrator composing classification → search → evaluation.

Single-responsibility: sequences the CVE enrichment stages in the correct
order.  Each stage is delegated to its dedicated module.

Flow:
  1. Classify finding (3-class: PRODUCT_VULNERABILITY / WEAK_DEFAULT / PURE_MISCONFIGURATION)
  2. If PURE_MISCONFIGURATION → return immediately (cve_ids=null)
  3. If PRODUCT_VULNERABILITY or WEAK_DEFAULT:
     a. Short-circuit: explicit CVE IDs from text
     b. API search: NVD CPE (using normalized cpe_vendor/cpe_product) + OSV.dev (parallel)
     c. Fetch CVE details from cve.org (enrichment)
     d. LLM evaluation judge (validate matches)
     e. Filter to final CVE IDs
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.config.settings import AppSettings
from src.scoring.cve_evaluator import CveEvaluator
from src.scoring.cve_searcher import CveSearcher
from src.scoring.finding_classifier import FindingClassifier
from src.scoring.models import CveEnrichment

logger = logging.getLogger("scoring.cve_pipeline")


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
        ), {"classifier_prompt_tokens": 0, "classifier_total_tokens": 0,
            "evaluator_prompt_tokens": 0, "evaluator_total_tokens": 0}

    start = time.time()

    # ── Step 1: Classify finding ──────────────────────
    classifier = FindingClassifier(settings)
    classification, classifier_tokens = classifier.classify(finding_text)

    tokens = {
        "classifier_prompt_tokens": classifier_tokens["prompt_tokens"],
        "classifier_total_tokens": classifier_tokens["total_tokens"],
        "evaluator_prompt_tokens": 0,
        "evaluator_total_tokens": 0,
    }

    # ── Step 2: Short-circuit for pure misconfigurations ───
    if classification.finding_type == "PURE_MISCONFIGURATION":
        duration = time.time() - start
        logger.info("Pure misconfiguration — no CVE search needed (%.1fs)", duration)
        return CveEnrichment(
            finding_type="PURE_MISCONFIGURATION",
            classification_reasoning=classification.reasoning,
            cve_ids=None,
            enrichment_duration_seconds=round(duration, 2),
        ), tokens

    # ── Step 3: CVE search + evaluation (PRODUCT_VULNERABILITY or WEAK_DEFAULT) ──
    enrichment = asyncio.run(
        _async_search_and_evaluate(
            finding_text, classification, settings, tokens,
        ),
    )
    enrichment.enrichment_duration_seconds = round(time.time() - start, 2)

    logger.info(
        "CVE enrichment complete: %s CVEs in %.1fs",
        len(enrichment.cve_ids) if enrichment.cve_ids else 0,
        enrichment.enrichment_duration_seconds,
    )

    return enrichment, tokens


async def _async_search_and_evaluate(
    finding_text: str,
    classification,
    settings: AppSettings,
    tokens: dict,
) -> CveEnrichment:
    """Async portion: search for CVEs, fetch details, evaluate."""

    searcher = CveSearcher(settings.cve)

    # ── Search ────────────────────────────────────────
    search_results, sources_queried = await searcher.search(classification)

    if not search_results:
        logger.info("No CVE candidates found from any source")
        return CveEnrichment(
            finding_type=classification.finding_type,
            classification_reasoning=classification.reasoning,
            software_component=classification.software_component,
            vendor=classification.vendor,
            version=classification.version,
            cve_ids=[],
            search_sources=sources_queried,
        )

    # ── Fetch details ─────────────────────────────────
    cve_ids = [r.cve_id for r in search_results]
    details = await searcher.fetch_details(cve_ids)

    # ── Evaluate matches ──────────────────────────────
    evaluator = CveEvaluator(settings)
    eval_result, eval_tokens = evaluator.evaluate(finding_text, search_results)

    tokens["evaluator_prompt_tokens"] = eval_tokens["prompt_tokens"]
    tokens["evaluator_total_tokens"] = eval_tokens["total_tokens"]

    # Filter details to only approved CVEs
    approved_set = set(eval_result.final_cve_ids)
    approved_details = [d for d in details if d.cve_id in approved_set]

    return CveEnrichment(
        finding_type=classification.finding_type,
        classification_reasoning=classification.reasoning,
        software_component=classification.software_component,
        vendor=classification.vendor,
        version=classification.version,
        cve_ids=eval_result.final_cve_ids if eval_result.final_cve_ids else [],
        cve_details=approved_details,
        evaluation_summary=eval_result.evaluations,
        search_sources=sources_queried,
    )
