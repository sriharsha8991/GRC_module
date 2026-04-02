"""CVE evaluator — LLM judge that validates CVE-to-finding matches.

Single-responsibility: given a security finding and candidate CVEs with
their full details (description, CVSS, CWE, affected versions), uses
Gemini structured output to evaluate whether each CVE is a correct match.

Details are fetched BEFORE evaluation so the LLM has full context.
Evaluation runs in parallel batches of 5 for throughput.

Short-circuit rules:
  - EXPLICIT CVE IDs (from finding text) → auto-approved, skip LLM
  - API-discovered CVEs (NVD/OSV) → require LLM evaluation
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.scoring.models import (
    CveDetail,
    CveEvaluation,
    CveEvaluationResult,
    CveSearchResult,
)

logger = logging.getLogger("scoring.cve_evaluator")

# Sources that skip LLM evaluation (deterministic paths)
_AUTO_APPROVE_SOURCES = {"EXPLICIT"}

# Batch size for parallel LLM evaluation
_BATCH_SIZE = 5

_SYSTEM_PROMPT = """\
You are a strict CVE evaluation judge. ONLY approve CVEs that have a \
DIRECT, verifiable relationship to the finding. Reject indirect, \
tangential, or "same product family" matches.

EVALUATION STEPS (apply strictly in order — fail at any step = reject):

1. PRODUCT MATCH — The CVE must affect the EXACT same software component \
   named in the finding. A different module, library, or sub-project from \
   the same vendor is NOT a match. Reject (0-29) immediately.

2. VERSION MATCH — The finding's specific version MUST fall within the \
   CVE's affected version range. Check bounds precisely:
   - version >= versionStartIncluding AND version < versionEndExcluding
   - If the CVE has no version range data, score 40-49 (uncertain, reject).
   - If the version is clearly outside the range, score 30-39 (reject).

3. VULNERABILITY TYPE — Read the CVE description carefully. The flaw \
   described must be the SAME type or directly related to what the \
   finding describes. An RCE CVE does NOT match an XSS finding even \
   if same product+version.

SCORING (0-100):
90-100: Exact product + version confirmed in affected range + same vuln type.
70-89: Exact product + version likely in range + consistent vuln type.
50-69: DO NOT USE unless product matches exactly and only version bounds \
       are ambiguous. Never use for indirect or tangential matches.
30-49: Same product but version outside range OR no version data. REJECT.
0-29: Different product, different vuln type, or unrelated. REJECT.

Set is_relevant=true ONLY if score >= 70.

