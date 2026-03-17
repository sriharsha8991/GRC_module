"""Adversarial critic — validates compliance mappings against source evidence.

Single-responsibility: given mappings and the evidence chunks they were derived
from, verifies citation grounding, logical consistency, and confidence
calibration.  Stamps each mapping APPROVED or FAILED.
"""

import json
import logging

from google import genai
from google.genai import types

from src.config.settings import IngestionSettings
from src.retrieval.models import ControlMapping, MappingStatus, RankedChunk

logger = logging.getLogger("retrieval.critic")

_CRITIC_SYSTEM = """\
You are an adversarial reviewer for GRC compliance mappings. For each mapping,
you must verify three things:

1. **Citation Grounding** — Does the `citation` text actually appear (verbatim
   or very close) in the provided evidence chunks for that framework? If the
   citation is fabricated or heavily paraphrased, FAIL it.

2. **Logical Consistency** — Does the cited control logically address the
   security finding? A mapping to an unrelated control should be FAILED.

3. **Confidence Calibration** — Is the confidence_score reasonable given the
   strength of evidence? A score of 90+ with only tangential evidence should
   be FAILED.

For each mapping, respond with:
- index: the 0-based position of the mapping in the input list
- is_valid: true if all three checks pass, false otherwise
- reason: brief explanation (required if is_valid is false, optional if true)

Return a JSON array of objects with these three fields.\
"""


class CriticVerdict(types.TypedDict):
    index: int
    is_valid: bool
    reason: str


class AdversarialCritic:
    """Validates compliance mappings against source evidence via Gemini."""

    def __init__(self, settings: IngestionSettings):
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_parse_model

    def _build_critic_prompt(
        self,
        finding: str,
        mappings: list[ControlMapping],
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> str:
        """Build the critic prompt with mappings + evidence for verification."""
        sections: list[str] = []

        # Include the original finding
        sections.append(f"# FINDING\n\n{finding}")

        # Include mappings to validate
        mapping_lines = ["# MAPPINGS TO VALIDATE\n"]
        for i, m in enumerate(mappings):
            mapping_lines.append(
                f"[{i}] framework={m.framework}, control_id={m.control_id}, "
                f"control_title={m.control_title}, confidence={m.confidence_score}\n"
                f"    citation: \"{m.citation}\"\n"
                f"    citation_source: {m.citation_source}\n"
                f"    risk_mitigated: {m.risk_mitigated}"
            )
        sections.append("\n".join(mapping_lines))

        # Include evidence chunks grouped by framework
        evidence_lines = ["# SOURCE EVIDENCE\n"]
        for fw_key, chunks in framework_chunks.items():
            if not chunks:
                continue
            evidence_lines.append(f"## {chunks[0].source_document} ({fw_key})")
            for j, chunk in enumerate(chunks, 1):
                evidence_lines.append(
                    f"\n[Evidence {j}] source: {chunk.citation_source}"
                )
                evidence_lines.append(chunk.text)
        sections.append("\n".join(evidence_lines))

        return "\n\n---\n\n".join(sections)

    def validate(
        self,
        finding: str,
        mappings: list[ControlMapping],
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> list[ControlMapping]:
        """Validate each mapping and stamp APPROVED / FAILED.

        Args:
            finding: The original security finding.
            mappings: Mappings produced by the compliance mapper.
            framework_chunks: The evidence chunks used to produce the mappings.

        Returns:
            Updated mappings with status and critic_reason fields set.
        """
        if not mappings:
            return []

        prompt = self._build_critic_prompt(finding, mappings, framework_chunks)

        logger.info("Calling Gemini critic for %d mappings", len(mappings))

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_CRITIC_SYSTEM,
                response_mime_type="application/json",
                response_schema=list[CriticVerdict],
                temperature=0.0,
            ),
        )

        raw_text = response.text
        try:
            verdicts = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Critic returned non-JSON: %s", raw_text[:500])
            return mappings

        # Build a lookup: index → verdict
        verdict_map: dict[int, dict] = {v["index"]: v for v in verdicts}

        updated: list[ControlMapping] = []
        approved, failed = 0, 0

        for i, mapping in enumerate(mappings):
            verdict = verdict_map.get(i)
            if verdict and not verdict["is_valid"]:
                mapping = mapping.model_copy(update={
                    "status": MappingStatus.FAILED,
                    "critic_reason": verdict.get("reason", "Failed validation"),
                })
                failed += 1
            else:
                mapping = mapping.model_copy(update={
                    "status": MappingStatus.APPROVED,
                })
                approved += 1
            updated.append(mapping)

        logger.info(
            "Critic verdict: %d approved, %d failed out of %d",
            approved, failed, len(mappings),
        )
        return updated
