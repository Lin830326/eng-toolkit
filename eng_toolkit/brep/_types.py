from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Vec3 = tuple[float, float, float]
FaceKind = Literal["plane", "cylinder", "cone", "sphere", "torus", "bspline", "other"]
FaceSense = Literal["flat", "convex", "concave", "mixed"]


@dataclass(frozen=True, eq=False)
class Solid:
    """A B-rep in millimetres.  ``_handle`` is the OCP ``TopoDS_Shape``; it never leaves this package."""

    _handle: object = field(repr=False)
    solids: int
    faces: int
    roots_in_file: int = 1  # root entities the STEP file offers for transfer
    roots_transferred: int = 1  # roots that produced at least one face


@dataclass(frozen=True, eq=False)
class Mesh:
    """Per-face triangles; the nodes of neighbouring faces are not shared (not welded)."""

    vertices: np.ndarray  # (n, 3) float64, millimetres
    triangles: np.ndarray  # (m, 3) int32, counter-clockwise seen from outside
    deflection_mm: float  # chord error bound the tessellation was asked for


@dataclass(frozen=True)
class MassProperties:
    volume_mm3: float | None  # None unless the shape holds a closed solid of positive volume
    area_mm2: float
    centroid_mm: Vec3  # of the volume, or of the surface when there is no volume
    bbox_min_mm: Vec3  # exact (BRepBndLib.AddOptimal), not OCCT's loose box
    bbox_max_mm: Vec3


@dataclass(frozen=True)
class FaceInfo:
    id: int  # 1-based TopExp_Explorer order: stable for one file's bytes and one OCCT version
    kind: FaceKind
    area_mm2: float
    sense: FaceSense  # cylinder, cone, sphere: does the outward normal point away from the axis or centre (convex) or towards it
    normal: Vec3 | None  # plane: outward unit normal
    axis: Vec3 | None  # cylinder, cone, torus: unit direction, its sign is arbitrary
    origin_mm: Vec3 | None  # plane or axis: a point on it; sphere: the centre
    radius_mm: float | None  # cylinder, sphere
    bbox_min_mm: Vec3
    bbox_max_mm: Vec3


@dataclass(frozen=True)
class HoleInfo:
    id: str  # "H1"...: ordered by axis, diameter, entry point
    face_ids: tuple[int, ...]
    diameter_mm: float
    axis: Vec3  # drilling direction, into the material
    entry_mm: Vec3
    depth_mm: float  # cylindrical depth, without a drill-point cone
    thru: bool
    counterbore: tuple[float, float] | None = None  # (diameter, depth); not detected yet
    countersink: tuple[float, float] | None = None  # (diameter, included angle); not detected yet


@dataclass(frozen=True)
class Inventory:
    faces: tuple[FaceInfo, ...]
    holes: tuple[HoleInfo, ...]
    edges: int
    vertices: int


@dataclass(frozen=True)
class FaceRef:
    id: int


@dataclass(frozen=True)
class HoleRef:
    id: str


@dataclass(frozen=True)
class PointRef:
    xyz_mm: Vec3


Entity = FaceRef | HoleRef | PointRef


@dataclass(frozen=True)
class MeshTopology:
    """What :func:`eng_toolkit.brep.mesh_topology` found; see there for the exact definitions."""

    edge_manifold_closed: bool  # every edge is shared by two triangles, one in each direction
    vertex_manifold: bool  # the triangles around every vertex form one fan

    @property
    def watertight(self) -> bool:
        return self.edge_manifold_closed and self.vertex_manifold
