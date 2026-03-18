"""Gemini embedding client — primary embedder for the ingestion pipeline.

Uses google-genai SDK with gemini-embedding-001.
Supports asymmetric search via task_type (RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY).
Embedding batches are sent concurrently via ThreadPoolExecutor (I/O-bound).
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings

logger = logging.getLogger("ingestion.embedder")

# Gemini embed_content accepts up to 100 texts per call
_GEMINI_MAX_BATCH = 100


class GeminiEmbedder:
    """Embeds text using Gemini embedding-001 with MRL dimensionality control."""

    def __init__(self, settings: AppSettings):
        self._client = get_client(settings)
        self._model = settings.gemini.embedding_model
        self._dimension = settings.embedding.dimension
        self._batch_size = min(settings.embedding.batch_size, _GEMINI_MAX_BATCH)

    def _embed_batch(
        self,
        texts: list[str],
        task_type: str,
    ) -> list[list[float]]:
        """Embed a single batch (≤100 texts) with retry."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = self._client.models.embed_content(
                    model=self._model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self._dimension,
                    ),
                )
                return [e.values for e in result.embeddings]
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Embed batch failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1, max_retries, e, wait,
                    )
                    time.sleep(wait)
                else:
                    raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document texts for ingestion (task_type=RETRIEVAL_DOCUMENT).

        Batches are sent concurrently via ThreadPoolExecutor since
        Gemini API calls are I/O-bound.
        """
        if len(texts) <= self._batch_size:
            return self._embed_batch(texts, "RETRIEVAL_DOCUMENT")

        batches = [
            texts[i : i + self._batch_size]
            for i in range(0, len(texts), self._batch_size)
        ]
        all_embeddings: list[list[float] | None] = [None] * len(batches)

        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            future_to_idx = {
                executor.submit(self._embed_batch, batch, "RETRIEVAL_DOCUMENT"): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                all_embeddings[idx] = future.result()
                logger.debug(
                    "Embedded docs batch %d/%d (%d texts)",
                    idx + 1, len(batches), len(batches[idx]),
                )

        # Flatten: list of batched results → single list
        flat: list[list[float]] = []
        for batch_result in all_embeddings:
            flat.extend(batch_result)  # type: ignore[arg-type]
        return flat

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query text (task_type=RETRIEVAL_QUERY)."""
        result = self._embed_batch([text], "RETRIEVAL_QUERY")
        return result[0]
