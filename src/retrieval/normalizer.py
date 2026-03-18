"""GRC-aware query normalizer and deterministic cache key builder.

Single-responsibility: normalize security finding text and build
deterministic cache keys for the Redis cache layer.

Normalization is deliberately conservative — GRC findings are terse
technical strings where every token carries meaning.  We only apply
safe transformations (lowercase, trim, collapse whitespace, strip
leading filler phrases) and preserve ALL punctuation and numbers.
"""

import hashlib
import re

from src.config.settings import AppSettings

# ── Filler prefixes (removed only when leading the text) ─────────

_FILLER_PREFIXES = [
    "can you map",
    "please map",
    "map this",
    "find controls for",
    "what controls apply to",
]

_FILLER_PATTERN = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in _FILLER_PREFIXES) + r")\s*",
    re.IGNORECASE,
)


def normalize_finding(text: str) -> str:
    """Normalize a security finding for cache key generation.

    Safe transforms only:
      1. Lowercase + strip
      2. Remove leading filler phrases (prefix-only)
      3. Collapse multiple whitespace → single space

    Preserves all punctuation (hyphens, dots, slashes, colons) because
    they carry meaning in control IDs (A.8.20), framework names (PCI-DSS),
    protocols (TCP/IP), and port references (port:3306).
    """
    text = text.lower().strip()
    text = _FILLER_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_cache_key(
    finding: str,
    frameworks: list[str],
    settings: AppSettings,
) -> str:
    """Build a deterministic SHA-256 cache key from finding + frameworks + config.

    The key encodes:
      - Normalized finding text
      - Sorted framework list  (order-independent, count-sensitive)
      - Model name             (prevents cross-model cache hits)
      - Collection name        (prevents cross-deployment collisions)

    Segments are separated by '||' (never appears in findings or framework keys).
    """
    normalized = normalize_finding(finding)
    fw_part = "|".join(sorted(frameworks))
    composite = (
        f"{normalized}||{fw_part}"
        f"||{settings.gemini.parse_model}"
        f"||{settings.qdrant.collection_name}"
    )
    digest = hashlib.sha256(composite.encode()).hexdigest()
    return f"{settings.redis.key_prefix}:query:{digest}"
