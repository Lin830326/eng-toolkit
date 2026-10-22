"""``geometry`` domain: exact measurement of 3D CAD models and parts built from a declarative spec."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.geometry.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="geometry",
    title="Geometry (3D CAD models)",
    description=(
        "Exact volume, area, size, mass, face and hole inventory and distance, angle, diameter, area and "
        "wall-thickness queries on STEP, IGES and BREP files, and parts built from a JSON spec (no code is executed) "
        "as STEP, GLB, STL and BREP, and coloured GLB views by face kind, DFM finding or chosen faces (OpenCASCADE; optional 'cad' extra)."
    ),
    engines=("pint", "numpy", "cadquery-ocp-novtk"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
