"""CVE searcher — multi-source CVE search with short-circuit logic.

Single-responsibility: given a FindingClassification with component
metadata, searches multiple sources for matching CVE IDs.

Search priority:
  1. Explicit CVE IDs (verbatim in text) → immediate return
  2. NVD virtualMatchString CPE search + OSV.dev (parallel)
  3. NVD keyword search (fallback if step 2 yields nothing)
  4. Deduplicate across sources
"""

from __future__ import annotations

import asyncio
import logging

from src.config.settings import CveSettings
from src.scoring.cve_client import CveOrgClient, NvdClient, OsvClient
from src.scoring.models import CveDetail, CveSearchResult, FindingClassification

logger = logging.getLogger("scoring.cve_searcher")


class CveSearcher:
    """Multi-source CVE search with short-circuit deterministic paths."""

    def __init__(self, settings: CveSettings) -> None:
        self._settings = settings
        self._nvd = NvdClient(settings)
        self._osv = OsvClient(settings)
        self._cve_org = CveOrgClient(settings)
        self._max_cves = settings.max_cves_per_finding

    async def search(
        self, classification: FindingClassification,
    ) -> tuple[list[CveSearchResult], list[str]]:
        """Search for CVE IDs matching a classified vulnerability.

        Args:
            classification: LLM classification with component metadata.

        Returns:
            (search_results, sources_queried)
        """
        sources_queried: list[str] = []

        # ── Step 1: Explicit CVE IDs (deterministic, zero API calls) ──
        if classification.explicit_cve_ids:
            logger.info(
                "Short-circuit: %d explicit CVE IDs in text",
                len(classification.explicit_cve_ids),
            )
            results = [
                CveSearchResult(
                    cve_id=cve_id,
                    source="EXPLICIT",
                    description="Extracted from finding text",
                )
                for cve_id in classification.explicit_cve_ids[:self._max_cves]
            ]
            return results, ["EXPLICIT"]

        # ── Step 2: Parallel API search (NVD CPE + OSV) ──
        product = classification.software_component
        version = classification.version or ""
        ecosystem = classification.ecosystem or ""

        # Use LLM-normalized CPE fields for precise NVD queries
        cpe_vendor = classification.cpe_vendor or "*"
        cpe_product = classification.cpe_product

        if not product:
            logger.warning("No software component extracted — skipping API search")
            return [], []

        # Determine the CPE product name: prefer LLM-normalized, fallback to raw
        nvd_product = cpe_product if cpe_product else product.lower().replace(" ", "_")

        tasks: list[asyncio.Task] = []
        task_sources: list[str] = []

        # NVD CPE search (using normalized CPE vendor + product)
        tasks.append(asyncio.create_task(
            self._nvd.search_by_cpe(nvd_product, version, cpe_vendor),
        ))
        task_sources.append("NVD_CPE")
        sources_queried.append("NVD_CPE")

        # OSV search (if ecosystem is known)
        if ecosystem and ecosystem != "OS":
            tasks.append(asyncio.create_task(
                self._osv.search(product.lower(), version, ecosystem=ecosystem),
            ))
            task_sources.append("OSV")
            sources_queried.append("OSV")

        # Gather results
        api_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[CveSearchResult] = []
        for i, result in enumerate(api_results):
            if isinstance(result, Exception):
                logger.error("Search source %s failed: %s", task_sources[i], result)
                continue
            all_results.extend(result)

        # ── Step 4: NVD keyword fallback (if no results yet) ──
        if not all_results and product:
            logger.info("No CPE/OSV results — falling back to NVD keyword search")
            keyword = f"{product} {version}".strip()
            try:
                keyword_results = await self._nvd.search_by_keyword(keyword)
                all_results.extend(keyword_results)
                sources_queried.append("NVD_KEYWORD")
            except Exception:
                logger.exception("NVD keyword search failed")

        # ── Step 5: Deduplicate by CVE ID ──
        deduped = self._deduplicate(all_results)

        logger.info(
            "Search complete: %d unique CVEs from %s",
            len(deduped), sources_queried,
        )
        return deduped[:self._max_cves], sources_queried

    async def fetch_details(
        self, cve_ids: list[str],
    ) -> list[CveDetail]:
        """Fetch full CVE details for a list of CVE IDs.

        Tries cve.org first (richer CISA-ADP data), falls back to NVD.
        """
        tasks = [self._fetch_single_detail(cve_id) for cve_id in cve_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        details: list[CveDetail] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Detail fetch failed: %s", result)
                continue
            if result is not None:
                details.append(result)
        return details

    async def _fetch_single_detail(self, cve_id: str) -> CveDetail | None:
        """Fetch detail from cve.org first, fall back to NVD."""
        # Try cve.org (has CISA-ADP enrichment)
        detail = await self._cve_org.fetch_detail(cve_id)
        if detail:
            return detail
        # Fallback to NVD
        return await self._nvd.fetch_detail(cve_id)

    @staticmethod
    def _deduplicate(results: list[CveSearchResult]) -> list[CveSearchResult]:
        """Deduplicate by CVE ID, preferring NVD_CPE > OSV > NVD_KEYWORD."""
        source_priority = {"EXPLICIT": 0, "NVD_CPE": 1, "OSV": 2, "NVD_KEYWORD": 3}
        seen: dict[str, CveSearchResult] = {}
        for r in results:
            existing = seen.get(r.cve_id)
            if existing is None:
                seen[r.cve_id] = r
            else:
                # Keep the one with higher priority (lower number)
                if source_priority.get(r.source, 99) < source_priority.get(existing.source, 99):
                    seen[r.cve_id] = r
        return list(seen.values())
