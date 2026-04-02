"""VulDB API client — optional CVE search source.

Only active if a VulDB API key is configured.  Free tier provides
~50 requests/day.  Uses advanced search by vendor + product + version.
"""

from __future__ import annotations

import logging

from src.config.settings import CveSettings
from src.scoring.models import CveDetail, CveSearchResult

from ._base import RateLimiter, create_client, request_with_retry

logger = logging.getLogger("scoring.cve_client")


class VuldbClient:
    """VulDB REST API client (search-only, detail via cve.org/NVD)."""

    def __init__(self, settings: CveSettings) -> None:
        self._base_url = "https://vuldb.com/?api"
        self._api_key = settings.vuldb_api_key or ""
        # 30 req/min max as recommended by VulDB docs
        self._limiter = RateLimiter(5, 60.0)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def search(
        self, product: str, version: str, **kwargs: str,
    ) -> list[CveSearchResult]:
        """Search VulDB by vendor/product/version via advancedsearch."""
        if not self._api_key:
            return []

        vendor = kwargs.get("vendor", "")
        search_parts = [f"product:{product}"]
        if vendor and vendor != "*":
            search_parts.insert(0, f"vendor:{vendor}")
        if version:
            search_parts.append(f"version:{version}")

        form_data = {
            "apikey": self._api_key,
            "advancedsearch": ",".join(search_parts),
            "details": "0",
        }

        async with create_client(15.0) as client:
            resp = await request_with_retry(
                client, "POST", self._base_url,
                limiter=self._limiter,
                data=form_data,
            )
        if not resp:
            return []

        return self._parse_results(resp.json(), product)

    async def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """VulDB detail not used — enrichment happens via cve.org/NVD."""
        return None

    @staticmethod
    def _parse_results(data: dict, product: str) -> list[CveSearchResult]:
        """Parse VulDB API response into CveSearchResult list."""
        results: list[CveSearchResult] = []
        seen: set[str] = set()

        for entry in data.get("result", []):
            source = entry.get("source", {})
            cve_info = source.get("cve", {})
            cve_id = cve_info.get("id", "")
            if not cve_id or not cve_id.startswith("CVE-") or cve_id in seen:
                continue
            seen.add(cve_id)

            title = entry.get("entry", {}).get("title", "")
            results.append(CveSearchResult(
                cve_id=cve_id,
                source="VULDB",
                description=title[:500],
                affected_product=product,
            ))

        logger.info("VulDB search for %s returned %d CVEs", product, len(results))
        return results
