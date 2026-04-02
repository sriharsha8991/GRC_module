"""cve.org (cveawg.mitre.org) client for CVE detail enrichment.

Not a search API — used to fetch rich CVE JSON 5.x records
including CISA-ADP container data (SSVC, KEV).
"""

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


class CveOrgClient:
    """cve.org client for CVE detail with CISA-ADP enrichment."""

    def __init__(self, settings: CveSettings) -> None:
        self._base_url = settings.cve_org_base_url
        self._limiter = RateLimiter(10, 60.0)  # Conservative: 10 req/min

    async def search(
        self, product: str, version: str, **kwargs: str,
    ) -> list[CveSearchResult]:
        """cve.org is not a search API — returns empty list."""
        return []

    async def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """Fetch a CVE record from cve.org with CISA-ADP enrichment."""
        url = f"{self._base_url}/cve/{cve_id}"
        async with create_client(10.0) as client:
            resp = await request_with_retry(
                client, "GET", url, limiter=self._limiter,
            )
        if not resp:
            return None
        return _parse_cve5(resp.json(), cve_id)


# ── CVE JSON 5.x parsing ───────────────────────────────


def _parse_cve5(data: dict, cve_id: str) -> CveDetail | None:
    """Parse a CVE JSON 5.x record into a CveDetail."""
    containers = data.get("containers", {})
    cna = containers.get("cna", {})

    desc = extract_english_description(cna.get("descriptions", []), max_len=1000)
    cvss_vector, cvss_score, cvss_severity = _extract_cna_cvss(cna)
    cvss_source = "CNA"
    cwe_id = _extract_cna_cwe(cna)
    refs = extract_references(cna.get("references", []))
    published = data.get("cveMetadata", {}).get("datePublished")

    # CISA-ADP enrichment may override CVSS and add SSVC/KEV
    adp_data = _extract_cisa_adp(containers.get("adp", []))
    if adp_data.get("cvss_vector") and not cvss_vector:
        cvss_vector = adp_data["cvss_vector"]
        cvss_score = adp_data["cvss_score"]
        cvss_severity = adp_data["cvss_severity"]
        cvss_source = "CISA-ADP"

    return CveDetail(
        cve_id=cve_id,
        description=desc,
        cvss_vector=cvss_vector,
        cvss_score=cvss_score,
        cvss_severity=cvss_severity,
        cvss_source=cvss_source,
        cwe_id=cwe_id,
        references=refs,
        published=published,
        source="CVE_ORG",
        kev=adp_data.get("kev", False),
        ssvc_exploitation=adp_data.get("ssvc_exploitation"),
        ssvc_automatable=adp_data.get("ssvc_automatable"),
        ssvc_technical_impact=adp_data.get("ssvc_technical_impact"),
    )


def _extract_cna_cvss(cna: dict) -> tuple[str | None, float | None, str | None]:
    """Extract CVSS v3.1 vector/score/severity from the CNA container."""
    for metric in cna.get("metrics", []):
        v31 = metric.get("cvssV3_1", {})
        if v31:
            return (
                v31.get("vectorString"),
                v31.get("baseScore"),
                v31.get("baseSeverity"),
            )
    return None, None, None


def _extract_cna_cwe(cna: dict) -> str | None:
    """Extract the first CWE-ID from the CNA problemTypes."""
    for pt in cna.get("problemTypes", []):
        for ptd in pt.get("descriptions", []):
            cwe = ptd.get("cweId", "")
            if cwe:
                return cwe
    return None


def _extract_cisa_adp(adp_list: list[dict]) -> dict:
    """Extract CISA-ADP enrichment (SSVC, KEV, CVSS) from ADP containers.

    Returns a flat dict with keys:
      kev, ssvc_exploitation, ssvc_automatable, ssvc_technical_impact,
      cvss_vector, cvss_score, cvss_severity
    """
    result: dict = {"kev": False}

    for adp in adp_list:
        provider = adp.get("providerMetadata", {}).get("shortName", "")
        if "CISA" not in provider.upper():
            continue

        for metric in adp.get("metrics", []):
            other = metric.get("other", {})
            if other.get("type") == "ssvc":
                _parse_ssvc_options(other.get("content", {}), result)
            if other.get("type") == "kev":
                result["kev"] = True

            v31 = metric.get("cvssV3_1", {})
            if v31:
                result["cvss_vector"] = v31.get("vectorString")
                result["cvss_score"] = v31.get("baseScore")
                result["cvss_severity"] = v31.get("baseSeverity")

        break  # Only process first CISA ADP container

    return result


def _parse_ssvc_options(content: dict, out: dict) -> None:
    """Parse SSVC decision point options into the output dict."""
    for opt in content.get("options", []):
        if "Exploitation" in opt:
            out["ssvc_exploitation"] = opt["Exploitation"]
        if "Automatable" in opt:
            out["ssvc_automatable"] = opt["Automatable"]
        if "Technical Impact" in opt:
            out["ssvc_technical_impact"] = opt["Technical Impact"]
