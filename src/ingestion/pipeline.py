"""Ingestion pipeline orchestrator — end-to-end PDF → Qdrant."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import IngestionSettings, get_ingestion_settings
from src.ingestion.storage import get_storage
from src.ingestion.extractor import extract_pdf_to_markdown
from src.ingestion.parser import get_parser
from src.ingestion.chunker import chunk_controls
from src.ingestion.qdrant_loader import QdrantLoader

logger = logging.getLogger("ingestion.pipeline")


@dataclass
class IngestionResult:
    """Summary of an ingestion run."""
    framework_key: str
    collection_name: str
    controls_parsed: int
    chunks_created: int
    points_upserted: int
    duration_seconds: float
    success: bool
    error: str | None = None


def ingest_framework(
    pdf_path: str | Path,
    framework_key: str,
    collection_name: str | None = None,
    reingest: bool = False,
    settings: IngestionSettings | None = None,
) -> IngestionResult:
    """Run the full ingestion pipeline for a single GRC framework PDF.

    Args:
        pdf_path: Path to the framework PDF (local or will be stored).
        framework_key: Key matching parser + config (e.g. "iso27001").
        collection_name: Qdrant collection name. Defaults to framework_key.
        reingest: If True, drop + recreate the collection before ingesting.
        settings: Override settings (uses defaults if None).

    Returns:
        IngestionResult with stats and status.
    """
    settings = settings or get_ingestion_settings()
    collection_name = collection_name or framework_key
    pdf_path = Path(pdf_path)
    start = time.time()

    try:
        # 1. Store PDF
        logger.info("=== Ingestion: %s → %s ===", framework_key, collection_name)
        storage = get_storage(settings)
        stored_path = storage.store(pdf_path, framework_key)

        # 2. Extract PDF → Markdown
        markdown = extract_pdf_to_markdown(stored_path)

        # 3. Parse Markdown → structured controls
        parser = get_parser(framework_key)
        controls = parser.parse(markdown)

        if not controls:
            raise ValueError(f"Parser returned 0 controls for {framework_key}")

        # 4. Chunk controls
        chunks = chunk_controls(controls, framework_key, settings)

        # 5. Load into Qdrant
        loader = QdrantLoader(settings)
        if reingest:
            points = loader.reingest(collection_name, chunks)
        else:
            loader.create_collection(collection_name)
            points = loader.ingest_chunks(collection_name, chunks)

        # 6. Cleanup PDF if configured
        if settings.delete_pdf_after_ingestion:
            storage.delete(stored_path)

        duration = time.time() - start
        logger.info(
            "=== Ingestion complete: %d controls → %d chunks → %d points (%.1fs) ===",
            len(controls), len(chunks), points, duration,
        )

        return IngestionResult(
            framework_key=framework_key,
            collection_name=collection_name,
            controls_parsed=len(controls),
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
            collection_name=collection_name or framework_key,
            controls_parsed=0,
            chunks_created=0,
            points_upserted=0,
            duration_seconds=round(duration, 2),
            success=False,
            error=str(e),
        )
