"""Query route — map a security finding to compliance controls.

Single-responsibility: HTTP interface for the retrieval pipeline.
Validates the request and delegates to the pipeline orchestrator.
"""

import logging

from fastapi import APIRouter, HTTPException

from src.api.schemas import QueryRequest, QueryResponse
from src.config.registry import get_framework, list_framework_keys
from src.retrieval.pipeline import query_finding

logger = logging.getLogger("api.query")

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
def query(request: QueryRequest):
    """Map a security finding to compliance framework controls.

    Accepts a finding and list of target frameworks, returns structured
    control mappings with citations, confidence scores, and critic verdicts.
    """
    # Validate all target frameworks exist in registry
    invalid = [
        k for k in request.target_frameworks
        if k not in list_framework_keys()
    ]
    if invalid:
        available = list_framework_keys()
        raise HTTPException(
            status_code=422,
            detail=f"Unknown framework(s): {invalid}. Available: {available}",
        )

    try:
        response = query_finding(request)
    except Exception as exc:
        logger.exception("Query pipeline failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return response
