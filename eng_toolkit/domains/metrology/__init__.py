"""``metrology`` domain: ISO 286 fits, tolerance stack-up and scan-to-model checks."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.metrology.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="metrology",
    title="Dimensional metrology (tolerances)",
    description=(
        "ISO 286 limits and fits (IT01-IT18, 3-500 mm) from an in-repo table, reverse search "
        "of standard fits for a clearance window, and 1-D tolerance stack-up by worst case, "
        "RSS and optional seeded Monte Carlo (NumPy), and a scan-to-model check of a point cloud "
        "against a STEP model (Open3D, OpenCASCADE; optional 'scan' extra)."
    ),
    engines=("pint", "numpy", "open3d", "cadquery-ocp-novtk"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
