"""Geometric facts that design-for-manufacturing rules are built on: how faces meet, which faces face each other
across material, and which directions reach a face.  The rules themselves (limits, severities) live in
``domains/geometry/dfm.py``; this module only measures.

Everything here is exact B-rep geometry except :func:`face_access`, which casts rays from one sample point per face.
Edge ids (``E<n>``) are the 1-based index of the edge in ``TopExp.MapShapesAndAncestors``; like face ids they are valid
only for the same file bytes and the same OCCT version.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._query import (
    SAME_MM,
    _array,
    _faces,
    _hits,
    _load_intersector,
    _pnt,
    _r,
    _tuple,
    _unit,
)
from eng_toolkit.brep._types import Inventory, Solid, Vec3

PARALLEL = 1e-9  # |sin| of the angle below which two directions count as parallel
SMOOTH_RAD = 1e-6  # turn angle between face normals below which an edge is tangent (smooth)
GAP_MIN_MM = 1e-9  # facing faces closer than this are the same plane
SAMPLES_PER_FACE = 12  # mesh-triangle centres tried per face when looking for material across from it
CHORD_FRACTION = 1e-3  # mesh chord error of the sample-point meshing, as a fraction of the bounding-box diagonal
MAX_PAIR_TESTS = 5000  # candidate face pairs examined before the search stops and says so


@dataclass(frozen=True)
class EdgeFact:
    id: int  # E<n>
    face_ids: tuple[int, int]
    kind: str  # "line", "circle" or "other"
    convexity: str  # "convex", "concave" or "smooth" (tangent faces)
    material_angle_deg: float  # angle of the material seen across the edge: below 180 convex, above 180 concave, 180 smooth
    length_mm: float
    midpoint_mm: Vec3
    direction: Vec3  # unit tangent at the midpoint (its sign is arbitrary)
    radius_mm: float | None  # circle edges


@dataclass(frozen=True)
class CylinderFact:
    face_id: int
    sense: str  # "concave" (a bore or fillet seen from the air) or "convex" (a boss, a round)
    radius_mm: float
    axis: Vec3
    start_mm: Vec3  # centre of the first axial end, on the axis
    end_mm: Vec3
    span_rad: float
    mid_point_mm: Vec3  # on the face, half way round and half way along
    radial: Vec3  # unit vector from the axis to mid_point_mm


@dataclass(frozen=True)
class FacingPair:
    """Two faces with material between them over a stretch where a ray crosses nothing else."""

    kind: str  # "plane" (anti-parallel planes) or "cylinder" (coaxial concave and convex cylinders)
    face_ids: tuple[int, int]  # outer face first for plane pairs; (concave, convex) for cylinder pairs
    gap_mm: float  # exact: plane offset, or the difference of the radii
    point_mm: Vec3  # in the material, half way between the two faces
    inner_radius_mm: float | None = None  # cylinder pairs
    outer_radius_mm: float | None = None
    span_rad: float | None = None
    face_area_mm2: float | None = None  # plane pairs: the smaller of the two face areas (an upper bound of the overlap)


@dataclass(frozen=True)
class PairSearch:
    pairs: tuple[FacingPair, ...]
    tested: int  # candidate pairs examined
    complete: bool  # False when MAX_PAIR_TESTS stopped the search


@dataclass(frozen=True)
class FaceAccess:
    face_id: int
    point_mm: Vec3
    open_directions: tuple[str, ...]  # labels from the caller's directions that reach the point without meeting the part


def _maps(shape):
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeToFaces,
    )
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap,
    )
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer

    edge_to_faces = EdgeToFaces()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
    face_map = ShapeMap()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    face_id: dict[int, int] = {}
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while explorer.More():
        n += 1
        face_id[face_map.FindIndex(explorer.Current())] = n
        explorer.Next()
    return edge_to_faces, face_map, face_id


class _Normals:
    """Outward normals of faces at given points; the per-face OpenCASCADE helpers are built once."""

    def __init__(self, faces):
        self._faces = faces
        self._cache: dict[int, tuple] = {}

    def at(self, face_id: int, point: np.ndarray) -> np.ndarray:
        from OCP.BRep import BRep_Tool
        from OCP.BRepGProp import BRepGProp_Face
        from OCP.gp import gp_Pnt, gp_Vec
        from OCP.ShapeAnalysis import ShapeAnalysis_Surface

        if face_id not in self._cache:
            face = self._faces[face_id - 1]
            self._cache[face_id] = (ShapeAnalysis_Surface(BRep_Tool.Surface_s(face)), BRepGProp_Face(face))
        surface, props = self._cache[face_id]
        uv = surface.ValueOfUV(_pnt(point), 1e-7)
        where, normal = gp_Pnt(), gp_Vec()
        props.Normal(uv.X(), uv.Y(), where, normal)  # follows the face orientation
        vector = _array(normal)
        return vector / np.linalg.norm(vector)


def _boundary_signs(faces, edge_to_faces) -> dict[tuple[int, int], float]:
    """``(face id, edge index) -> +1`` when that face's boundary runs along the edge's own parameter direction, ``-1`` against it."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer

    signs: dict[tuple[int, int], float] = {}
    for face_id, face in enumerate(faces, start=1):
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = explorer.Current()
            signs[(face_id, int(edge_to_faces.FindIndex(edge)))] = -1.0 if edge.Orientation() == TopAbs_REVERSED else 1.0
            explorer.Next()
    return signs


