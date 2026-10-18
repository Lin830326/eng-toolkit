"""``drawing`` domain: orthographic views of 3D models on ISO sheets, as DXF and PDF."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.drawing.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="drawing",
    title="Drawing (2D views of 3D CAD models)",
    description=(
        "Orthographic views with exact hidden lines on an ISO 5457 sheet with title block and overall dimensions, "
        "first or third angle, as a deterministic DXF and a vector PDF (OpenCASCADE and ezdxf; optional 'drawing' extra)."
    ),
    engines=("pint", "numpy", "cadquery-ocp-novtk", "ezdxf"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
