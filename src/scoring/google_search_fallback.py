"""Google Search grounding fallback for CVE discovery.

Last-resort layer: when all CVE database tools (NVD, OSV, VulDB) return
zero results for a PRODUCT_VULNERABILITY or WEAK_DEFAULT finding, this
module uses Gemini's built-in Google Search grounding to find CVE IDs
from the open web.

Results are returned as CveSearchResult objects with source="GOOGLE_SEARCH"
and passed to the evaluator unchanged.
"""

from __future__ import annotations

import logging
import re

from google.genai import types

from src.config.genai_client import get_genai_client
from src.scoring.cve_tools import GOOGLE_SEARCH_PROMPT
from src.scoring.models import CveSearchResult

logger = logging.getLogger("scoring.google_search_fallback")

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}")


async def search_cves_via_google(
    finding_text: str,
    software_component: str | None,
    vendor: str | None,
    version: str | None,
    api_key: str,
    model: str,
) -> tuple[list[CveSearchResult], int, int]:
    """Use Gemini Google Search grounding to find CVE IDs.

    Args:
        finding_text: Original security finding text.
        software_component: Identified software name.
        vendor: Identified vendor.
        version: Identified version.
        api_key: Gemini API key.
        model: Gemini model name.

    Returns:
        (results, prompt_tokens, total_tokens)
    """
    client = get_genai_client(api_key)

    prompt = GOOGLE_SEARCH_PROMPT.format(
        software_component=software_component or "unknown",
        vendor=vendor or "unknown",
        version=version or "unspecified",
        finding_text=finding_text[:500],  # Limit to avoid token bloat
    )

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.5,
    )

    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
    except Exception:
        logger.exception("Google Search grounding call failed")
        return [], 0, 0

    # Track tokens
    prompt_tokens = 0
    total_tokens = 0
    if response.usage_metadata:
        prompt_tokens = response.usage_metadata.prompt_token_count or 0
        total_tokens = response.usage_metadata.total_token_count or 0

    # Extract response text
    response_text = response.text or ""
    if not response_text:
        logger.info("Google Search grounding returned empty response")
        return [], prompt_tokens, total_tokens

    # Parse CVE IDs from response
    cve_ids = list(dict.fromkeys(_CVE_PATTERN.findall(response_text)))

    if not cve_ids:
        logger.info("Google Search grounding found no CVE IDs")
        return [], prompt_tokens, total_tokens

    # Build CveSearchResult objects
    results: list[CveSearchResult] = []
    for cve_id in cve_ids:
        results.append(
            CveSearchResult(
                cve_id=cve_id,
                source="GOOGLE_SEARCH",
                description=f"Found via Google Search grounding",
                affected_product=software_component,
            ),
        )

    logger.info(
        "Google Search grounding found %d CVE(s): %s",
        len(results),
        [r.cve_id for r in results],
    )
    return results, prompt_tokens, total_tokens