def edge_facts(solid: Solid) -> tuple[EdgeFact, ...]:
    """Every edge shared by exactly two different faces, with how the faces meet there.

    Convex or concave is decided from the faces' outward normals and the boundary direction (exact); a seam
    (one face on both sides), a degenerate edge and an edge of three or more faces are left out.
    """
    _occ.load()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.GProp import GProp_GProps
    from OCP.TopoDS import TopoDS

    shape = solid._handle
    edge_to_faces, face_map, face_id = _maps(shape)
    faces = _faces(shape)
    normals = _Normals(faces)
    signs = _boundary_signs(faces, edge_to_faces)
    found: list[EdgeFact] = []
    for index in range(1, edge_to_faces.Extent() + 1):
        edge = TopoDS.Edge(edge_to_faces.FindKey(index))
        if BRep_Tool.Degenerated_s(edge):
            continue
        ids = sorted({face_id[face_map.FindIndex(f)] for f in edge_to_faces.FindFromIndex(index)})
        if len(ids) != 2:
            continue
        curve = BRepAdaptor_Curve(edge)
        middle = 0.5 * (curve.FirstParameter() + curve.LastParameter())
        point, tangent = gp_Pnt(), gp_Vec()
        curve.D1(middle, point, tangent)
        p, t = _array(point), _unit(_array(tangent))
        n1, n2 = normals.at(ids[0], p), normals.at(ids[1], p)
        t2 = t * signs[(ids[1], index)]
        inward_second = np.cross(n2, t2)
        turn = math.acos(max(-1.0, min(1.0, float(n1 @ n2))))
        side = float(inward_second @ n1)  # < 0: the second face leaves behind the first's tangent plane, i.e. a convex edge
        if turn < SMOOTH_RAD:
            convexity, angle = "smooth", 180.0
        elif side < 0.0:
            convexity, angle = "convex", 180.0 - math.degrees(turn)
        else:
            convexity, angle = "concave", 180.0 + math.degrees(turn)
        kind = {GeomAbs_Line: "line", GeomAbs_Circle: "circle"}.get(curve.GetType(), "other")
        length = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, length)
        found.append(
            EdgeFact(
                id=index,
                face_ids=(ids[0], ids[1]),
                kind=kind,
                convexity=convexity,
                material_angle_deg=_r(angle),
                length_mm=_r(length.Mass()),
                midpoint_mm=_tuple(p),
                direction=_tuple(t),
                radius_mm=_r(curve.Circle().Radius()) if kind == "circle" else None,
            )
        )
    return tuple(found)


def cylinder_facts(solid: Solid, inv: Inventory) -> tuple[CylinderFact, ...]:
    """Every cylinder face with its radius, axis, axial extent and angular span."""
    _occ.load()
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    faces = _faces(solid._handle)
    found = []
    for info in inv.faces:
        if info.kind != "cylinder" or info.sense not in ("concave", "convex"):
            continue
        surface = BRepAdaptor_Surface(faces[info.id - 1])
        line = surface.Cylinder().Axis()
        axis, base = _array(line.Direction()), _array(line.Location())
        u0, u1, v0, v1 = surface.FirstUParameter(), surface.LastUParameter(), surface.FirstVParameter(), surface.LastVParameter()
        mid = _array(surface.Value(0.5 * (u0 + u1), 0.5 * (v0 + v1)))
        radial = mid - base
        radial = radial - (radial @ axis) * axis
        found.append(
            CylinderFact(
                face_id=info.id,
                sense=info.sense,
                radius_mm=float(info.radius_mm),
                axis=_tuple(axis),
                start_mm=_tuple(base + v0 * axis),
                end_mm=_tuple(base + v1 * axis),
                span_rad=float(u1 - u0),
                mid_point_mm=_tuple(mid),
                radial=_tuple(_unit(radial)),
            )
        )
    return tuple(found)


# --------------------------------------------------------------------------- faces that face each other


