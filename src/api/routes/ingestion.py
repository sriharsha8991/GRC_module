"""Ingestion route — upload a PDF and ingest into Qdrant."""

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.schemas import IngestionResponse
from src.config.registry import get_framework, list_framework_keys
from src.ingestion.pipeline import ingest_framework

logger = logging.getLogger("api.ingestion")

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/ingest", response_model=IngestionResponse)
async def ingest(
    file: UploadFile = File(..., description="GRC framework PDF"),
    framework_key: str = Form(..., description="Framework key, e.g. iso_27001"),
):
    """Upload a GRC framework PDF and ingest it into the vector store."""

    # Validate framework key
    try:
        get_framework(framework_key)
    except ValueError:
        available = list_framework_keys()
        raise HTTPException(
            status_code=422,
            detail=f"Unknown framework_key '{framework_key}'. Available: {available}",
        )

    # Validate file type
    if file.content_type not in ("application/pdf",):
        raise HTTPException(
            status_code=422,
            detail=f"Expected a PDF file, got '{file.content_type}'",
        )

    # Write upload to a temp file and run the pipeline
    suffix = Path(file.filename).suffix if file.filename else ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = ingest_framework(pdf_path=tmp_path, framework_key=framework_key)
    except Exception as exc:
        logger.exception("Ingestion failed for %s", framework_key)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        tmp_path.unlink(missing_ok=True)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    return IngestionResponse(
        framework_key=result.framework_key,
        collection_name=result.collection_name,
        chunks_created=result.chunks_created,
        points_upserted=result.points_upserted,
        duration_seconds=result.duration_seconds,
        success=result.success,
        error=result.error,
    )
