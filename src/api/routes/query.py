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

    Runs a 5-stage retrieval pipeline: embed → search → rerank → map → critique.
    If Redis caching is enabled and a matching query exists, returns the cached
    result instantly with zero token usage.

    **Request body:**
    - `finding_text` — the security finding or observation (min 10 chars).
    - `target_frameworks` — list of framework keys to search against
      (e.g. `["iso_27001"]`, `["iso_27001", "iso_27002"]`).

    **Response:**
    - `mappings` — list of `ControlMapping` objects, each with control ID,
      title, domain, risk mitigated, citation, confidence score (0–100),
      and critic verdict (`APPROVED` / `FAILED`).
    - `token_usage` — Gemini tokens consumed (mapper + critic). Zero on cache hit.
    - `duration_seconds` — end-to-end latency.

    **Errors:**
    - `422` — unknown framework key(s) in `target_frameworks`.
    - `500` — pipeline failure (e.g. Qdrant/Gemini unreachable).
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
