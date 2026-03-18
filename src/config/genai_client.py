"""Centralized Gemini client — single shared instance per API key.

Avoids creating multiple genai.Client objects across embedder, mapper,
and critic.  Thread-safe via module-level caching keyed by API key.
"""

from functools import lru_cache

from google import genai

from src.config.settings import AppSettings


@lru_cache(maxsize=4)
def get_genai_client(api_key: str) -> genai.Client:
    """Return a shared genai.Client for the given API key."""
    return genai.Client(api_key=api_key)


def get_client(settings: AppSettings) -> genai.Client:
    """Convenience wrapper — extracts key from settings."""
    return get_genai_client(settings.gemini.api_key)
