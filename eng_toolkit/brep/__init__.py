"""The one owner of OpenCASCADE (OCP, optional extra ``cad``) and of 3D model files.

Lengths are millimetres, angles radians.  OCP is imported inside functions, so importing this package never
needs the wheel; a missing or unloadable one raises :class:`EngineUnavailable` from the first call that
needs it.  Nothing outside this package may import OCP or reach for ``Solid._handle``.  Welding a
tessellation and testing it for closure (:func:`weld`, :func:`mesh_topology`) is pure NumPy and
needs neither OCP nor Open3D, and so is :func:`parse_edge_selector`.

Face and hole ids from :func:`inventory` are valid only for the same file bytes and the same OCCT version.

Modelling (``make_*``, :func:`combine`, :func:`fillet`, :func:`chamfer`, :func:`transform`) and the writers
(:func:`write_step`, :func:`write_brep`, :func:`write_mesh`) are what the part spec language in
``domains/geometry/part.py`` builds on.  :func:`project` turns a solid into the 2D lines, arcs, ellipses and
polylines of one of the seven fixed views (:data:`VIEW_FRAMES`), hidden lines removed exactly.
"""

from eng_toolkit.brep._files import SOLID_EXTENSIONS, read_solid, triangulate
from eng_toolkit.brep._mesh import WELD_EPS_MM, mesh_topology, weld
from eng_toolkit.brep._model import (
    EDGE_TERMS,
    MODES,
    Circle2,
    EdgeMatch,
    EdgeSelector,
    Polygon2,
    Profile2,
    Rect2,
    SketchPlane,
    Slot2,
    chamfer,
    combine,
    fillet,
    make_box,
    make_cone,
    make_cylinder,
    make_hole_tool,
    make_prism,
    make_revolution,
    make_sphere,
    parse_edge_selector,
    select_edges,
    transform,
    validity,
)
from eng_toolkit.brep._occ import load as require_engine
from eng_toolkit.brep._project import VIEW_FRAMES, Arc2, Ellipse2, Line2, Poly2, Prim2, Projection, ViewFrame, ViewName, project
from eng_toolkit.brep._query import angle, distance, inventory, lookup, mass_properties, wall_thickness
from eng_toolkit.brep._types import (
    Entity,
    FaceInfo,
    FaceRef,
    HoleInfo,
    HoleRef,
    Inventory,
    MassProperties,
    Mesh,
    MeshTopology,
    PointRef,
    Solid,
    Vec3,
)
from eng_toolkit.brep._write import write_brep, write_mesh, write_step
from eng_toolkit.errors import EngineUnavailable

__all__ = [
    "EDGE_TERMS",
    "MODES",
    "SOLID_EXTENSIONS",
    "VIEW_FRAMES",
    "WELD_EPS_MM",
    "Arc2",
    "Circle2",
    "Ellipse2",
    "EdgeMatch",
    "EdgeSelector",
    "EngineUnavailable",
    "Entity",
    "FaceInfo",
    "FaceRef",
    "HoleInfo",
    "HoleRef",
    "Inventory",
    "Line2",
    "MassProperties",
    "Mesh",
    "MeshTopology",
    "PointRef",
    "Poly2",
    "Polygon2",
    "Prim2",
    "Profile2",
    "Projection",
    "Rect2",
    "SketchPlane",
    "Slot2",
    "Solid",
    "Vec3",
    "ViewFrame",
    "ViewName",
    "angle",
    "chamfer",
    "combine",
    "distance",
    "fillet",
    "inventory",
    "lookup",
    "make_box",
    "make_cone",
    "make_cylinder",
    "make_hole_tool",
    "make_prism",
    "make_revolution",
    "make_sphere",
    "mass_properties",
    "mesh_topology",
    "parse_edge_selector",
    "project",
    "read_solid",
    "require_engine",
    "select_edges",
    "transform",
    "triangulate",
    "validity",
    "wall_thickness",
    "weld",
    "write_brep",
    "write_mesh",
    "write_step",
]
