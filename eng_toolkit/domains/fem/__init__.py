"""``fem`` domain: tetrahedral meshing with Gmsh and linear static analysis with CalculiX of a solid CAD model."""

from eng_toolkit.domains import DomainInfo
from eng_toolkit.domains.fem.tools import TOOL_NAMES, server

DOMAIN = DomainInfo(
    name="fem",
    title="Finite elements (meshing and linear statics)",
    description=(
        "Second-order tetrahedral mesh of a STEP, IGES or BREP solid (Gmsh) and linear static analysis (CalculiX): maximum "
        "displacement, maximum von Mises stress, reactions against the applied loads, with faces picked by plane selectors. "
        "The toolkit writes the Gmsh and CalculiX inputs itself; both programs are installed by the user and run as separate "
        "processes (optional 'cad' extra for reading the geometry). fem_view writes a static result as a colour-mapped GLB "
        "(stress or displacement on the mesh surface, optionally deformed) with a legend SVG."
    ),
    engines=("pint", "numpy", "cadquery-ocp-novtk"),
    server=server,
    tools=TOOL_NAMES,
)

__all__ = ["DOMAIN", "server"]