class _Samples:
    """Points of planar faces: the centres of their largest mesh triangles.  The whole shape is meshed once, up front."""

    def __init__(self, shape, faces, deflection: float):
        from OCP.BRepMesh import BRepMesh_IncrementalMesh

        BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, True)
        self._faces = faces
        self._cache: dict[int, list[np.ndarray]] = {}

    def of(self, face_id: int, limit: int) -> list[np.ndarray]:
        from OCP.BRep import BRep_Tool
        from OCP.TopLoc import TopLoc_Location

        if face_id not in self._cache:
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(self._faces[face_id - 1], location)
            ranked: list[tuple[float, np.ndarray]] = []
            if mesh is not None:
                transform = location.Transformation()
                nodes = [_array(mesh.Node(i).Transformed(transform)) for i in range(1, mesh.NbNodes() + 1)]
                for i in range(1, mesh.NbTriangles() + 1):
                    p, q, r = (nodes[k - 1] for k in mesh.Triangle(i).Get())
                    ranked.append((0.5 * float(np.linalg.norm(np.cross(q - p, r - p))), (p + q + r) / 3.0))
            ranked.sort(key=lambda item: -item[0])
            self._cache[face_id] = [point for _, point in ranked]
        return self._cache[face_id][:limit]


def _inside(face, point: np.ndarray) -> bool:
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN

    return BRepClass_FaceClassifier(face, _pnt(point), 1e-7).State() == TopAbs_IN


def _axis_aligned(normal: np.ndarray) -> int | None:
    index = int(np.argmax(np.abs(normal)))
    return index if abs(normal[index]) > 1.0 - 1e-9 else None


def facing_pairs(solid: Solid, inv: Inventory, cylinders: tuple[CylinderFact, ...], *, max_gap_mm: float) -> PairSearch:
    """Pairs of faces with material between them closer than ``max_gap_mm``.

    * Planes: two planar faces with opposite outward normals, each on the other's material side, whose shadows overlap;
      the gap is the exact plane offset.  A pair is kept only when a ray from a point of the overlap, through the
      material, meets nothing before the second face.
    * Cylinders: a concave and a convex cylinder face on one axis with the same axial extent and span; the gap is the
      difference of the radii, confirmed by a radial ray that meets exactly those two surfaces first.
    """
    _occ.load()
    shape = solid._handle
    faces = _faces(shape)
    lo = np.min([f.bbox_min_mm for f in inv.faces], axis=0)
    hi = np.max([f.bbox_max_mm for f in inv.faces], axis=0)
    diagonal = float(np.linalg.norm(hi - lo))
    nudge = max(1e-6 * diagonal, 1e-5)
    deflection = max(CHORD_FRACTION * diagonal, 1e-3)
    intersector = _load_intersector(shape)
    samples = _Samples(shape, faces, deflection)
    pairs: list[FacingPair] = []
    tested = 0
    complete = True

    planes = [f for f in inv.faces if f.kind == "plane" and f.normal is not None and f.origin_mm is not None]
    by_normal: dict[tuple[float, float, float], list] = {}
    for f in planes:
        by_normal.setdefault(tuple(round(c, 6) + 0.0 for c in f.normal), []).append(f)
    for key, group in sorted(by_normal.items()):
        opposite_key = tuple(round(-c, 6) + 0.0 for c in key)
        opposite = by_normal.get(opposite_key)
        if not opposite or key > opposite_key:
            continue  # each unordered pair of normal directions once
        for a in group:
            n = np.array(a.normal)
            axis = _axis_aligned(n)
            for b in opposite:
                # b behind a (on a's material side) is also a behind b, because b's outward normal is -n; the other sign is a gap in the air
                gap = -float((np.array(b.origin_mm) - np.array(a.origin_mm)) @ n)
                if not GAP_MIN_MM < gap <= max_gap_mm:
                    continue
                if axis is not None and any(
                    a.bbox_max_mm[i] < b.bbox_min_mm[i] - SAME_MM or b.bbox_max_mm[i] < a.bbox_min_mm[i] - SAME_MM
                    for i in range(3)
                    if i != axis
                ):
                    continue
                if tested >= MAX_PAIR_TESTS:
                    complete = False
                    break
                tested += 1
                for source, target, out in ((a, b, n), (b, a, -n)):
                    wall = None
                    for point in samples.of(source.id, SAMPLES_PER_FACE):
                        if not _inside(faces[target.id - 1], point - out * gap):
                            continue  # the point's shadow misses the other face: air or another feature is across from it
                        found = _hits(intersector, point - out * nudge, -out, gap)
                        if found and abs(found[0] - (gap - nudge)) <= 1e-6:
                            wall = point - out * (gap / 2.0)
                            break
                    if wall is not None:
                        pairs.append(
                            FacingPair(
                                kind="plane",
                                face_ids=(a.id, b.id),
                                gap_mm=_r(gap),
                                point_mm=_tuple(wall),
                                face_area_mm2=_r(min(a.area_mm2, b.area_mm2)),
                            )
                        )
                        break
            if not complete:
                break
        if not complete:
            break

    concave = [c for c in cylinders if c.sense == "concave"]
    convex = [c for c in cylinders if c.sense == "convex"]
    for inner in concave:
        axis = np.array(inner.axis)
        base = np.array(inner.start_mm)
        for outer in convex:
            gap = outer.radius_mm - inner.radius_mm
            if not GAP_MIN_MM < gap <= max_gap_mm:
                continue
            outer_axis = np.array(outer.axis)
            if np.linalg.norm(np.cross(axis, outer_axis)) > PARALLEL:
                continue
            offset = np.array(outer.start_mm) - base
            if np.linalg.norm(offset - (offset @ axis) * axis) > SAME_MM:
                continue
            ends = sorted(float((np.array(p) - base) @ axis) for p in (inner.start_mm, inner.end_mm))
            outer_ends = sorted(float((np.array(p) - base) @ axis) for p in (outer.start_mm, outer.end_mm))
            if abs(ends[0] - outer_ends[0]) > SAME_MM or abs(ends[1] - outer_ends[1]) > SAME_MM:
                continue
            if abs(inner.span_rad - outer.span_rad) > 1e-6:
                continue
            if tested >= MAX_PAIR_TESTS:
                complete = False
                break
            tested += 1
            middle = np.array(inner.mid_point_mm)
            on_axis = base + ((middle - base) @ axis) * axis
            radial = np.array(inner.radial)
            found = _hits(intersector, on_axis, radial, outer.radius_mm + 1.0)
            if len(found) >= 2 and abs(found[0] - inner.radius_mm) <= 1e-6 and abs(found[1] - outer.radius_mm) <= 1e-6:
                pairs.append(
                    FacingPair(
                        kind="cylinder",
                        face_ids=(inner.face_id, outer.face_id),
                        gap_mm=_r(gap),
                        point_mm=_tuple(on_axis + radial * (inner.radius_mm + outer.radius_mm) / 2.0),
                        inner_radius_mm=_r(inner.radius_mm),
                        outer_radius_mm=_r(outer.radius_mm),
                        span_rad=_r(inner.span_rad),
                    )
                )
        if not complete:
            break
    return PairSearch(pairs=tuple(pairs), tested=tested, complete=complete)


