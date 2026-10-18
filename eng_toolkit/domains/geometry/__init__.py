"""``geometry`` domain: exact measurement of 3D CAD models."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.geometry.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="geometry",
    title="Geometry (3D CAD models)",
    description=(
        "Exact volume, area, size, mass, face and hole inventory and distance, angle, diameter, area and "
        "wall-thickness queries on STEP, IGES and BREP files (OpenCASCADE; optional 'cad' extra)."
    ),
    engines=("pint", "numpy", "cadquery-ocp-novtk"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
