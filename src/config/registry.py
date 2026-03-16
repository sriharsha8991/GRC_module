"""Framework registry — loads framework metadata from frameworks.json."""

import json
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger("config.registry")

_REGISTRY_PATH = Path(__file__).resolve().parent / "frameworks.json"


@lru_cache
def _load_registry() -> dict[str, dict]:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded %d frameworks from registry", len(data))
    return data


def get_framework(key: str) -> dict:
    """Get metadata for a framework by key. Raises ValueError if unknown."""
    registry = _load_registry()
    if key not in registry:
        raise ValueError(
            f"Unknown framework '{key}'. Available: {list(registry.keys())}"
        )
    return registry[key]


def list_framework_keys() -> list[str]:
    """Return all registered framework keys."""
    return list(_load_registry().keys())


def has_specialized_parser(key: str) -> bool:
    """Check if a framework has a specialized (non-Gemini) parser."""
    fw = get_framework(key)
    return fw.get("has_specialized_parser", False)
