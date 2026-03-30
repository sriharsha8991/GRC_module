"""CVE evaluator — LLM judge that validates CVE-to-finding matches.

Single-responsibility: given a security finding and a list of candidate
CVEs with their details, uses Gemini structured output to evaluate
whether each CVE is a correct match.

Short-circuit rules:
  - EXPLICIT CVE IDs (from finding text) → auto-approved, skip LLM
  - API-discovered CVEs (NVD/OSV) → require LLM evaluation
"""

import json
import logging

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.scoring.models import (
    CveEvaluation,
    CveEvaluationResult,
    CveSearchResult,
)

logger = logging.getLogger("scoring.cve_evaluator")

# Sources that skip LLM evaluation (deterministic paths)
_AUTO_APPROVE_SOURCES = {"EXPLICIT"}

_SYSTEM_PROMPT = """\
You are a cybersecurity analyst acting as a CVE mapping expert.

TASK: Given a security finding and a list of candidate CVEs, evaluate \
whether each CVE is a correct match for the specific finding described.

EVALUATION CRITERIA:
1. PRODUCT MATCH — Does the CVE affect the same software component \
   mentioned in the finding? Reject if a different product.
2. VERSION MATCH — Does the finding's version fall within the CVE's \
   affected version range? Reject if version clearly outside range.
3. VULNERABILITY TYPE — Is the vulnerability type consistent? \
   e.g. if finding mentions SSRF, the CVE should be an SSRF or \
   closely related flaw, not a completely different bug class.

SCORING GUIDE (relevance_score 0-100):
  90-100: Exact product + version in affected range + same vuln type
  70-89:  Exact product + version likely in range (ambiguous range)
  50-69:  Same product, version unclear, vuln type consistent
  30-49:  Same product but version appears outside affected range
  0-29:   Different product or completely unrelated vulnerability

Set is_relevant=true only if relevance_score >= 50.

For each CVE, provide a one-line reasoning explaining your decision.\
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
        candidates: list[CveSearchResult],
    ) -> tuple[CveEvaluationResult, dict]:
        """Evaluate candidate CVEs against the finding.

        Auto-approves deterministic sources (EXPLICIT)
        and uses LLM only for API-discovered CVEs.

        Args:
            finding_text: The original security finding text.
            candidates: CVE candidates from search step.

        Returns:
            (CveEvaluationResult, {"prompt_tokens": int, "total_tokens": int})
        """
        auto_approved: list[CveEvaluation] = []
        needs_llm: list[CveSearchResult] = []

        for c in candidates:
            if c.source in _AUTO_APPROVE_SOURCES:
                auto_approved.append(CveEvaluation(
                    cve_id=c.cve_id,
                    is_relevant=True,
                    relevance_score=100,
                    reasoning=f"Auto-approved: {c.source} source (deterministic)",
                ))
            else:
                needs_llm.append(c)

        tokens = {"prompt_tokens": 0, "total_tokens": 0}
        llm_evaluations: list[CveEvaluation] = []

        if needs_llm:
            llm_evaluations, tokens = self._evaluate_via_llm(
                finding_text, needs_llm,
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

    def _evaluate_via_llm(
        self,
        finding_text: str,
        candidates: list[CveSearchResult],
    ) -> tuple[list[CveEvaluation], dict]:
        """Run LLM evaluation on API-discovered CVE candidates."""
        # Build structured input for the LLM
        cve_descriptions = []
        for c in candidates:
            entry = {
                "cve_id": c.cve_id,
                "description": c.description,
                "affected_product": c.affected_product,
                "affected_versions": c.affected_versions,
            }
            cve_descriptions.append(entry)

        prompt = (
            f"FINDING:\n{finding_text}\n\n"
            f"CANDIDATE CVEs:\n{json.dumps(cve_descriptions, indent=2)}\n\n"
            f"Evaluate each CVE and return a list of evaluations."
        )

        # Use list schema for structured output
        response_schema = list[CveEvaluation]

        logger.info("Calling Gemini CVE evaluator for %d candidates", len(candidates))

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=response_schema,
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
            logger.error("CVE evaluator returned non-JSON: %s", raw_text[:500])
            # On parse failure, reject all candidates (safe default)
            return [
                CveEvaluation(
                    cve_id=c.cve_id,
                    is_relevant=False,
                    relevance_score=0,
                    reasoning="LLM evaluation parse error — rejected as safety fallback",
                )
                for c in candidates
            ], tokens

        evaluations: list[CveEvaluation] = []
        if isinstance(raw_list, list):
            for item in raw_list:
                try:
                    evaluations.append(CveEvaluation.model_validate(item))
                except Exception:
                    logger.warning("Failed to parse evaluation item: %s", item)

        logger.info(
            "LLM evaluated %d CVEs (%d tokens)",
            len(evaluations), tokens["total_tokens"],
        )

        return evaluations, tokens
