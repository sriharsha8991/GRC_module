"""CVE agent tool executor — dispatches and executes tool calls.

Single-responsibility: handles the execution of Gemini function-calling
tool calls against CVE data sources (NVD, OSV, VulDB).  Collects
results and tracks which sources were queried.
"""

from __future__ import annotations

import logging

from src.scoring.cve_client import NvdClient, OsvClient, VuldbClient
from src.scoring.cve_client.osv import OsvQuery
from src.scoring.models import CveSearchResult

logger = logging.getLogger("scoring.tool_executor")


class ToolExecutor:
    """Executes CVE search tool calls and collects results."""

    def __init__(
        self,
        nvd: NvdClient,
        osv: OsvClient,
        vuldb: VuldbClient,
    ) -> None:
        self.nvd = nvd
        self._osv = osv
        self._vuldb = vuldb

        # Accumulated state — reset between agent runs
        self.collected_results: list[CveSearchResult] = []
        self.sources_queried: list[str] = []

    def reset(self) -> None:
        """Clear accumulated results for a new agent run."""
        self.collected_results = []
        self.sources_queried = []

    async def dispatch(self, name: str, args: dict) -> dict:
        """Dispatch a tool call to the appropriate handler."""
        handlers = {
            "search_nvd_by_cpe": self._search_nvd_cpe,
            "search_nvd_by_keyword": self._search_nvd_keyword,
            "search_osv": self._search_osv,
            "search_vuldb": self._search_vuldb,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        return await handler(args)

    # ── Individual tool handlers ───────────────────────

    async def _search_nvd_cpe(self, args: dict) -> dict:
        vendor = args.get("vendor", "*")
        product = args.get("product", "")
        version = args.get("version", "*")

        source = "NVD_CPE" if version != "*" else "NVD_CPE_VERSIONLESS"
        self.sources_queried.append(source)

        results = await self.nvd.search_by_cpe(product, version, vendor)
        self.collected_results.extend(results)

        response = format_tool_response(results)
        logger.info(
            "NVD CPE search (%s/%s/%s): %d CVEs",
            vendor, product, version, len(results),
        )
        logger.debug("NVD CPE response: %s", response)
        return response

    async def _search_nvd_keyword(self, args: dict) -> dict:
        keywords = args.get("keywords", "")
        self.sources_queried.append("NVD_KEYWORD")

        results = await self.nvd.search_by_keyword(keywords)
        self.collected_results.extend(results)

        response = format_tool_response(results)
        logger.info(
            "NVD keyword search (%s): %d CVEs", keywords, len(results),
        )
        logger.debug("NVD keyword response: %s", response)
        return response

    async def _search_osv(self, args: dict) -> dict:
        package_name = args.get("package_name", "")
        ecosystem = args.get("ecosystem", "")
        version = args.get("version")

        self.sources_queried.append("OSV")

        query = OsvQuery(
            package_name=package_name,
            ecosystem=ecosystem,
            version=version,
        )
        results = await self._osv.execute_query(query)
        self.collected_results.extend(results)

        response = format_tool_response(results)
        logger.info(
            "OSV search (%s/%s): %d CVEs",
            ecosystem, package_name, len(results),
        )
        logger.debug("OSV response: %s", response)
        return response

    async def _search_vuldb(self, args: dict) -> dict:
        product = args.get("product", "")
        version = args.get("version", "")
        vendor = args.get("vendor", "")

        self.sources_queried.append("VULDB")

        results = await self._vuldb.search(
            product, version, vendor=vendor,
        )
        self.collected_results.extend(results)

        response = format_tool_response(results)
        logger.info(
            "VulDB search (%s/%s): %d CVEs",
            vendor, product, len(results),
        )
        logger.debug("VulDB response: %s", response)
        return response


# ── Shared utilities ───────────────────────────────────


def format_tool_response(results: list[CveSearchResult]) -> dict:
    """Format search results as a dict for the model to consume."""
    return {
        "cve_count": len(results),
        "cves": [
            {
                "cve_id": r.cve_id,
                "description": (
                    r.description[:200] if r.description else ""
                ),
            }
            for r in results[:15]  # Cap to avoid token bloat
        ],
    }


def deduplicate(
    results: list[CveSearchResult],
    source_priority: dict[str, int],
) -> list[CveSearchResult]:
    """Deduplicate by CVE ID, preferring higher-priority sources."""
    seen: dict[str, CveSearchResult] = {}
    for r in results:
        existing = seen.get(r.cve_id)
        if existing is None:
            seen[r.cve_id] = r
        else:
            if source_priority.get(
                r.source, 99,
            ) < source_priority.get(existing.source, 99):
                seen[r.cve_id] = r
    return list(seen.values())
