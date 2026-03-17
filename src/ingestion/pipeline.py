"""Ingestion pipeline orchestrator — end-to-end PDF → Qdrant.

PDF → Markdown (PyMuPDF4LLM) → Markdown Chunks → Gemini Embed → Qdrant.
No structured parsing — the markdown splitter preserves heading structure
and the embeddings + reranker handle semantic matching.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import IngestionSettings, get_ingestion_settings
from src.config.registry import get_framework
from src.ingestion.storage import get_storage
from src.ingestion.extractor import extract_pdf_to_markdown
from src.ingestion.chunker import chunk_markdown
from src.ingestion.qdrant_loader import QdrantLoader

logger = logging.getLogger("ingestion.pipeline")


@dataclass
class IngestionResult:
    """Summary of an ingestion run."""
    framework_key: str
    collection_name: str
    chunks_created: int
    points_upserted: int
    duration_seconds: float
    success: bool
    error: str | None = None


def ingest_framework(
    pdf_path: str | Path,
    framework_key: str,
    settings: IngestionSettings | None = None,
) -> IngestionResult:
    """Run the full ingestion pipeline for a single GRC framework PDF.

    Args:
        pdf_path: Path to the framework PDF.
        framework_key: Key in frameworks.json (e.g., "iso_27001", "nist_800_53").
        settings: Override settings (uses defaults if None).

    Returns:
        IngestionResult with stats and status.
    """
    settings = settings or get_ingestion_settings()
    pdf_path = Path(pdf_path)
    start = time.time()

    try:
        # 0. Validate framework exists in registry
        fw_meta = get_framework(framework_key)
        logger.info(
            "=== Ingestion: %s (%s) → %s ===",
            framework_key, fw_meta["display_name"], settings.collection_name,
        )

        # 1. Store PDF
        storage = get_storage(settings)
        stored_path = storage.store(pdf_path, framework_key)

        # 2. Extract PDF → Markdown
        markdown = extract_pdf_to_markdown(stored_path)

        # 3. Split markdown into chunks (no parsing needed)
        chunks = chunk_markdown(
            markdown=markdown,
            framework_key=framework_key,
            framework_version=fw_meta["version"],
            settings=settings,
            source_document=fw_meta["display_name"],
        )

        if not chunks:
            raise ValueError(f"Chunker produced 0 chunks for {framework_key}")

        # 4. Embed + upsert into grc_controls
        loader = QdrantLoader(settings)
        points = loader.ingest_chunks(chunks)

        # 5. Cleanup PDF if configured
        if settings.delete_pdf_after_ingestion:
            storage.delete(stored_path)

        duration = time.time() - start
        logger.info(
            "=== Ingestion complete: %d chunks → %d points (%.1fs) ===",
            len(chunks), points, duration,
        )

        return IngestionResult(
            framework_key=framework_key,
            collection_name=settings.collection_name,
            chunks_created=len(chunks),
            points_upserted=points,
            duration_seconds=round(duration, 2),
            success=True,
        )

    except Exception as e:
        duration = time.time() - start
        logger.error("Ingestion failed for %s: %s", framework_key, e, exc_info=True)
        return IngestionResult(
            framework_key=framework_key,
            collection_name=settings.collection_name,
            chunks_created=0,
            points_upserted=0,
            duration_seconds=round(duration, 2),
            success=False,
            error=str(e),
        )
