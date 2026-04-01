"""CVE agent — Gemini function-calling agent for CVE discovery.

Orchestrates tool-use via Gemini function calling to classify security
findings and search CVE databases.  Tool declarations live in
``cve_tools`` and execution logic lives in ``tool_executor``.

Flow:
  1. Receives a security finding
  2. Gemini reasons about classification and decides which tools to call
  3. ToolExecutor dispatches search calls (NVD, OSV, VulDB)
  4. Agent reports classification and collected CVE IDs
  5. Downstream CveEvaluator validates the matches
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from google.genai import types

from src.config.genai_client import get_genai_client
from src.config.settings import AppSettings
from src.scoring.cve_client import CveOrgClient, NvdClient, OsvClient, VuldbClient
from src.scoring.cve_tools import (
    MAX_TURNS,
    SOURCE_PRIORITY,
    SYSTEM_PROMPT,
    TOOL_REPORT_FINDING,
    TOOL_SEARCH_NVD_BY_CPE,
    TOOL_SEARCH_NVD_BY_KEYWORD,
    TOOL_SEARCH_OSV,
    TOOL_SEARCH_VULDB,
)
from src.scoring.models import CveDetail, CveSearchResult
from src.scoring.tool_executor import ToolExecutor, deduplicate

logger = logging.getLogger("scoring.cve_agent")

# ── Agent result ────────────────────────────────────────


@dataclass
class AgentResult:
    """Output from the CVE agent run."""

    finding_type: str = "PRODUCT_VULNERABILITY"
    reasoning: str = ""
    software_component: str | None = None
    vendor: str | None = None
    version: str | None = None
    cve_results: list[CveSearchResult] = field(default_factory=list)
    sources_queried: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    total_tokens: int = 0


# ── Agent ──────────────────────────────────────────────


class CveAgent:
    """Gemini function-calling agent for CVE discovery."""

    def __init__(self, settings: AppSettings) -> None:
        self._client = get_genai_client(settings.gemini.api_key)
        self._model = settings.gemini.parse_model

        # CVE data source clients
        nvd = NvdClient(settings.cve)
        osv = OsvClient(settings.cve)
        vuldb = VuldbClient(settings.cve)
        self._cve_org = CveOrgClient(settings.cve)

        # Tool executor handles search dispatch and result collection
        self._executor = ToolExecutor(nvd, osv, vuldb)

        # Build tool declarations (conditionally include VulDB)
        tool_defs = [
            TOOL_SEARCH_NVD_BY_CPE,
            TOOL_SEARCH_NVD_BY_KEYWORD,
            TOOL_SEARCH_OSV,
        ]
        if vuldb.is_configured:
            tool_defs.append(TOOL_SEARCH_VULDB)
        tool_defs.append(TOOL_REPORT_FINDING)

        self._tools = types.Tool(function_declarations=tool_defs)
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[self._tools],
            temperature=1.0,  # Gemini 2.5 recommended default
        )

        # Per-run state (reset on each run)
        self._report_args: dict | None = None
        self._prompt_tokens = 0
        self._total_tokens = 0

    async def run(self, finding_text: str) -> AgentResult:
        """Run the agent on a security finding.

        The agent classifies the finding, calls search tools, and
        collects CVE IDs — all driven by Gemini's reasoning.

        Args:
            finding_text: The raw security finding text.

        Returns:
            AgentResult with classification and CVE search results.
        """
        # Reset per-run state
        self._executor.reset()
        self._report_args = None
        self._prompt_tokens = 0
        self._total_tokens = 0

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part(text=(
                    "Analyze this security finding and search for "
                    "relevant CVEs:\n\n"
                    f"{finding_text}"
                ))],
            ),
        ]

        for turn in range(MAX_TURNS):
            logger.debug("Agent turn %d", turn + 1)

            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=self._config,
            )

            # Track tokens
            if response.usage_metadata:
                self._prompt_tokens += (
                    response.usage_metadata.prompt_token_count or 0
                )
                self._total_tokens += (
                    response.usage_metadata.total_token_count or 0
                )

            # Check for function calls
            function_calls = response.function_calls
            if not function_calls:
                logger.info(
                    "Agent finished (no more function calls) at turn %d",
                    turn + 1,
                )
                break

            # Append model response to conversation
            contents.append(response.candidates[0].content)

            # Execute all function calls (parallel for search tools)
            fn_response_parts = await self._execute_tool_calls(
                function_calls,
            )

            # Append function responses
            contents.append(
                types.Content(role="user", parts=fn_response_parts),
            )

            # Stop if report_finding was called
            if self._report_args is not None:
                logger.info(
                    "Agent reported finding at turn %d", turn + 1,
                )
                break
        else:
            logger.warning(
                "Agent hit max turns (%d) — forcing completion",
                MAX_TURNS,
            )

        # Build result — pass all deduplicated CVEs to evaluator;
        # final cap is applied post-evaluation in the pipeline.
        deduped = deduplicate(
            self._executor.collected_results, SOURCE_PRIORITY,
        )

        result = AgentResult(
            cve_results=deduped,
            sources_queried=list(set(self._executor.sources_queried)),
            prompt_tokens=self._prompt_tokens,
            total_tokens=self._total_tokens,
        )

        if self._report_args:
            result.finding_type = self._report_args.get(
                "finding_type", "PRODUCT_VULNERABILITY",
            )
            result.reasoning = self._report_args.get("reasoning", "")
            result.software_component = self._report_args.get(
                "software_component",
            )
            result.vendor = self._report_args.get("vendor")
            result.version = self._report_args.get("version")

        logger.info(
            "Agent complete: type=%s, %d CVEs from %s (%d tokens)",
            result.finding_type,
            len(result.cve_results),
            result.sources_queried,
            result.total_tokens,
        )
        return result

    async def fetch_details(
        self, cve_ids: list[str],
    ) -> list[CveDetail]:
        """Fetch full CVE details — tries cve.org first, falls back
        to NVD."""
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

    async def _fetch_single_detail(
        self, cve_id: str,
    ) -> CveDetail | None:
        """Fetch detail from cve.org first, fall back to NVD."""
        detail = await self._cve_org.fetch_detail(cve_id)
        if detail:
            return detail
        return await self._executor.nvd.fetch_detail(cve_id)

    # ── Tool execution ──────────────────────────────────

    async def _execute_tool_calls(
        self, function_calls: list,
    ) -> list[types.Part]:
        """Execute tool calls, running search tools in parallel."""
        search_calls = []
        report_call = None

        for fc in function_calls:
            if fc.name == "report_finding":
                report_call = fc
            else:
                search_calls.append(fc)

        response_parts: list[types.Part] = []

        # Execute search tools in parallel via ToolExecutor
        if search_calls:
            search_tasks = [
                self._executor.dispatch(
                    fc.name, dict(fc.args) if fc.args else {},
                )
                for fc in search_calls
            ]
            search_results = await asyncio.gather(
                *search_tasks, return_exceptions=True,
            )

            for fc, result in zip(search_calls, search_results):
                if isinstance(result, Exception):
                    logger.error("Tool %s failed: %s", fc.name, result)
                    result = {"error": str(result)}
                response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result},
                    ),
                )

        # Handle report_finding (captures state, not parallelized)
        if report_call:
            args = dict(report_call.args) if report_call.args else {}
            self._report_args = args
            response_parts.append(
                types.Part.from_function_response(
                    name="report_finding",
                    response={"result": {"status": "recorded"}},
                ),
            )

        return response_parts
