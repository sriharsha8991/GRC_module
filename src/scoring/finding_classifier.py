"""Finding classifier — LLM-based 3-class security finding classification.

Single-responsibility: given a security finding text, classifies it as one of:
  PRODUCT_VULNERABILITY — software flaw, CVE expected
  WEAK_DEFAULT          — insecure default shipped with a product, CVE possible
  PURE_MISCONFIGURATION — operational policy gap, no CVE

Also extracts component metadata and CPE-normalized vendor/product for
precise NVD queries.

Follows the same pattern as src/scoring/classifier.py (Gemini structured output).
"""

import json
import logging
import re

from google.genai import types

from src.config.genai_client import get_client
from src.config.settings import AppSettings
from src.scoring.models import FindingClassification

logger = logging.getLogger("scoring.finding_classifier")

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}")

_SYSTEM_PROMPT = """\
You are a cybersecurity analyst classifying security findings into one of \
three categories and extracting CPE-normalized component metadata.

═══ CLASSIFICATION (choose exactly one) ═══

1. PRODUCT_VULNERABILITY
   A flaw in a specific software component tied to a version. \
   Has or should have a CVE ID. The bug is IN the code.
   Examples:
   - Buffer overflow in OpenSSL 1.1.1k
   - SSRF in Next.js 13.4 Server Actions
   - Log4Shell in Apache Log4j 2.14.1
   - SQL injection in WordPress plugin WPForms v3.2
   - Deserialization RCE in Apache Commons Collections 3.x

2. WEAK_DEFAULT
   An insecure default, dangerous built-in behaviour, or bad practice \
   SHIPPED WITH a specific product. The product makes it too easy to be \
   insecure. A CVE may or may not exist.
   Examples:
   - Default admin credentials on a Cisco ASA appliance
   - TLS 1.0 still enabled by default in IIS 8.5
   - Insecure CORS wildcard shipped in Express.js default config
   - SSL 3.0 still supported in an F5 BIG-IP firmware
   - Default community string "public" in SNMP on a Juniper router
   Key distinction: a SPECIFIC PRODUCT is named, but the issue is an \
   insecure default/config shipped with it — not a code-level bug.

3. PURE_MISCONFIGURATION
   An operational policy gap, human error, or missing security control \
   with NO tie to a specific product flaw. No CVE is expected.
   Examples:
   - MFA not enforced on VPN access
   - Weak password policy (6 chars, no complexity)
   - Firewall rules not reviewed in 12 months
   - S3 bucket publicly accessible (operator error, not AWS bug)
   - Access reviews not conducted quarterly
   - Encryption not enabled at rest (policy gap, not product flaw)
   Key distinction: no specific software component/version is involved.

═══ EXTRACTION RULES ═══

For PRODUCT_VULNERABILITY and WEAK_DEFAULT, extract:
- software_component: exact product name (e.g. "Next.js", "OpenSSL")
- vendor: publisher (e.g. "vercel", "openssl", "apache", "cisco")
- version: exact version if stated (e.g. "13.0.0", "2.14.1")
- version_range: range if stated (e.g. ">= 13.4.0, < 14.1.1")
- ecosystem: "npm", "PyPI", "Maven", "Go", "NuGet", "RubyGems", \
  "crates.io", "OS" (for OS-level / firmware / appliance)
- named_vulnerability: well-known name if mentioned (Log4Shell, etc.)

For PURE_MISCONFIGURATION, leave ALL component fields as null.

═══ CPE NORMALIZATION ═══

For PRODUCT_VULNERABILITY and WEAK_DEFAULT, also provide:
- cpe_vendor: NVD CPE-style vendor name (lowercase, underscores for \
  spaces). Examples: "vercel", "apache", "openssl", "cisco", \
  "microsoft", "f5", "juniper".
- cpe_product: NVD CPE-style product name (lowercase, underscores for \
  spaces, keep dots/hyphens as in NVD convention). Examples: \
  "next.js", "log4j", "openssl", "tomcat", "big-ip_access_policy_manager", \
  "adaptive_security_appliance_software".

CPE format context: cpe:2.3:a:{cpe_vendor}:{cpe_product}:{version}
These values will be used directly in NVD API queries.

For PURE_MISCONFIGURATION, leave cpe_vendor and cpe_product as null.\
"""


class FindingClassifier:
    """Classifies a finding as VULNERABILITY or MISCONFIGURATION via Gemini."""

    def __init__(self, settings: AppSettings) -> None:
        self._client = get_client(settings)
        self._model = settings.gemini.parse_model

    def classify(self, finding_text: str) -> tuple[FindingClassification, dict]:
        """Classify a finding and extract component details.

        Also performs regex extraction of explicit CVE IDs from the text,
        which are merged into the LLM output regardless of what the LLM
        returns (deterministic override).

        Args:
            finding_text: Raw security finding text.

        Returns:
            (FindingClassification, {"prompt_tokens": int, "total_tokens": int})
        """
        logger.info("Classifying finding type (3-class: PRODUCT_VULNERABILITY / WEAK_DEFAULT / PURE_MISCONFIGURATION)")

        # Deterministic CVE extraction via regex
        regex_cves = sorted(set(_CVE_PATTERN.findall(finding_text)))

        response = self._client.models.generate_content(
            model=self._model,
            contents=f"FINDING: {finding_text}",
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=FindingClassification,
                temperature=0.1,
            ),
        )

        usage = response.usage_metadata
        tokens = {
            "prompt_tokens": usage.prompt_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        } if usage else {"prompt_tokens": 0, "total_tokens": 0}

        raw_text = response.text
        try:
            raw_dict = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.error("Finding classifier returned non-JSON: %s", raw_text[:500])
            raise ValueError("Finding classifier returned invalid JSON")

        classification = FindingClassification.model_validate(raw_dict)

        # Merge regex-extracted CVE IDs (deterministic override)
        if regex_cves:
            existing = set(classification.explicit_cve_ids)
            merged = sorted(existing | set(regex_cves))
            classification.explicit_cve_ids = merged

        logger.info(
            "Classified as %s | component=%s version=%s | explicit_cves=%s (%d tokens)",
            classification.finding_type,
            classification.software_component,
            classification.version,
            classification.explicit_cve_ids,
            tokens["total_tokens"],
        )

        return classification, tokens
