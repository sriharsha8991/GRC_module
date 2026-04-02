"""OSV.dev client for open-source vulnerability search.

OSV.dev is ecosystem-agnostic but ecosystem-aware.  It aggregates
vulnerability data from 35+ ecosystems including language package
managers (npm, PyPI, Maven …), OS distributions (Debian, Alpine,
Ubuntu …), and supply-chain feeds (GitHub Actions, Wolfi, Bitnami …).

Key facts (from https://ossf.github.io/osv-schema/):
  • Ecosystem is **required** — queries without it return 400.
  • Ecosystem values are **case-sensitive** (``PyPI`` not ``pypi``).
  • OS-level ecosystems use versioned suffixes:
    ``Debian:12``, ``Alpine:v3.18``, ``Ubuntu:22.04:LTS``.
  • ``"OS"`` is NOT a valid ecosystem — it must be mapped to a real
    distro ecosystem or the query must be skipped.
  • CVE IDs appear in ``aliases`` (language ecosystems) *or*
    ``upstream`` (OS distribution advisories).
  • No rate limits.

Called exclusively by CveAgent which crafts query parameters via
Gemini function calling.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.config.settings import CveSettings
from src.scoring.models import CveDetail, CveSearchResult

from ._base import create_client, request_with_retry

logger = logging.getLogger("scoring.cve_client")


# ── Query model ─────────────────────────────────────────


class OsvQuery(BaseModel):
    """Parameters for a single OSV API query."""

    package_name: str = Field(description="Exact package name in the ecosystem's registry.")
    ecosystem: str = Field(description="Exact OSV ecosystem string, case-sensitive.")
    version: str | None = Field(default=None, description="Exact version to query, or null for all.")


# ── Client ──────────────────────────────────────────────


class OsvClient:
    """OSV.dev API client.

    Exposes ``execute_query`` for the CVE agent to call directly with
    agent-crafted query parameters.
    """

    def __init__(self, settings: CveSettings) -> None:
        self._base_url = settings.osv_base_url

    async def execute_query(
        self, query: OsvQuery,
    ) -> list[CveSearchResult]:
        """Execute a single OSV API query."""
        body: dict = {
            "package": {
                "name": query.package_name,
                "ecosystem": query.ecosystem,
            },
        }
        if query.version:
            body["version"] = query.version

        url = f"{self._base_url}/v1/query"
        async with create_client(10.0) as client:
            resp = await request_with_retry(client, "POST", url, json=body)
        if not resp:
            return []

        resp_data = resp.json()

        if "error" in resp_data or "code" in resp_data:
            logger.warning(
                "OSV API error for %s/%s: %s",
                query.ecosystem, query.package_name,
                resp_data.get("message", resp_data),
            )
            return []

        results: list[CveSearchResult] = []
        seen: set[str] = set()

        for vuln in resp_data.get("vulns", []):
            for cve_id in _extract_cve_ids(vuln):
                if cve_id in seen:
                    continue
                seen.add(cve_id)
                results.append(CveSearchResult(
                    cve_id=cve_id,
                    source="OSV",
                    description=vuln.get("summary", "")[:500],
                    affected_product=query.package_name,
                    affected_versions=_extract_version_range(vuln),
                ))

        logger.info(
            "OSV search for %s/%s returned %d CVEs",
            query.ecosystem, query.package_name, len(results),
        )
        return results

    async def fetch_detail(self, cve_id: str) -> CveDetail | None:
        """OSV.dev doesn't provide NVD-level detail — return None."""
        return None


# ── OSV-specific parsing helpers ────────────────────────


def _extract_cve_ids(vuln: dict) -> list[str]:
    """Extract CVE IDs from an OSV record.

    CVE IDs may appear in several locations:
      1. ``aliases`` — the standard place for language ecosystems
      2. ``upstream`` — used by OS distro advisories (Debian, Ubuntu …)
      3. ``id`` — some records use CVE-* as their primary ID
    """
    cve_ids: list[str] = []

    # 1. Check aliases (most language ecosystems)
    for alias in vuln.get("aliases", []):
        if alias.startswith("CVE-"):
            cve_ids.append(alias)

    # 2. Check upstream (OS distro advisories like DEBIAN-CVE-*)
    for upstream_id in vuln.get("upstream", []):
        if upstream_id.startswith("CVE-") and upstream_id not in cve_ids:
            cve_ids.append(upstream_id)

    # 3. Check the record ID itself
    if not cve_ids:
        osv_id = vuln.get("id", "")
        if osv_id.startswith("CVE-"):
            cve_ids.append(osv_id)

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
