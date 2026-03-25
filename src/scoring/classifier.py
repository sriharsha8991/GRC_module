"""CVSS 3.1 classifier — LLM-based CVSS base metric classification.

Single-responsibility: given a security finding text, produces a
CVSSClassification via a single Gemini structured output call.
Does NOT compute scores — that is the engine's job.

The system prompt embeds the full FIRST CVSS v3.1 spec metric definitions
to ensure the LLM makes calibrated metric decisions.  Only the finding
text is passed (no control mappings) because CVSS metrics are intrinsic
to the vulnerability, not the compliance controls that address it.
"""

import json
import logging

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.scoring.models import CVSSClassification

logger = logging.getLogger("scoring.classifier")

_SYSTEM_PROMPT = """\
You are an expert cybersecurity analyst specialising in CVSS v3.1 scoring.

Given a security finding, reason through each of the 8 base metrics like a \
seasoned penetration tester would.  For every metric, consider the \
vulnerability's nature, typical attack scenarios, and realistic worst-case \
impact before choosing a value.  Think about what an attacker could \
actually achieve — not just what the finding text explicitly states.

METRIC DEFINITIONS (use single-letter values only):

AV  Attack Vector       N=Network  A=Adjacent  L=Local  P=Physical
AC  Attack Complexity   L=Low  H=High
PR  Privileges Required N=None  L=Low  H=High
UI  User Interaction    N=None  R=Required
S   Scope               U=Unchanged  C=Changed
C   Confidentiality     N=None  L=Low  H=High
I   Integrity           N=None  L=Low  H=High
A   Availability        N=None  L=Low  H=High

SCORING APPROACH:
- Analyse the vulnerability class, not just the symptom described.
- Reason about the full range of attack outcomes an expert would consider \
  (data exfiltration, data modification, service disruption, lateral \
  movement, privilege escalation, etc.).
- Apply CVSS 3.1 base metric definitions faithfully per the FIRST spec.
- Base metrics only (no Temporal/Environmental).

OUTPUT RULES:
- description, potential_impact, how_to_remediate: each ONE concise sentence.
- metric_rationale: one short line per metric explaining your reasoning \
  (e.g. "AV:N — the service is exposed over the network").\
"""


class CVSSClassifier:
    """Classifies a security finding into CVSS 3.1 base metrics via Gemini."""

    def __init__(self, settings: AppSettings):
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model

    def classify(
        self,
        finding: str,
    ) -> tuple[CVSSClassification, dict]:
        """Classify a finding into CVSS 3.1 base metrics.

        Args:
            finding: The security finding text to classify.

        Returns:
            Tuple of (CVSSClassification, token usage dict).
        """
        logger.info("Calling Gemini CVSS classifier for finding")

        response = self._client.models.generate_content(
            model=self._model,
            contents=f"FINDING: {finding}",
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=CVSSClassification,
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
            raw_dict = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("CVSS classifier returned non-JSON: %s", raw_text[:500])
            raise ValueError("CVSS classifier returned invalid JSON")

        classification = CVSSClassification.model_validate(raw_dict)

        logger.info(
            "CVSS classified: AV:%s/AC:%s/PR:%s/UI:%s/S:%s/C:%s/I:%s/A:%s (%d tokens)",
            classification.attack_vector,
            classification.attack_complexity,
            classification.privileges_required,
            classification.user_interaction,
            classification.scope,
            classification.confidentiality_impact,
            classification.integrity_impact,
            classification.availability_impact,
            tokens["total_tokens"],
        )

        return classification, tokens
