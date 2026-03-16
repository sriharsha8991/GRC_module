"""PDF → Markdown extraction using PyMuPDF direct with font-based heading detection.

Two-pass approach:
  Pass 1 — scan all pages to discover body font size and heading font sizes.
  Pass 2 — extract text, emitting markdown heading markers (#…) for detected
            heading sizes.  ~40-50× faster than pymupdf4llm.

Produces well-formatted markdown with:
  - Blank lines between paragraphs
  - List items (a), 1), —) on their own lines
  - Bold body-size lines treated as sub-headings
  - NOTEs separated from surrounding text
"""

import logging
import re
from collections import Counter
from pathlib import Path

import pymupdf

logger = logging.getLogger("ingestion.extractor")

# ── Cleanup patterns ────────────────────────────────────
_TOC_DOTS_RE = re.compile(r"\.{4,}")
_COPYRIGHT_RE = re.compile(
    r"^[ivx]*\s*©\s*ISO(?:/IEC)?.*$|^©\s*ISO(?:/IEC)?.*$",
    re.MULTILINE | re.IGNORECASE,
)
_HEADER_FOOTER_RE = re.compile(
    r"^#{0,6}\s*ISO/IEC\s+\d+:\d+\(\w\)\s*$", re.MULTILINE,
)
_STANDALONE_PAGE_NUM_RE = re.compile(r"^\d{1,4}\s*$", re.MULTILINE)
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_HYPHEN_BREAK_RE = re.compile(r"(\w)- (\w)")
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")
_BOLD_FLAG = 1 << 4

# Lines that start a list item or a NOTE
_LIST_MARKER_RE = re.compile(
    r"^(?:[a-z]\)|[0-9]+\)|—|--|NOTE\b)", re.IGNORECASE,
)
# Sub-section numbering at body-bold size (e.g. "6.1.1", "10.2")
_SUBSECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)+$")


def _clean_markdown(md: str) -> str:
    """Remove PDF extraction artifacts from markdown."""
    md = _ZERO_WIDTH_RE.sub("", md)
    md = _HYPHEN_BREAK_RE.sub(r"\1\2", md)
    md = _COPYRIGHT_RE.sub("", md)
    md = _HEADER_FOOTER_RE.sub("", md)
    md = _STANDALONE_PAGE_NUM_RE.sub("", md)
    md = _EXCESS_NEWLINES_RE.sub("\n\n", md)
    return md.strip()


def extract_pdf_to_markdown(pdf_path: Path) -> str:
    """Extract a PDF to clean Markdown with auto-detected heading levels.

    Uses pymupdf directly (C-level text extraction) instead of the much
    slower pymupdf4llm Python layer.
    """
    pdf_str = str(pdf_path)
    doc = pymupdf.open(pdf_str)
    logger.info("Extracting PDF: %s (%d pages)", pdf_path.name, doc.page_count)

    # ── Pass 1: discover font sizes ─────────────────────
    size_chars: Counter[float] = Counter()
    bold_size_chars: Counter[float] = Counter()

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    sz = round(span["size"], 1)
                    size_chars[sz] += len(text)
                    if span["flags"] & _BOLD_FLAG:
                        bold_size_chars[sz] += len(text)

    body_size = size_chars.most_common(1)[0][0]

    # Heading = bold + larger than body, up to 6 levels
    heading_sizes = sorted(
        [s for s in bold_size_chars if s > body_size], reverse=True,
    )[:6]
    size_to_level = {sz: i + 1 for i, sz in enumerate(heading_sizes)}

    # Body-bold text gets the next heading level (for sub-sub-sections)
    body_bold_level = len(heading_sizes) + 1
    if body_bold_level > 6:
        body_bold_level = 6

    logger.info(
        "Font analysis: body=%.1fpt, headings=%s, body-bold→h%d",
        body_size, size_to_level, body_bold_level,
    )

    # ── Pass 2: build markdown ──────────────────────────
    out_lines: list[str] = []
    # Buffer to merge continuation lines within a paragraph
    pending_subsection: str | None = None

    def _flush_buf(buf: list[str]) -> None:
        """Write buffered body lines as a paragraph."""
        if buf:
            out_lines.append(" ".join(buf))
            buf.clear()

    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block["type"] != 0:
                continue

            buf: list[str] = []

            for line in block["lines"]:
                spans = line["spans"]
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                # Skip TOC dot-leader lines
                if _TOC_DOTS_RE.search(text):
                    continue

                sz = round(spans[0]["size"], 1)
                is_bold = bool(spans[0]["flags"] & _BOLD_FLAG)

                # ── Heading (larger-than-body bold) ──
                level = size_to_level.get(sz) if is_bold else None
                if level:
                    _flush_buf(buf)
                    pending_subsection = None
                    out_lines.append(f"\n{'#' * level} {text}")
                    continue

                # ── Body-bold: sub-section number or title ──
                if is_bold and sz == body_size:
                    stripped = text.strip()
                    if _SUBSECTION_NUM_RE.match(stripped):
                        # e.g. "6.1.1" — hold it to merge with next bold title line
                        _flush_buf(buf)
                        pending_subsection = stripped
                        continue
                    if pending_subsection:
                        # Merge number + title: "6.1.1 General"
                        _flush_buf(buf)
                        heading = f"{pending_subsection} {stripped}"
                        pending_subsection = None
                        out_lines.append(f"\n{'#' * body_bold_level} {heading}")
                        continue
                    # Standalone bold body line (e.g. "Control")
                    _flush_buf(buf)
                    out_lines.append(f"\n**{stripped}**")
                    continue

                # Clear pending subsection if the next line isn't bold
                if pending_subsection:
                    _flush_buf(buf)
                    out_lines.append(
                        f"\n{'#' * body_bold_level} {pending_subsection}"
                    )
                    pending_subsection = None

                # ── List items / NOTEs → own line with blank line before ──
                if _LIST_MARKER_RE.match(text):
                    _flush_buf(buf)
                    out_lines.append(text)
                    continue

                # ── Regular body text → buffer for paragraph merging ──
                buf.append(text)

            _flush_buf(buf)
            # Paragraph break between blocks
            out_lines.append("")

    doc.close()

    raw_md = "\n".join(out_lines)
    logger.info("Raw extraction: %d chars", len(raw_md))

    clean_md = _clean_markdown(raw_md)
    logger.info(
        "After cleanup: %d chars (removed %d chars of noise)",
        len(clean_md), len(raw_md) - len(clean_md),
    )
    return clean_md
