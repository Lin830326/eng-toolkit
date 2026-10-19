"""``drawing`` domain: orthographic views of 3D models on ISO sheets as DXF and PDF, and a reader of DXF and PDF drawings.

``DOMAIN`` and ``server`` are built on first use (module ``__getattr__``), not when a submodule of this package is
imported: the readers ``dxf_read`` and ``pdf_read`` run in a child process for every call of ``drawing_read``, and
importing them must not pull in the MCP stack (FastMCP, about 1.9 s) that only the server needs.
"""

from __future__ import annotations

from typing import Any

_LAZY = ("DOMAIN", "server", "TOOL_NAMES")


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from eng_toolkit.domains import DomainInfo
    from eng_toolkit.domains.drawing.tools import TOOL_NAMES, server

    domain = DomainInfo(
        name="drawing",
        title="Drawing (2D views of 3D CAD models)",
        description=(
            "Orthographic views with exact hidden lines on an ISO 5457 sheet with title block and overall dimensions, "
            "first or third angle, as a deterministic DXF and a vector PDF (OpenCASCADE and ezdxf); and a reader of DXF and PDF "
            "drawings: units, layers, dimensions with DIMLFAC applied, title block, texts, PDF text and vector statistics, read in a "
            "limited child process (ezdxf and pdfminer.six; optional 'drawing' extra); and a converter of DXF drawings to PDF, SVG, PNG at true scale or to another "
            "DXF release, with a report of what the target cannot carry."
        ),
        engines=("pint", "numpy", "cadquery-ocp-novtk", "ezdxf", "pdfminer.six"),
        server=server,
        tools=TOOL_NAMES,
    )
    globals().update(DOMAIN=domain, server=server, TOOL_NAMES=TOOL_NAMES)
    return globals()[name]


__all__ = ["DOMAIN", "server"]
