"""Adversarial critic — validates compliance mappings against source evidence.

Single-responsibility: given mappings and the evidence chunks they were derived
from, verifies citation grounding, logical consistency, and confidence
calibration.  Stamps each mapping APPROVED or FAILED.
"""

import json
import logging

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.retrieval.models import ControlMapping, MappingStatus, RankedChunk

logger = logging.getLogger("retrieval.critic")

_CRITIC_SYSTEM = """\
You are an adversarial reviewer for GRC compliance mappings. For each mapping verify:
1. Citation Grounding — citation text appears verbatim (or very close) in the evidence.
2. Logical Consistency — the cited control logically addresses the finding.
3. Confidence Calibration — score is reasonable given evidence strength.

FAIL any mapping that does not pass all three checks.
Return a JSON array of {index, is_valid, reason} per mapping.\
"""


class CriticVerdict(types.TypedDict):
    index: int
    is_valid: bool
    reason: str


class AdversarialCritic:
    """Validates compliance mappings against source evidence via Gemini."""

    def __init__(self, settings: AppSettings):
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model

    def _build_critic_prompt(
        self,
        finding: str,
        mappings: list[ControlMapping],
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> str:
        """Build the critic prompt with mappings + evidence for verification."""
        sections: list[str] = []

        sections.append(f"FINDING: {finding}")

        mapping_lines = ["MAPPINGS"]
        for i, m in enumerate(mappings):
            mapping_lines.append(
                f"[{i}] {m.control_id} \"{m.control_title}\" confidence={m.confidence_score} "
                f"citation=\"{m.citation}\" source={m.citation_source}"
            )
        sections.append("\n".join(mapping_lines))

        evidence_lines = ["EVIDENCE"]
        for fw_key, chunks in framework_chunks.items():
            if not chunks:
                continue
            for j, chunk in enumerate(chunks, 1):
                evidence_lines.append(f"[{j}|{chunk.citation_source}]")
                evidence_lines.append(chunk.text)
        sections.append("\n".join(evidence_lines))

        return "\n\n".join(sections)

    def validate(
        self,
        finding: str,
        mappings: list[ControlMapping],
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> tuple[list[ControlMapping], dict]:
        """Validate each mapping and stamp APPROVED / FAILED.

        Args:
            finding: The original security finding.
            mappings: Mappings produced by the compliance mapper.
            framework_chunks: The evidence chunks used to produce the mappings.

        Returns:
            Tuple of (updated mappings, token usage dict).
        """
        _zero_tokens = {"prompt_tokens": 0, "total_tokens": 0}
        if not mappings:
            return [], _zero_tokens

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

        # Extract token usage
        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count or 0,
            # "response_tokens": usage.response_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        } if usage else _zero_tokens

        raw_text = response.text
        try:
            verdicts = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Critic returned non-JSON: %s", raw_text[:500])
            return mappings, tokens

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
            "Critic verdict: %d approved, %d failed out of %d (%d tokens)",
            approved, failed, len(mappings), tokens["total_tokens"],
        )
        return updated, tokens