REASONING FORMAT (required for each CVE):
Product: [match/mismatch] — [CVE product] vs [finding product]
Version: [in range/out of range/unclear] — [finding version] vs [CVE range]
Vuln type: [match/mismatch] — [CVE type] vs [finding type]
Verdict: [approved/rejected] — [score]\
"""


class CveEvaluator:
    """LLM-based judge for validating CVE-to-finding matches."""

    def __init__(self, settings: AppSettings) -> None:
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model
        self._threshold = settings.cve.llm_evaluation_threshold

    def evaluate(
        self,
        finding_text: str,
        candidate_pairs: list[tuple[CveSearchResult, CveDetail | None]],
        software_component: str | None = None,
        vendor: str | None = None,
        version: str | None = None,
    ) -> tuple[CveEvaluationResult, dict]:
        """Evaluate candidate CVEs against the finding.

        Args:
            finding_text: The original security finding text.
            candidate_pairs: List of (CveSearchResult, CveDetail | None).
            software_component: Identified software name from agent.
            vendor: Identified vendor from agent.
            version: Identified version from agent.

        Returns:
            (CveEvaluationResult, {"prompt_tokens": int, "total_tokens": int})
        """
        auto_approved: list[CveEvaluation] = []
        needs_llm: list[tuple[CveSearchResult, CveDetail | None]] = []

        for search_result, detail in candidate_pairs:
            if search_result.source in _AUTO_APPROVE_SOURCES:
                auto_approved.append(CveEvaluation(
                    cve_id=search_result.cve_id,
                    is_relevant=True,
                    relevance_score=100,
                    reasoning=f"Auto-approved: {search_result.source} source (deterministic)",
                ))
            else:
                needs_llm.append((search_result, detail))

        tokens = {"prompt_tokens": 0, "total_tokens": 0}
        llm_evaluations: list[CveEvaluation] = []

        if needs_llm:
            llm_evaluations, tokens = self._evaluate_batched(
                finding_text, needs_llm,
                software_component, vendor, version,
            )

        all_evaluations = auto_approved + llm_evaluations
        final_cve_ids = [
            e.cve_id for e in all_evaluations
            if e.is_relevant and e.relevance_score >= self._threshold
        ]

        logger.info(
            "Evaluation: %d auto-approved, %d LLM-evaluated, %d final CVEs",
            len(auto_approved), len(llm_evaluations), len(final_cve_ids),
        )

        return CveEvaluationResult(
            evaluations=all_evaluations,
            final_cve_ids=final_cve_ids,
        ), tokens

    def _evaluate_batched(
        self,
        finding_text: str,
        pairs: list[tuple[CveSearchResult, CveDetail | None]],
        software_component: str | None = None,
        vendor: str | None = None,
        version: str | None = None,
    ) -> tuple[list[CveEvaluation], dict]:
        """Evaluate in parallel batches of _BATCH_SIZE."""
        batches = [
            pairs[i : i + _BATCH_SIZE]
            for i in range(0, len(pairs), _BATCH_SIZE)
        ]

        logger.info(
            "Evaluating %d CVEs in %d batch(es) of up to %d",
            len(pairs), len(batches), _BATCH_SIZE,
        )

        all_evaluations: list[CveEvaluation] = []
        total_prompt = 0
        total_tokens = 0

        # Run batches in parallel using threads (Gemini client is sync)
        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            futures = [
                executor.submit(
                    self._evaluate_batch,
                    finding_text, batch,
                    software_component, vendor, version,
                    batch_idx + 1,
                )
                for batch_idx, batch in enumerate(batches)
            ]
            for future in futures:
                try:
                    evals, toks = future.result()
                    all_evaluations.extend(evals)
                    total_prompt += toks["prompt_tokens"]
                    total_tokens += toks["total_tokens"]
                except Exception:
                    logger.exception("Evaluation batch failed")

        return all_evaluations, {
            "prompt_tokens": total_prompt,
            "total_tokens": total_tokens,
        }

    def _evaluate_batch(
        self,
        finding_text: str,
        batch: list[tuple[CveSearchResult, CveDetail | None]],
        software_component: str | None,
        vendor: str | None,
        version: str | None,
        batch_num: int,
    ) -> tuple[list[CveEvaluation], dict]:
        """Evaluate a single batch of CVE candidates."""
        # Build rich CVE context from details
        cve_entries = []
        for search_result, detail in batch:
            entry: dict = {"cve_id": search_result.cve_id}

            if detail:
                # Full detail available — give the LLM everything
                entry["description"] = detail.description
                if detail.cvss_score is not None:
                    entry["cvss_score"] = detail.cvss_score
                    entry["cvss_severity"] = detail.cvss_severity
                if detail.cwe_id:
                    entry["cwe_id"] = detail.cwe_id
                if detail.published:
                    entry["published"] = detail.published
                if detail.kev:
                    entry["kev"] = True
            else:
                # Fallback to search result data
                if search_result.description:
                    entry["description"] = search_result.description

            # Always include version range from search result
            if search_result.affected_product:
                entry["affected_product"] = search_result.affected_product
            if search_result.affected_versions:
                entry["affected_versions"] = search_result.affected_versions
            entry["source"] = search_result.source

            cve_entries.append(entry)

        # Build structured finding context
        context_lines = [f"FINDING TEXT:\n{finding_text}"]
        if software_component or vendor or version:
            context_lines.append("\nIDENTIFIED TARGET:")
            if software_component:
                context_lines.append(f"  Software: {software_component}")
            if vendor:
                context_lines.append(f"  Vendor: {vendor}")
            if version:
                context_lines.append(
                    f"  Version: {version} (CVEs must affect THIS version)"
                )

        prompt = (
            "\n".join(context_lines)
            + f"\n\nCANDIDATE CVEs ({len(cve_entries)}):\n"
            + json.dumps(cve_entries, indent=2)
            + "\n\nFor each CVE:"
            "\n1. Read its description to understand the exact vulnerability."
            "\n2. Check if affected_versions range includes the target version."
            "\n3. Verify the vulnerability TYPE in the description matches the finding."
            "\nReturn evaluations."
        )

        logger.info(
            "Batch %d: evaluating %d CVEs", batch_num, len(batch),
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=list[CveEvaluation],
                temperature=0.1,
            ),
        )

        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        } if usage else {"prompt_tokens": 0, "total_tokens": 0}

        raw_text = response.text
        try:
            raw_list = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error(
                "Batch %d: non-JSON response: %s", batch_num, raw_text[:500],
            )
            return [
                CveEvaluation(
                    cve_id=sr.cve_id,
                    is_relevant=False,
                    relevance_score=0,
                    reasoning="LLM evaluation parse error — rejected as safety fallback",
                )
                for sr, _ in batch
            ], tokens

        evaluations: list[CveEvaluation] = []
        if isinstance(raw_list, list):
            for item in raw_list:
                try:
                    evaluations.append(CveEvaluation.model_validate(item))
                except Exception:
                    logger.warning(
                        "Batch %d: failed to parse item: %s", batch_num, item,
                    )

        logger.info(
            "Batch %d: evaluated %d CVEs (%d tokens)",
            batch_num, len(evaluations), tokens["total_tokens"],
        )

        return evaluations, tokens
