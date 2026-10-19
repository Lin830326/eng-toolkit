"""``metrology`` domain: ISO 286 fits, tolerance stack-up, scan-to-model checks and GD&T form / position tools."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.metrology import gdt_tools
from eng_toolkit.domains.metrology.tools import TOOL_NAMES as _BASE_TOOL_NAMES
from eng_toolkit.domains.metrology.tools import server

TOOL_NAMES = _BASE_TOOL_NAMES + gdt_tools.TOOL_NAMES

DOMAIN = DomainInfo(
    name="metrology",
    title="Dimensional metrology (tolerances)",
    description=(
        "ISO 286 limits and fits (IT01-IT18, 3-500 mm) from an in-repo table, reverse search "
        "of standard fits for a clearance window, and 1-D tolerance stack-up by worst case, "
        "RSS and optional seeded Monte Carlo (NumPy), and a scan-to-model check of a point cloud "
        "against a STEP model (Open3D, OpenCASCADE; optional 'scan' extra), form errors (flatness, "
        "straightness, circularity, cylindricity as minimum zones) from measured points, and true "
        "position with MMC / LMC bonus tolerance (NumPy)."
    ),
    engines=("pint", "numpy", "open3d", "cadquery-ocp-novtk"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
