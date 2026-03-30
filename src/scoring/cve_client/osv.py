"""OSV.dev client for open-source package vulnerability search.

Zero rate limits — ideal for npm/PyPI/Maven/Go ecosystem packages.
"""

from __future__ import annotations

import logging

from src.config.settings import CveSettings
from src.scoring.models import CveDetail, CveSearchResult

from ._base import create_client, request_with_retry

logger = logging.getLogger("scoring.cve_client")


class OsvClient:
    """OSV.dev client for open-source package vulnerability search."""

    def __init__(self, settings: CveSettings) -> None:
        self._base_url = settings.osv_base_url

    async def search(
        self, product: str, version: str, **kwargs: str,
    ) -> list[CveSearchResult]:
        """Query OSV.dev by package name, ecosystem, and version."""
        ecosystem = kwargs.get("ecosystem", "")
        if not ecosystem:
            return []

        body: dict = {
            "package": {"name": product, "ecosystem": ecosystem},
        }
        if version:
            body["version"] = version

        url = f"{self._base_url}/v1/query"
        async with create_client(10.0) as client:
            resp = await request_with_retry(client, "POST", url, json=body)
        if not resp:
            return []

        results: list[CveSearchResult] = []
        seen: set[str] = set()

        for vuln in resp.json().get("vulns", []):
            for cve_id in _extract_cve_ids(vuln):
                if cve_id in seen:
                    continue
                seen.add(cve_id)
                results.append(CveSearchResult(
                    cve_id=cve_id,
                    source="OSV",
                    description=vuln.get("summary", "")[:500],
                    affected_product=product,
                    affected_versions=_extract_version_range(vuln),
                ))

        logger.info(
            "OSV search for %s/%s returned %d CVEs",
            ecosystem, product, len(results),
        )
        return results

    async def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """OSV.dev doesn't provide NVD-level detail — return None."""
        return None


# ── OSV-specific parsing helpers ────────────────────────


def _extract_cve_ids(vuln: dict) -> list[str]:
    """Extract CVE IDs from OSV aliases (skip GHSA-only records)."""
    aliases = vuln.get("aliases", [])
    cve_ids = [a for a in aliases if a.startswith("CVE-")]
    if not cve_ids:
        osv_id = vuln.get("id", "")
        if osv_id.startswith("CVE-"):
            cve_ids = [osv_id]
    return cve_ids


def _extract_version_range(vuln: dict) -> str | None:
    """Extract the first affected version range from an OSV record."""
    for aff in vuln.get("affected", []):
        for rng in aff.get("ranges", []):
            parts: list[str] = []
            for ev in rng.get("events", []):
                if "introduced" in ev:
                    parts.append(f">= {ev['introduced']}")
                if "fixed" in ev:
                    parts.append(f"< {ev['fixed']}")
            if parts:
                return ", ".join(parts)
    return None