# --------------------------------------------------------------------------- reach


def face_access(solid: Solid, inv: Inventory, directions: dict[str, Vec3], *, kinds: tuple[str, ...] = ("plane",)) -> tuple[FaceAccess, ...]:
    """For each face of the given kinds, the ``directions`` from which a point of the face is reachable in a straight line.

    The test point is the centre of the face's largest mesh triangle, moved 1e-5 of the part size out along the
    outward normal.  A direction counts when it does not point into the face (non-negative dot product with the
    normal) and the ray from the point along it leaves the part without meeting any face.  One point per face, so a
    face that is only partly shadowed counts as reachable where its sample is.
    """
    _occ.load()
    shape = solid._handle
    faces = _faces(shape)
    lo = np.min([f.bbox_min_mm for f in inv.faces], axis=0)
    hi = np.max([f.bbox_max_mm for f in inv.faces], axis=0)
    diagonal = float(np.linalg.norm(hi - lo))
    nudge = max(1e-5 * diagonal, 1e-5)
    deflection = max(CHORD_FRACTION * diagonal, 1e-3)
    reach = 2.0 * diagonal + 1.0
    intersector = _load_intersector(shape)
    samples = _Samples(shape, faces, deflection)
    unit = {label: _unit(np.array(v, dtype=np.float64)) for label, v in directions.items()}
    found = []
    for info in inv.faces:
        if info.kind not in kinds or info.normal is None:
            continue
        points = samples.of(info.id, 1)
        if not points:
            continue
        sample = points[0]
        normal = np.array(info.normal)
        start = sample + nudge * normal
        reachable = tuple(
            label for label, d in unit.items() if float(d @ normal) >= -1e-9 and not _hits(intersector, start, d, reach)
        )
        found.append(FaceAccess(face_id=info.id, point_mm=_tuple(sample), open_directions=reachable))
    return tuple(found)


__all__ = ["CylinderFact", "EdgeFact", "FaceAccess", "FacingPair", "PairSearch", "cylinder_facts", "edge_facts", "face_access", "facing_pairs"]
