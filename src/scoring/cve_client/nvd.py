"""NVD API v2.0 client with rate limiting and retry."""

from __future__ import annotations

import logging

from src.config.settings import CveSettings
from src.scoring.models import CveDetail, CveSearchResult

from ._base import (
    RateLimiter,
    create_client,
    extract_english_description,
    extract_references,
    request_with_retry,
)

logger = logging.getLogger("scoring.cve_client")


class NvdClient:
    """NVD API v2.0 client with rate limiting and retry."""

    def __init__(self, settings: CveSettings) -> None:
        self._base_url = settings.nvd_base_url
        self._timeout = settings.nvd_timeout
        self._headers: dict[str, str] = {}
        if settings.nvd_api_key:
            self._headers["apiKey"] = settings.nvd_api_key
        # 50 req/30s with key, 5 req/30s without
        max_tokens = 50 if settings.nvd_api_key else 5
        self._limiter = RateLimiter(max_tokens, 30.0)

    # ── Search methods ──────────────────────────────────

    async def search_by_cpe(
        self, product: str, version: str, vendor: str = "*",
    ) -> list[CveSearchResult]:
        """Search NVD via virtualMatchString CPE match."""
        cpe = f"cpe:2.3:a:{vendor}:{product}:{version}:*:*:*:*:*:*:*"
        params = {"virtualMatchString": cpe, "resultsPerPage": 20}
        return await self._search(params, source="NVD_CPE", target_product=product)

    async def search_by_keyword(
        self, keywords: str, max_results: int = 10,
    ) -> list[CveSearchResult]:
        """Search NVD via keyword text search (fallback)."""
        params = {"keywordSearch": keywords, "resultsPerPage": max_results}
        return await self._search(params, source="NVD_KEYWORD")

    async def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """Fetch a single CVE record from NVD by ID."""
        async with create_client(self._timeout) as client:
            resp = await request_with_retry(
                client, "GET", self._base_url,
                limiter=self._limiter,
                params={"cveId": cve_id},
                headers=self._headers,
            )
        if not resp:
            return None
        return self._parse_detail(resp.json())

    # ── Internal helpers ────────────────────────────────

    async def _search(
        self, params: dict, source: str,
        target_product: str | None = None,
    ) -> list[CveSearchResult]:
        async with create_client(self._timeout) as client:
            resp = await request_with_retry(
                client, "GET", self._base_url,
                limiter=self._limiter,
                params=params,
                headers=self._headers,
            )
        if not resp:
            return []
        data = resp.json()
        results: list[CveSearchResult] = []
        for vuln in data.get("vulnerabilities", []):
            cve = vuln.get("cve", {})
            cve_id = cve.get("id", "")
            desc = extract_english_description(cve.get("descriptions", []))
            affected_product, affected_versions = _extract_affected(
                cve, target_product=target_product,
            )
            results.append(CveSearchResult(
                cve_id=cve_id,
                source=source,
                description=desc[:500],
                affected_product=affected_product,
                affected_versions=affected_versions,
            ))
        logger.info("NVD %s search returned %d results", source, len(results))
        return results

    @staticmethod
    def _parse_detail(data: dict) -> CveDetail | None:
        """Parse NVD API response into a CveDetail."""
        vulns = data.get("vulnerabilities", [])
        if not vulns:
            return None
        cve = vulns[0].get("cve", {})
        cve_id = cve.get("id", "")
        desc = extract_english_description(
            cve.get("descriptions", []), max_len=1000,
        )
        cvss_vector, cvss_score, cvss_severity, cvss_source = _extract_cvss(
            cve.get("metrics", {}),
        )
        cwe_id = _extract_cwe(cve.get("weaknesses", []))
        refs = extract_references(cve.get("references", []))

        return CveDetail(
            cve_id=cve_id,
            description=desc,
            cvss_vector=cvss_vector,
            cvss_score=cvss_score,
            cvss_severity=cvss_severity,
            cvss_source=cvss_source,
            cwe_id=cwe_id,
            references=refs,
            published=cve.get("published"),
            source="NVD",
        )


# ── NVD-specific parsing helpers ────────────────────────


def _extract_affected(
    cve: dict, target_product: str | None = None,
) -> tuple[str | None, str | None]:
    """Extract product and version ranges from NVD CPE configurations.

    When ``target_product`` is provided, only cpeMatch entries whose CPE
    product field matches the target are included.  This filters out
    unrelated products that share the same CVE (e.g. Tomcat versions
    appearing in a Spring Framework CVE).
    """
    product_name: str | None = None
    all_ranges: list[str] = []

    for config in cve.get("configurations", []):
        for node in config.get("nodes", []):
            for match in node.get("cpeMatch", []):
                if not match.get("vulnerable", False):
                    continue
                criteria = match.get("criteria", "")
                parts = criteria.split(":")
                cpe_product = parts[4] if len(parts) > 4 else None

                # Filter: skip entries for different products
                if target_product and cpe_product:
                    if cpe_product.lower() != target_product.lower():
                        continue

                if not product_name and cpe_product:
                    product_name = cpe_product

                v_start = match.get("versionStartIncluding", "")
                v_end = match.get("versionEndExcluding", "")
                v_end_inc = match.get("versionEndIncluding", "")

                range_parts: list[str] = []
                if v_start:
                    range_parts.append(f">= {v_start}")
                if v_end:
                    range_parts.append(f"< {v_end}")
                elif v_end_inc:
                    range_parts.append(f"<= {v_end_inc}")

                if range_parts:
                    all_ranges.append(", ".join(range_parts))
                elif len(parts) > 5 and parts[5] not in ("*", "-"):
                    all_ranges.append(parts[5])

    # Deduplicate ranges while preserving order
    seen: set[str] = set()
    unique_ranges: list[str] = []
    for r in all_ranges:
        if r not in seen:
            seen.add(r)
            unique_ranges.append(r)

    if not unique_ranges:
        return product_name, None
    return product_name, " | ".join(unique_ranges)


def _extract_cvss(metrics: dict) -> tuple[str | None, float | None, str | None, str | None]:
    """Extract CVSS v3.1 score/vector/severity from NVD metrics."""
    for m in metrics.get("cvssMetricV31", []):
        cvss_data = m.get("cvssData", {})
        return (
            cvss_data.get("vectorString"),
            cvss_data.get("baseScore"),
            cvss_data.get("baseSeverity"),
            m.get("source", "NVD"),
        )
    return None, None, None, None


def _extract_cwe(weaknesses: list[dict]) -> str | None:
    """Extract the first CWE-ID from NVD weaknesses list."""
    for w in weaknesses:
        for wd in w.get("description", []):
            val = wd.get("value", "")
            if val.startswith("CWE-"):
                return val
    return None
