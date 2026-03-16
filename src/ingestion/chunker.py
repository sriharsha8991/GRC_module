"""Document-native chunker with markdown-aware splitting for large controls.

Small controls (≤ chunk_size tokens) stay as a single chunk.
Large controls are split using RecursiveCharacterTextSplitter with
markdown heading separators to preserve semantic coherence.
"""

import logging
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config.settings import IngestionSettings
from src.ingestion.parser import ParsedControl

logger = logging.getLogger("ingestion.chunker")

# Markdown-aware separators — prefer splitting at heading boundaries
MARKDOWN_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", " "]


@dataclass
class Chunk:
    """A single chunk ready for embedding and Qdrant upsert."""
    chunk_id: str  # "{framework_key}_{control_id}_{chunk_index}"
    text: str
    metadata: dict = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~0.75 tokens per whitespace-delimited word.
    Good enough for chunking decisions; exact count only matters at embedding time.
    """
    return int(len(text.split()) * 1.33)


def chunk_controls(
    controls: list[ParsedControl],
    framework_key: str,
    settings: IngestionSettings,
) -> list[Chunk]:
    """Chunk a list of parsed controls into embedding-ready chunks.

    Args:
        controls: Parsed controls from the structure parser.
        framework_key: e.g. "iso27001", "pci_dss_v4".
        settings: Ingestion settings (chunk_size, chunk_overlap).

    Returns:
        List of Chunk objects with metadata attached.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=MARKDOWN_SEPARATORS,
        length_function=_estimate_tokens,
    )

    chunks: list[Chunk] = []

    for control in controls:
        base_metadata = {
            "framework": control.framework,
            "framework_version": control.framework_version,
            "framework_category": control.framework_category,
            "domain": control.domain,
            "control_id": control.control_id,
            "title": control.title,
        }

        token_est = _estimate_tokens(control.text)

        if token_est <= settings.chunk_size:
            # Single chunk — fits within limit
            chunk_id = f"{framework_key}_{control.control_id}_0"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=control.text,
                metadata={**base_metadata, "chunk_index": 0, "total_chunks": 1},
            ))
        else:
            # Split large control
            splits = splitter.split_text(control.text)
            total = len(splits)
            for idx, split_text in enumerate(splits):
                chunk_id = f"{framework_key}_{control.control_id}_{idx}"
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=split_text,
                    metadata={**base_metadata, "chunk_index": idx, "total_chunks": total},
                ))

            logger.debug(
                "Control %s split into %d chunks (%d est. tokens)",
                control.control_id, total, token_est,
            )

    logger.info(
        "Chunked %d controls → %d chunks (framework=%s)",
        len(controls), len(chunks), framework_key,
    )
    return chunks
