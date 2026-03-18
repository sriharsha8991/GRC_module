"""PDF storage handler — local filesystem now, S3-compatible interface for future extension."""

import logging
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config.settings import AppSettings

logger = logging.getLogger("ingestion.storage")


@runtime_checkable
class PDFStorage(Protocol):
    """Interface for PDF storage backends."""

    def store(self, source_path: Path, framework_key: str) -> Path:
        """Store a PDF and return the stored path."""
        ...

    def get_path(self, framework_key: str, filename: str) -> Path:
        """Get the path to a stored PDF."""
        ...

    def delete(self, path: Path) -> None:
        """Delete a stored PDF."""
        ...

    def list_pdfs(self, framework_key: str) -> list[Path]:
        """List all PDFs for a framework."""
        ...


class LocalPDFStorage:
    """Stores PDFs on local filesystem under data/pdfs/{framework_key}/."""

    def __init__(self, settings: AppSettings):
        self._base_dir = Path(settings.storage.local_pdf_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def store(self, source_path: Path, framework_key: str) -> Path:
        dest_dir = self._base_dir / framework_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source_path.name
        shutil.copy2(source_path, dest)
        logger.info("Stored PDF: %s → %s", source_path.name, dest)
        return dest

    def get_path(self, framework_key: str, filename: str) -> Path:
        return self._base_dir / framework_key / filename

    def delete(self, path: Path) -> None:
        if path.exists():
            path.unlink()
            logger.info("Deleted PDF: %s", path)

    def list_pdfs(self, framework_key: str) -> list[Path]:
        fdir = self._base_dir / framework_key
        if not fdir.exists():
            return []
        return sorted(fdir.glob("*.pdf"))


def get_storage(settings: AppSettings) -> PDFStorage:
    """Factory — returns the appropriate storage backend."""
    if settings.storage.backend == "local":
        return LocalPDFStorage(settings)
    # Future: elif settings.storage.backend == "s3": return S3PDFStorage(settings)
    raise ValueError(f"Unknown storage backend: {settings.storage.backend}")
