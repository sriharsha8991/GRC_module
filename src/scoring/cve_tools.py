"""CVE agent tool declarations and constants.

Single-responsibility: defines the Gemini function-calling tool schemas,
system prompt, and shared constants used by the CVE agent.

Parameter schemas are Pydantic models — their JSON schemas are passed
to ``FunctionDeclaration.parameters_json_schema`` so Gemini knows
the exact shape of each tool's arguments.
"""

from __future__ import annotations

from typing import Literal, Optional

from google.genai import types
from pydantic import BaseModel, Field

# ── Parameter schemas ──────────────────────────────────


class NvdCpeParams(BaseModel):
    vendor: str = Field(description="CPE vendor, lowercase (e.g. 'apache', 'microsoft').")
    product: str = Field(description="CPE product, lowercase, underscores for spaces (e.g. 'log4j', 'next.js').")
    version: str = Field(description="Exact version or '*' for versionless search.")


class NvdKeywordParams(BaseModel):
    keywords: str = Field(description="Free-text search terms (e.g. 'Apache Log4j RCE').")


class OsvParams(BaseModel):
    package_name: str = Field(
        description="Registry package name: Maven='groupId:artifactId', Packagist='vendor/package', npm/PyPI/Go=name.",
    )
    ecosystem: str = Field(
        description="Case-sensitive OSV ecosystem: npm|PyPI|Maven|Go|crates.io|NuGet|RubyGems|Packagist|Debian:NN|Alpine:vN.NN|Ubuntu:NN.NN:LTS.",
    )
    version: Optional[str] = Field(default=None, description="Exact version or omit for all vulns.")


class VuldbParams(BaseModel):
    product: str = Field(description="Product name, lowercase.")
    version: str = Field(description="Version string.")
    vendor: str = Field(description="Vendor name, lowercase.")


class ReportFindingParams(BaseModel):
    finding_type: Literal[
        "PRODUCT_VULNERABILITY",
        "WEAK_DEFAULT",
        "PURE_MISCONFIGURATION",
    ] = Field(description="Classification of this finding.")
    reasoning: str = Field(description="2-3 sentence justification.")
    software_component: Optional[str] = Field(default=None, description="Software name if applicable.")
    vendor: Optional[str] = Field(default=None, description="Vendor/publisher.")
    version: Optional[str] = Field(default=None, description="Version if stated.")


# ── Tool declarations ──────────────────────────────────

TOOL_SEARCH_NVD_BY_CPE = types.FunctionDeclaration(
    name="search_nvd_by_cpe",
    description="Search NVD by CPE. Primary method for known products. Use '*' version for versionless fallback.",
    parameters_json_schema=NvdCpeParams.model_json_schema(),
)

TOOL_SEARCH_NVD_BY_KEYWORD = types.FunctionDeclaration(
    name="search_nvd_by_keyword",
    description="Free-text NVD keyword search. Last resort when CPE and OSV return nothing.",
    parameters_json_schema=NvdKeywordParams.model_json_schema(),
)

TOOL_SEARCH_OSV = types.FunctionDeclaration(
    name="search_osv",
    description=(
        "Search OSV.dev for open-source package vulns. "
        "Ecosystem is REQUIRED, CASE-SENSITIVE. "
        "NOT for proprietary software — use NVD CPE instead."
    ),
    parameters_json_schema=OsvParams.model_json_schema(),
)

TOOL_SEARCH_VULDB = types.FunctionDeclaration(
    name="search_vuldb",
    description="Search VulDB. Supplementary source — use alongside NVD/OSV.",
    parameters_json_schema=VuldbParams.model_json_schema(),
)

TOOL_REPORT_FINDING = types.FunctionDeclaration(
    name="report_finding",
    description="Report final classification. MUST be your LAST action.",
    parameters_json_schema=ReportFindingParams.model_json_schema(),
)

# ── System prompt ──────────────────────────────────────

SYSTEM_PROMPT = """\
You are a CVE analyst. Classify a security finding and search for CVE IDs.

CLASSES:
- PRODUCT_VULNERABILITY: Code flaw in a specific software version. Has/should have CVE.
- WEAK_DEFAULT: Insecure default in a product. CVE may exist.
- PURE_MISCONFIGURATION: Operational policy gap, no product flaw. No CVE. Report immediately, skip search.

SEARCH RULES:
1. Search multiple sources in parallel: NVD CPE (primary), OSV (open-source), VulDB (supplementary).
2. OSV: ecosystem CASE-SENSITIVE, use exact registry names. Not for proprietary software.
3. If versioned CPE returns 0 → retry with version='*'.
4. If CPE+OSV both 0 → try search_nvd_by_keyword.
5. If finding contains explicit CVE IDs, still search for additional related CVEs.

WORKFLOW:
1. PURE_MISCONFIGURATION → call report_finding immediately.
2. Otherwise → search → report_finding as LAST action.\
"""

# Max agent turns to prevent infinite loops
MAX_TURNS = 10

# Source priority for deduplication (lower = higher priority)
SOURCE_PRIORITY = {
    "NVD_CPE": 1,
    "OSV": 2,
    "VULDB": 3,
    "NVD_CPE_VERSIONLESS": 4,
    "NVD_KEYWORD": 5,
    "GOOGLE_SEARCH": 6,
}

# Google Search grounding prompt template
GOOGLE_SEARCH_PROMPT = """\
List CVE IDs (CVE-YYYY-NNNNN) for this vulnerability. One per line with brief description.
Say "No CVEs found" if none exist.

{software_component} {version} ({vendor}): {finding_text}\
"""
