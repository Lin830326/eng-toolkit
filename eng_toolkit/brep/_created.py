"""What a feature created: the edges and faces a part gained from it, for the ``of:<feature>`` selector term.

No history of the kernel's booleans is used (it would have to be composed across unify, fillet and shell).  The rule is
geometric and is applied when the feature is built: an edge (or face) of the part after the feature belongs to it when all
its sample points lie on the boundary of the feature's tool shape and not all of them lie on an edge (or face) the part
already had.  A later feature that splits or trims an edge leaves pieces that still lie on the recorded one, so
:meth:`Created.edge_flags` and :meth:`Created.face_flags` match by containment in the recorded geometry.  Edges that a
later fillet or boolean replaces no longer match.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._query import _array, _bbox, _faces, _pnt
from eng_toolkit.brep._types import Solid

TOL_MM = 1e-5  # a sample point lies on a shape when it is this close to it
EDGE_SAMPLES = 7
FACE_GRID = 3


def _compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def unique_edges(shape) -> list:
    """The distinct, non-degenerate edges of a shape in the kernel's order."""
    from OCP.BRep import BRep_Tool
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    table = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, table)
    found = []
    for i in range(1, int(table.Extent()) + 1):
        edge = TopoDS.Edge(table.FindKey(i))
        if not BRep_Tool.Degenerated_s(edge):
            found.append(edge)
    return found


def edge_points(edge) -> np.ndarray:
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    return np.array([_array(curve.Value(first + (last - first) * k / (EDGE_SAMPLES - 1))) for k in range(EDGE_SAMPLES)])


def face_points(face) -> np.ndarray:
    """Interior points on a grid of the face's parameter box, then the sample points of its edges."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS

    points = []
    surface = BRep_Tool.Surface_s(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    for i in range(1, FACE_GRID + 1):
        for j in range(1, FACE_GRID + 1):
            u, v = u0 + (u1 - u0) * i / (FACE_GRID + 1), v0 + (v1 - v0) * j / (FACE_GRID + 1)
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-7).State() == TopAbs_IN:
                points.append(_array(surface.Value(u, v)))
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        points.append(edge_points(edge)[EDGE_SAMPLES // 2])
        explorer.Next()
    return np.array(points)


def _near(points: np.ndarray, shape) -> bool:
    """True when every point is within ``TOL_MM`` of ``shape``."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    for p in points:
        vertex = BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex()
        measure = BRepExtrema_DistShapeShape(vertex, shape)
        if not measure.IsDone() or measure.Value() > TOL_MM:
            return False
    return True


def _inside_box(points: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> bool:
    return bool((points >= lo - TOL_MM).all() and (points <= hi + TOL_MM).all())


@dataclass(frozen=True, eq=False)
class Created:
    """The recorded edges and faces of one feature (kernel handles; they never leave the package)."""

    edges: tuple = ()
    faces: tuple = ()

    def merge(self, other: "Created") -> "Created":
        return Created(self.edges + other.edges, self.faces + other.faces)

    def edge_flags(self, edges: list) -> list[bool]:
        """For each edge: do all its sample points lie on one of the recorded edges?"""
        return _flags(edges, self.edges, edge_points)

    def face_flags(self, faces: list) -> list[bool]:
        return _flags(faces, self.faces, face_points)


def _flags(candidates: list, recorded: tuple, sampler) -> list[bool]:
    if not recorded:
        return [False] * len(candidates)
    compound = _compound(recorded)
    lo, hi = (np.array(v) for v in _bbox(compound))
    out = []
    for item in candidates:
        points = sampler(item)
        out.append(_inside_box(points, lo, hi) and _near(points, compound))
    return out


def created_by(before: Solid | None, after: Solid, tool: Solid) -> Created:
    """The edges and faces of ``after`` that lie on the boundary of ``tool`` and were not already on ``before``."""
    _occ.load()
    tool_faces = _compound(_faces(tool._handle))
    lo, hi = (np.array(v) for v in _bbox(tool._handle))
    old_edges = None if before is None else _compound(unique_edges(before._handle))
    old_faces = None if before is None else _compound(_faces(before._handle))
    edges = []
    for edge in unique_edges(after._handle):
        points = edge_points(edge)
        if _inside_box(points, lo, hi) and _near(points, tool_faces) and (old_edges is None or not _near(points, old_edges)):
            edges.append(edge)
    faces = []
    for face in _faces(after._handle):
        points = face_points(face)
        if len(points) and _inside_box(points, lo, hi) and _near(points, tool_faces) and (old_faces is None or not _near(points, old_faces)):
            faces.append(face)
    return Created(tuple(edges), tuple(faces))
