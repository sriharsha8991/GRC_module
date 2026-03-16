"""Two-stage markdown chunker with heading-aware splitting.

Stage 1: MarkdownHeaderTextSplitter — splits at heading boundaries and
         captures the heading text as metadata.
Stage 2: RecursiveCharacterTextSplitter — sub-splits any chunks that
         exceed the token limit, preserving the heading metadata.

Every chunk carries its parent heading so the reranker and LLM can
identify which clause/section the text belongs to.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from src.config.settings import IngestionSettings

logger = logging.getLogger("ingestion.chunker")

# Heading levels to split on — PyMuPDF4LLM typically produces ## for all
HEADERS_TO_SPLIT_ON = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
]

_SUB_SEPARATORS = ["\n\n", "\n", " "]

# Patterns that should be preceded by a blank line for readability
_LIST_ITEM_RE = re.compile(r"^(?:[a-z]\)|[0-9]+\)|—|--|NOTE\b)", re.IGNORECASE | re.MULTILINE)
_BOLD_LINE_RE = re.compile(r"^\*\*.*\*\*$", re.MULTILINE)
_BOLD_PAGE_NUM_RE = re.compile(r"^\*\*\d{1,4}\*\*$")
# Orphaned list marker: just "c)" or "f)" alone on a line
_ORPHAN_MARKER_RE = re.compile(r"^[a-z]\)$|^[0-9]+\)$")


@dataclass
class Chunk:
    """A single chunk ready for embedding and Qdrant upsert."""
    point_id: str
    text: str
    metadata: dict = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.33)


def _make_point_id(framework: str, chunk_index: int) -> str:
    key = f"{framework}_{chunk_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def _heading_label(headings: dict[str, str]) -> str:
    """Build a breadcrumb from heading metadata.

    Example: "7 Support > 7.2 Competence"
    """
    parts = []
    for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
        val = headings.get(level, "").strip().strip("*")
        if val:
            parts.append(val)
    return " > ".join(parts)


def _format_chunk_text(text: str) -> str:
    """Ensure blank lines before list items, bold labels, and NOTEs for readability.

    Also merges orphaned list markers (e.g. a lone 'c)' line) with the
    continuation on the next line, and strips bold page numbers.
    """
    lines = text.split("\n")

    # Pass 1: merge orphaned list markers with the next non-empty line
    merged: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if _ORPHAN_MARKER_RE.match(stripped):
            # Find the next non-empty line to merge with
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                merged.append(f"{stripped} {lines[j].strip()}")
                i = j + 1
                continue
        merged.append(lines[i])
        i += 1

    # Pass 2: add blank lines for readability, drop bold page numbers
    formatted: list[str] = []
    for line in merged:
        stripped = line.strip()
        if not stripped:
            if formatted and formatted[-1] != "":
                formatted.append("")
            continue

        # Drop bold standalone page numbers like **2**, **15**
        if _BOLD_PAGE_NUM_RE.match(stripped):
            continue

        needs_blank = False
        if formatted and formatted[-1] != "":
            if _LIST_ITEM_RE.match(stripped) or _BOLD_LINE_RE.match(stripped):
                needs_blank = True

        if needs_blank:
            formatted.append("")
        formatted.append(stripped)

    return "\n".join(formatted).strip()


def chunk_markdown(
    markdown: str,
    framework_key: str,
    framework_version: str,
    settings: IngestionSettings,
) -> list[Chunk]:
    """Split framework markdown into embedding-ready chunks.

    Two-stage split:
    1. MarkdownHeaderTextSplitter — heading-aware, captures heading text
    2. RecursiveCharacterTextSplitter — sub-splits oversized chunks

    Each chunk's text is prefixed with the heading breadcrumb for richer
    embeddings (e.g., "6.2 Information security objectives > ...").
    """
    # Stage 1: split at heading boundaries, strip headers into metadata
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS_TO_SPLIT_ON,
        strip_headers=True,
    )
    header_docs = header_splitter.split_text(markdown)

    # Stage 2: sub-split oversized chunks
    sub_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=_SUB_SEPARATORS,
        length_function=_estimate_tokens,
    )

    chunks: list[Chunk] = []
    idx = 0

    for doc in header_docs:
        headings = doc.metadata
        content = doc.page_content.strip()
        if not content:
            continue

        label = _heading_label(headings)
        base_meta = {
            "framework": framework_key,
            "framework_version": framework_version,
            **headings,
        }

        if _estimate_tokens(content) <= settings.chunk_size:
            raw = f"{label}\n\n{content}" if label else content
            chunks.append(Chunk(
                point_id=_make_point_id(framework_key, idx),
                text=_format_chunk_text(raw),
                metadata={**base_meta, "chunk_index": idx},
            ))
            idx += 1
        else:
            sub_splits = sub_splitter.split_text(content)
            for sub_text in sub_splits:
                raw = f"{label}\n\n{sub_text}" if label else sub_text
                chunks.append(Chunk(
                    point_id=_make_point_id(framework_key, idx),
                    text=_format_chunk_text(raw),
                    metadata={**base_meta, "chunk_index": idx},
                ))
                idx += 1

    logger.info(
        "Chunked markdown → %d chunks (framework=%s, heading_sections=%d, avg_tokens=%d)",
        len(chunks),
        framework_key,
        len(header_docs),
        sum(_estimate_tokens(c.text) for c in chunks) // max(len(chunks), 1),
    )
    return chunks
