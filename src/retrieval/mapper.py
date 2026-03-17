"""Compliance mapper — maps a security finding to framework controls via Gemini.

Single-responsibility: given a finding and reranked evidence chunks, produces
structured ControlMapping objects using a single Gemini LLM call.
Does NOT search, embed, or validate — those are separate concerns.
"""

import json
import logging

from google import genai
from google.genai import types

from src.config.settings import IngestionSettings
from src.retrieval.models import ControlMapping, RankedChunk

logger = logging.getLogger("retrieval.mapper")

_SYSTEM_PROMPT = """\
You are an expert GRC (Governance, Risk, Compliance) analyst. Your task is to
map a security finding to specific controls from compliance frameworks.

You will receive:
1. A security FINDING (an observation, vulnerability, or audit issue).
2. EVIDENCE chunks from one or more compliance frameworks, each with its
   source document name and heading path.

For each relevant control you identify in the evidence, produce a mapping with:
- framework: The framework display name (e.g. "ISO/IEC 27001:2022")
- framework_version: The version string
- control_id: The specific control identifier (e.g. "A.8.20", "1.2.1")
- control_title: The control's title
- domain: The domain or category within the framework
- risk_mitigated: What risk this control addresses for the given finding
- citation: An EXACT quote from the evidence text that supports this mapping
- citation_source: The full source path as provided (e.g. "ISO/IEC 27001:2022, 8 Operation > A.8.20")
- confidence_score: Your confidence in this mapping (0-100)

Rules:
- ONLY map to controls that are directly supported by the provided evidence.
- The citation MUST be a verbatim excerpt from the evidence — do not paraphrase.
- If no evidence supports a mapping, do not fabricate one.
- Produce one mapping per distinct control; do not duplicate.
- confidence_score reflects how directly the control addresses the finding.\
"""


class ComplianceMapper:
    """Maps findings to framework controls via Gemini structured output."""

    def __init__(self, settings: IngestionSettings):
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_parse_model

    def _build_evidence_prompt(
        self,
        finding: str,
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> str:
        """Build the user prompt with finding + grouped evidence chunks."""
        sections: list[str] = []

        for fw_key, chunks in framework_chunks.items():
            if not chunks:
                continue
            lines = [f"## Framework: {chunks[0].source_document} ({fw_key})"]
            for i, chunk in enumerate(chunks, 1):
                lines.append(
                    f"\n### Evidence {i} "
                    f"[source: {chunk.citation_source}] "
                    f"(rerank_score={chunk.rerank_score:.3f})"
                )
                lines.append(chunk.text)
            sections.append("\n".join(lines))

        evidence_block = "\n\n---\n\n".join(sections)

        return (
            f"# FINDING\n\n{finding}\n\n"
            f"# EVIDENCE FROM COMPLIANCE FRAMEWORKS\n\n{evidence_block}"
        )

    def map_finding(
        self,
        finding: str,
        framework_chunks: dict[str, list[RankedChunk]],
    ) -> list[ControlMapping]:
        """Map a finding to controls using a single Gemini call.

        Args:
            finding: The security finding text.
            framework_chunks: Reranked evidence grouped by framework key.

        Returns:
            List of ControlMapping objects.
        """
        total_chunks = sum(len(v) for v in framework_chunks.values())
        if total_chunks == 0:
            logger.warning("No evidence chunks — skipping mapper")
            return []

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

        raw_text = response.text
        try:
            raw_list = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Gemini returned non-JSON: %s", raw_text[:500])
            return []

        mappings = [ControlMapping.model_validate(item) for item in raw_list]

        logger.info("Mapper produced %d control mappings", len(mappings))
        return mappings
