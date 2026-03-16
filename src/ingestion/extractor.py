"""PDF → Markdown extraction using PyMuPDF4LLM."""

import logging
from pathlib import Path

import pymupdf4llm

logger = logging.getLogger("ingestion.extractor")


def extract_pdf_to_markdown(pdf_path: Path) -> str:
    """Extract a PDF file to clean Markdown using PyMuPDF4LLM.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Markdown string with tables, headers, and hierarchy preserved.
    """
    logger.info("Extracting PDF: %s", pdf_path.name)
    md_text = pymupdf4llm.to_markdown(str(pdf_path))
    logger.info("Extracted %d characters from %s", len(md_text), pdf_path.name)
    return md_text
