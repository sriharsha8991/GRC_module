"""Compliance mapper — maps a security finding to framework controls via Gemini.

Single-responsibility: given a finding and retrieved evidence chunks, produces
structured ControlMapping objects using a single Gemini LLM call.
Does NOT search, embed, or validate — those are separate concerns.
"""

import json
import logging

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.retrieval.models import ControlMapping, ScoredChunk

logger = logging.getLogger("retrieval.mapper")

_SYSTEM_PROMPT = """\
You are a GRC compliance analyst. Map the security finding to specific
framework controls using ONLY the provided evidence.

Rules:
- One mapping per distinct control.
- citation MUST be a verbatim excerpt from the evidence — never paraphrase.
- citation_source must match the source path provided with the evidence chunk.
- Do not fabricate mappings unsupported by evidence.
- confidence_score (0-100) reflects how directly the control addresses the finding.\
"""


class ComplianceMapper:
    """Maps findings to framework controls via Gemini structured output."""

    def __init__(self, settings: AppSettings):
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model

    def _build_evidence_prompt(
        self,
        finding: str,
        framework_chunks: dict[str, list[ScoredChunk]],
    ) -> str:
        """Build the user prompt with finding + grouped evidence chunks."""
        sections: list[str] = []

        for fw_key, chunks in framework_chunks.items():
            if not chunks:
                continue
            lines = [f"## {fw_key}"]
            for i, chunk in enumerate(chunks, 1):
                lines.append(f"[{i}|{chunk.citation_source}]")
                lines.append(chunk.text)
            sections.append("\n".join(lines))

        evidence_block = "\n\n".join(sections)

        return (
            f"FINDING: {finding}\n\n"
            f"EVIDENCE\n\n{evidence_block}"
        )

    def map_finding(
        self,
        finding: str,
        framework_chunks: dict[str, list[ScoredChunk]],
    ) -> tuple[list[ControlMapping], dict]:
        """Map a finding to controls using a single Gemini call.

        Args:
            finding: The security finding text.
            framework_chunks: Retrieved evidence grouped by framework key.

        Returns:
            Tuple of (list of ControlMapping, token usage dict).
        """
        total_chunks = sum(len(v) for v in framework_chunks.values())
        if total_chunks == 0:
            logger.warning("No evidence chunks — skipping mapper")
            return [], {"prompt_tokens": 0, "response_tokens": 0, "total_tokens": 0}

        prompt = self._build_evidence_prompt(finding, framework_chunks)

        logger.info(
            "Calling Gemini mapper: %d frameworks, %d evidence chunks",
            len(framework_chunks), total_chunks,
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=list[ControlMapping],
                temperature=0.1,
            ),
        )

        # Extract token usage
        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count or 0,
            # "response_tokens": usage.response_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        } if usage else {"prompt_tokens": 0, "total_tokens": 0}

        raw_text = response.text
        try:
            raw_list = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Gemini returned non-JSON: %s", raw_text[:500])
            return [], tokens

        mappings = [ControlMapping.model_validate(item) for item in raw_list]

        logger.info("Mapper produced %d control mappings (%d tokens)",
                    len(mappings), tokens["total_tokens"])
        return mappings, tokens
