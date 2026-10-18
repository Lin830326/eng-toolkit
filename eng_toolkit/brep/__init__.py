"""The one owner of OpenCASCADE (OCP, optional extra ``cad``) and of 3D model files.

Lengths are millimetres, angles radians.  OCP is imported inside functions, so importing this package never
needs the wheel; a missing or unloadable one raises :class:`EngineUnavailable` from the first call that
needs it.  Nothing outside this package may import OCP or reach for ``Solid._handle``.  Welding a
tessellation and testing it for closure (:func:`weld`, :func:`mesh_topology`) is pure NumPy and
needs neither OCP nor Open3D.

Face and hole ids from :func:`inventory` are valid only for the same file bytes and the same OCCT version.
"""

from eng_toolkit.brep._files import SOLID_EXTENSIONS, read_solid, triangulate
from eng_toolkit.brep._mesh import WELD_EPS_MM, mesh_topology, weld
from eng_toolkit.brep._occ import load as require_engine
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
from eng_toolkit.errors import EngineUnavailable

__all__ = [
    "SOLID_EXTENSIONS",
    "WELD_EPS_MM",
    "EngineUnavailable",
    "Entity",
    "FaceInfo",
    "FaceRef",
    "HoleInfo",
    "HoleRef",
    "Inventory",
    "MassProperties",
    "Mesh",
    "MeshTopology",
    "PointRef",
    "Solid",
    "Vec3",
    "angle",
    "distance",
    "inventory",
    "lookup",
    "mass_properties",
    "mesh_topology",
    "read_solid",
    "require_engine",
    "triangulate",
    "wall_thickness",
    "weld",
]
