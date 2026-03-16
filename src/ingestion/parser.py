"""Markdown → structured JSON parser for GRC framework documents.

Each framework has its own document structure. This module provides a base
class and per-framework parsers that extract controls into a uniform schema.
"""

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable

import yaml
from pathlib import Path

logger = logging.getLogger("ingestion.parser")

# ── Uniform control schema ────────────────────────────────

@dataclass
class ParsedControl:
    """One control extracted from a GRC framework document."""
    framework: str
    framework_version: str
    framework_category: str
    domain: str
    control_id: str
    title: str
    text: str  # Full control text (description + guidance concatenated)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Parser protocol ───────────────────────────────────────

@runtime_checkable
class FrameworkParser(Protocol):
    """Interface for framework-specific parsers."""

    def parse(self, markdown: str) -> list[ParsedControl]:
        """Parse markdown text into a list of controls."""
        ...


# ── Category config loader ────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "framework_categories.yaml"


def load_framework_config(framework_key: str) -> dict:
    """Load framework metadata and categories from YAML config."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        all_configs = yaml.safe_load(f)
    if framework_key not in all_configs:
        raise ValueError(
            f"Framework '{framework_key}' not found in {_CONFIG_PATH}. "
            f"Available: {list(all_configs.keys())}"
        )
    return all_configs[framework_key]


def resolve_category(framework_key: str, control_id: str) -> str:
    """Map a control_id to its framework_category using the config.

    For ISO 27001: control_id "8.20" → major prefix "8" → "Technological controls"
    For NIST 800-53: control_id "AC-4" → family prefix "AC" → "Access Control"
    For PCI-DSS: control_id "1.2.1" → major prefix "1" → "Build and Maintain..."
    """
    config = load_framework_config(framework_key)
    categories = config.get("categories", {})

    # Try progressively shorter prefixes
    # "8.20" → "8.20", "8.2", "8" / "AC-4" → "AC-4", "AC" / "1.2.1" → "1.2.1", "1.2", "1"
    parts_dot = control_id.split(".")
    parts_dash = control_id.split("-")

    candidates = []
    # Dot-separated: "8.20" → ["8.20", "8"]
    for i in range(len(parts_dot), 0, -1):
        candidates.append(".".join(parts_dot[:i]))
    # Dash-separated: "AC-4" → ["AC-4", "AC"]
    for i in range(len(parts_dash), 0, -1):
        candidates.append("-".join(parts_dash[:i]))
    # Also try the raw first token (letters only) for NIST-style families
    alpha_prefix = re.match(r"^[A-Za-z]+", control_id)
    if alpha_prefix:
        candidates.append(alpha_prefix.group())

    for candidate in candidates:
        if candidate in categories:
            return categories[candidate]

    return "Uncategorized"


# ── ISO 27001 parser ──────────────────────────────────────

class ISO27001Parser:
    """Parses ISO/IEC 27001:2022 Annex A controls from markdown tables."""

    FRAMEWORK_KEY = "iso27001"

    # Category header rows in the Annex A table: "| 5 | Organizational controls |..."
    _CATEGORY_PATTERN = re.compile(
        r"^\|\s*(\d+)\s*\|\s*([A-Z][a-zA-Z\s]+controls)\s*\|",
        re.MULTILINE,
    )

    # Control rows: "| 5.1 | Policies for info... | Control ...shall be... |"
    _CONTROL_PATTERN = re.compile(
        r"^\|\s*([\d]+\.[\d]+)\s*\|\s*(.+?)\s*\|\s*(?:Control\s+)?(.+?)\s*\|",
        re.MULTILINE,
    )

    def parse(self, markdown: str) -> list[ParsedControl]:
        config = load_framework_config(self.FRAMEWORK_KEY)
        controls: list[ParsedControl] = []

        # Find the Annex A section
        annex_start = markdown.find("Annex A")
        if annex_start == -1:
            annex_start = markdown.find("Table A.1")
        if annex_start == -1:
            logger.warning("Could not find Annex A section in ISO 27001 document")
            return controls

        annex_text = markdown[annex_start:]

        # Track current category
        current_category = "Uncategorized"
        current_domain = "General"

        # Split into lines and process
        lines = annex_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Check for category header row
            cat_match = self._CATEGORY_PATTERN.match(line)
            if cat_match:
                cat_num = cat_match.group(1)
                cat_name = cat_match.group(2).strip()
                current_category = cat_name
                current_domain = cat_name
                i += 1
                continue

            # Check for control row
            ctrl_match = self._CONTROL_PATTERN.match(line)
            if ctrl_match:
                control_id = ctrl_match.group(1).strip()
                title = ctrl_match.group(2).strip()
                description = ctrl_match.group(3).strip()

                # Clean up PDF artifacts
                title = re.sub(r"\s*-\s*$", "", title)  # trailing hyphens
                title = re.sub(r"\s+", " ", title)  # normalize whitespace
                description = re.sub(r"\s+", " ", description)

                # Resolve category from config
                framework_category = resolve_category(self.FRAMEWORK_KEY, control_id)

                # Build the full text block for embedding
                text_block = f"{control_id} — {title}\n\n{description}"

                controls.append(ParsedControl(
                    framework=config["name"],
                    framework_version=config["version"],
                    framework_category=framework_category,
                    domain=current_domain,
                    control_id=control_id,
                    title=title,
                    text=text_block,
                ))

            i += 1

        logger.info("Parsed %d controls from ISO 27001", len(controls))
        return controls


# ── Parser registry ───────────────────────────────────────

_PARSER_REGISTRY: dict[str, type] = {
    "iso27001": ISO27001Parser,
}


def get_parser(framework_key: str) -> FrameworkParser:
    """Get the parser for a given framework key."""
    if framework_key not in _PARSER_REGISTRY:
        raise ValueError(
            f"No parser for framework '{framework_key}'. "
            f"Available: {list(_PARSER_REGISTRY.keys())}"
        )
    return _PARSER_REGISTRY[framework_key]()


def register_parser(framework_key: str, parser_class: type) -> None:
    """Register a new framework parser at runtime."""
    _PARSER_REGISTRY[framework_key] = parser_class
