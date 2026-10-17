"""eng-toolkit: AI-callable open-source engineering toolkit (MCP gateway).

All numeric results carry units (pint) and an uncertainty slot, and every tool
returns the unified :class:`eng_toolkit.contract.ToolResult` contract with
``review_status="unreviewed"``.  Nothing produced here is engineering-approved.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eng-toolkit")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
