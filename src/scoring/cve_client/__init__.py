"""CVE data source clients — NVD, cve.org, OSV.dev.

Package re-exports so existing ``from src.scoring.cve_client import …``
imports continue to work unchanged.
"""

from ._base import CveDataSource
from .cve_org import CveOrgClient
from .nvd import NvdClient
from .osv import OsvClient

__all__ = [
    "CveDataSource",
    "CveOrgClient",
    "NvdClient",
    "OsvClient",
]
