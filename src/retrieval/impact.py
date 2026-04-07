"""Business impact analyser — assesses organisational impact of a security finding.

Single-responsibility: given a finding, its compliance control mappings, and
an optional CVSS assessment, produces a holistic BusinessImpact via a single
Gemini structured output call.

Runs *after* the mapper/critic stage so it has full mapping context.
"""

import json
import logging

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.retrieval.models import BusinessImpact, ControlMapping
from src.scoring.models import CVSSResult

logger = logging.getLogger("retrieval.impact")

_SYSTEM_PROMPT = """\
You are a senior business risk analyst specialising in information security \
and regulatory compliance.

Given a security finding, its mapped compliance controls, and an optional \
CVSS severity assessment, produce a holistic business impact analysis that \
explains how this issue could affect the organisation if left unaddressed.

ANALYSIS RULES:
- summary: 1-2 concise sentences for an executive audience — state the core \
  business risk clearly.
- financial_risk: potential monetary consequences — regulatory fines, breach \
  notification costs, litigation, revenue loss, remediation spend.  Reference \
  specific regulations/standards where applicable.
- operational_risk: service disruption, downtime, degraded availability, \
  productivity loss, supply-chain impact.
- reputational_risk: customer trust erosion, brand damage, public disclosure \
  consequences, partner confidence.
- regulatory_risk: specific compliance violations, audit failures, \
  certification revocation, legal exposure, reporting obligations.
- impact_severity: classify as exactly one of CRITICAL / HIGH / MEDIUM / LOW \
  based on the combined financial, operational, reputational, and regulatory \
  exposure.  Use CRITICAL only when the organisation faces existential or \
  very large-scale consequences.

GUIDELINES:
- Ground your analysis in the mapped controls and CVSS severity when available.
- Be specific and actionable — avoid generic statements.
- Consider both immediate and downstream consequences.
- If multiple frameworks are mapped, consider the cumulative regulatory exposure.\
"""


class BusinessImpactAnalyzer:
    """Assesses the holistic business impact of a security finding."""

    def __init__(self, settings: AppSettings):
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model

    def _build_prompt(
        self,
        finding: str,
        mappings: list[ControlMapping],
        cvss: CVSSResult | None,
    ) -> str:
        """Build the user prompt with finding + mapping context + CVSS."""
        parts: list[str] = [f"FINDING: {finding}"]

        if mappings:
            lines = ["", "MAPPED CONTROLS:"]
            for m in mappings:
                lines.append(
                    f"- [{m.framework}] {m.control_id} {m.control_title} "
                    f"(domain: {m.domain}, risk mitigated: {m.risk_mitigated}, "
                    f"confidence: {m.confidence_score}%)"
                )
            parts.append("\n".join(lines))

        if cvss:
            parts.append(
                f"\nCVSS ASSESSMENT:\n"
                f"- Score: {cvss.score} ({cvss.severity})\n"
                f"- Vector: {cvss.cvss_vector}\n"
                f"- Potential Impact: {cvss.potential_impact}"
            )

        return "\n".join(parts)

    def analyze(
        self,
        finding: str,
        mappings: list[ControlMapping],
        cvss: CVSSResult | None = None,
    ) -> tuple[BusinessImpact, dict]:
        """Produce a holistic business impact assessment.

        Args:
            finding: The security finding text.
            mappings: All control mappings produced by the mapper/critic.
            cvss: Optional CVSS assessment for severity context.

        Returns:
            Tuple of (BusinessImpact, token usage dict).
        """
        prompt = self._build_prompt(finding, mappings, cvss)

        logger.info(
            "Calling Gemini business impact analyser: %d mappings, cvss=%s",
            len(mappings),
            cvss.severity if cvss else "N/A",
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=BusinessImpact,
                temperature=0.2,
            ),
        )

        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        } if usage else {"prompt_tokens": 0, "total_tokens": 0}

        raw_text = response.text
        try:
            raw_dict = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Impact analyser returned non-JSON: %s", raw_text[:500])
            raise ValueError("Business impact analyser returned invalid JSON")

        impact = BusinessImpact.model_validate(raw_dict)

        logger.info(
            "Business impact assessed: severity=%s (%d tokens)",
            impact.impact_severity,
            tokens["total_tokens"],
        )
        return impact, tokens
