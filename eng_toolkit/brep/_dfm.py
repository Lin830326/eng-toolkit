"""Geometric facts that design-for-manufacturing rules are built on: how faces meet, which faces face each other
across material, and which directions reach a face.  The rules themselves (limits, severities) live in
``domains/geometry/dfm.py``; this module only measures.

Everything here is exact B-rep geometry except :func:`face_access`, which casts rays from one sample point per face, and
:func:`normal_walls`, which samples up to 12 points per face (and refines thin pairs to an exact closest distance).
Edge ids (``E<n>``) are the 1-based index of the edge in ``TopExp.MapShapesAndAncestors``; like face ids they are valid
only for the same file bytes and the same OCCT version.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._query import (
    SAME_MM,
    _array,
    _faces,
    _hits,
    _IndexedIntersector,
    _pnt,
    _r,
    _tuple,
    _unit,
)
from eng_toolkit.brep._types import Inventory, Solid, Vec3

AXIS_SIN = 1e-6  # |sin| of the angle below which two cylinder axes count as parallel
SMOOTH_RAD = 1e-6  # turn angle between face normals below which an edge is tangent (smooth)
GAP_MIN_MM = 1e-9  # facing faces closer than this are the same plane
SAMPLES_PER_FACE = 12  # mesh-triangle centres tried per face when looking for material across from it
CHORD_FRACTION = 1e-3  # mesh chord error of the sample-point meshing, as a fraction of the bounding-box diagonal
MAX_PAIR_TESTS = 5000  # candidate face pairs examined before the search stops and says so
# Two cylinders are on one axis when their axes are closer than this fraction of the wall between them (and at least
# SAME_MM): 3e-3 mm for a 3 mm sheet.  A bend drawn through rounded 3-point arcs (via points to 4 decimals) has its inner
# and outer cylinders about 7e-5 mm apart; a sheet's own thickness tolerance is a hundred times larger than this.
COAXIAL_REL = 1e-3
WALL_SAMPLES_PER_FACE = 12  # spread sample points per face for the wall rays along the inward normal
MAX_WALL_RAYS = 40000  # wall rays cast before the search stops and says so
MAX_WALL_REFINEMENTS = 200  # thin face pairs whose exact closest distance is computed
PERPENDICULAR_DEG = 1.0  # a closest-distance segment is a wall thickness when it is this close to both face normals


def coaxial_tolerance_mm(gap_mm: float) -> float:
    """How far apart two cylinder axes (or two wall gaps) may be and still count as one: :data:`COAXIAL_REL` of the gap."""
    return max(SAME_MM, COAXIAL_REL * abs(gap_mm))


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
    normal: Vec3 | None = None  # the face's outward normal
    clearance_mm: float | None = None  # air along the outward normal from the point to the next face; None: open to the outside


@dataclass(frozen=True)
class NormalWall:
    """The thinnest material found between two faces by rays cast along the inward normal of sample points."""

    face_ids: tuple[int, int]  # the face the ray started on, the face it left the material through (the same for a pin)
    thickness_mm: float  # the exact closest distance when ``exact``, else the shortest ray chord
    point_mm: Vec3  # in the material, half way along that distance
    exact: bool  # True: an exact closest distance whose segment is normal to both faces (to PERPENDICULAR_DEG)
    chord_mm: float  # the shortest ray chord along the inward normal (an upper bound of the local wall)


@dataclass(frozen=True)
class NormalWallSearch:
    walls: tuple[NormalWall, ...]  # one per face pair whose shortest chord is within the reach
    rays: int  # rays cast
    faces_sampled: int
    faces_without_samples: tuple[int, ...]  # faces the mesher gave no triangle (not measured)
    complete: bool  # False when MAX_WALL_RAYS stopped the search


def _maps(shape, *, deadline: Callable[[str], None] | None = None):
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
        if deadline is not None:
            deadline("edges")
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
        return self.surface_point(face_id, point)[1]

    def surface_point(self, face_id: int, point: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(the point projected onto the face's surface, the outward unit normal there)``."""
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
        return _array(where), vector / np.linalg.norm(vector)


def _boundary_signs(faces, edge_to_faces, *, deadline: Callable[[str], None] | None = None) -> dict[tuple[int, int], float]:
    """``(face id, edge index) -> +1`` when that face's boundary runs along the edge's own parameter direction, ``-1`` against it."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer

    signs: dict[tuple[int, int], float] = {}
    for face_id, face in enumerate(faces, start=1):
        if deadline is not None:
            deadline("edges")
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = explorer.Current()
            signs[(face_id, int(edge_to_faces.FindIndex(edge)))] = -1.0 if edge.Orientation() == TopAbs_REVERSED else 1.0
            explorer.Next()
    return signs


def edge_facts(solid: Solid, *, deadline: Callable[[str], None] | None = None) -> tuple[EdgeFact, ...]:
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
    if deadline is not None:
        deadline("edges")
    edge_to_faces, face_map, face_id = _maps(shape, deadline=deadline)
    faces = _faces(shape)
    normals = _Normals(faces)
    signs = _boundary_signs(faces, edge_to_faces, deadline=deadline)
    found: list[EdgeFact] = []
    for index in range(1, edge_to_faces.Extent() + 1):
        if deadline is not None:
            deadline("edges")
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
    if deadline is not None:
        deadline("edges")
    return tuple(found)


def face_extents(solid: Solid, requests: list[tuple[int, Vec3, Vec3]]) -> list[tuple[float, float]]:
    """For each ``(face id, origin, direction)``: the smallest and largest ``(p - origin) . direction`` over the face's
    boundary, its vertices and 16 points along every curved edge (exact for straight edges, within the sampling for
    curved ones).  Tells on which side of a line in its plane a face lies."""
    _occ.load()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces = _faces(solid._handle)
    points: dict[int, np.ndarray] = {}
    found = []
    for face_id, origin, direction in requests:
        if face_id not in points:
            boundary = []
            explorer = TopExp_Explorer(faces[face_id - 1], TopAbs_EDGE)
            while explorer.More():
                edge = TopoDS.Edge(explorer.Current())
                explorer.Next()
                if BRep_Tool.Degenerated_s(edge):
                    continue
                curve = BRepAdaptor_Curve(edge)
                first, last = curve.FirstParameter(), curve.LastParameter()
                count = 2 if curve.GetType() == GeomAbs_Line else 17
                boundary.extend(_array(curve.Value(first + (last - first) * k / (count - 1))) for k in range(count))
            points[face_id] = np.array(boundary) if boundary else np.zeros((0, 3))
        cloud = points[face_id]
        if not len(cloud):
            found.append((0.0, 0.0))
            continue
        along = (cloud - np.array(origin)) @ _unit(np.array(direction, dtype=np.float64))
        found.append((float(along.min()), float(along.max())))
    return found


def cylinder_facts(solid: Solid, inv: Inventory, *, deadline: Callable[[str], None] | None = None) -> tuple[CylinderFact, ...]:
    """Every cylinder face with its radius, axis, axial extent and angular span."""
    _occ.load()
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    faces = _faces(solid._handle)
    found = []
    for info in inv.faces:
        if deadline is not None:
            deadline("cylinders")
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
    if deadline is not None:
        deadline("cylinders")
    return tuple(found)


# --------------------------------------------------------------------------- faces that face each other


class _Samples:
    """Points of planar faces: the centres of their largest mesh triangles.  The whole shape is meshed once, up front."""

    def __init__(self, shape, faces, deflection: float, *, deadline=None):
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from eng_toolkit.brep._mesh_process import needs_isolation, isolated_mesh

        if needs_isolation(shape):
            faces = _faces(isolated_mesh(shape, deflection, deadline=deadline))
        else:
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
            if mesh is not None and mesh.NbTriangles():
                transform = location.Transformation()
                nodes = np.array([_array(mesh.Node(i).Transformed(transform)) for i in range(1, mesh.NbNodes() + 1)])
                triangles = np.array([mesh.Triangle(i).Get() for i in range(1, mesh.NbTriangles() + 1)]) - 1
                p, q, r = (nodes[triangles[:, i]] for i in range(3))
                cross = np.cross(q - p, r - p)
                centres = (p + q + r) / 3.0
                # Keep the scalar norm and stable sort: changing their rounding can reorder equal-area samples.
                ranked = [(0.5 * float(np.linalg.norm(vector)), point) for vector, point in zip(cross, centres)]
            ranked.sort(key=lambda item: -item[0])
            self._cache[face_id] = [point for _, point in ranked]
        return self._cache[face_id][:limit]

    def spread(self, face_id: int, limit: int) -> list[np.ndarray]:
        """Up to ``limit`` triangle centres spread over the face: the largest triangle's first, then each time the centre
        farthest from those already taken (so a hole wall is sampled all the way round, not on one side)."""
        points = self.of(face_id, 4000)
        if len(points) <= limit:
            return list(points)
        cloud = np.array(points)
        chosen = [0]
        nearest = np.linalg.norm(cloud - cloud[0], axis=1)
        while len(chosen) < limit:
            index = int(np.argmax(nearest))
            if nearest[index] <= 0.0:
                break
            chosen.append(index)
            nearest = np.minimum(nearest, np.linalg.norm(cloud - cloud[index], axis=1))
        return [points[i] for i in chosen]


def _inside(face, point: np.ndarray) -> bool:
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN

    return BRepClass_FaceClassifier(face, _pnt(point), 1e-7).State() == TopAbs_IN


def _axis_aligned(normal: np.ndarray) -> int | None:
    index = int(np.argmax(np.abs(normal)))
    return index if abs(normal[index]) > 1.0 - 1e-9 else None


def facing_pairs(
    solid: Solid, inv: Inventory, cylinders: tuple[CylinderFact, ...], *, max_gap_mm: float,
    deadline: Callable[[str], None] | None = None,
) -> PairSearch:
    """Pairs of faces with material between them closer than ``max_gap_mm``.

    * Planes: two planar faces with opposite outward normals, each on the other's material side, whose shadows overlap;
      the gap is the exact plane offset.  A pair is kept only when a ray from a point of the overlap, through the
      material, meets nothing before the second face.
    * Cylinders: a concave and a convex cylinder face on one axis with overlapping axial and angular ranges; the gap is the
      difference of the radii, confirmed by a radial ray that meets exactly those two surfaces first.  "One axis" and the
      ray allow :func:`coaxial_tolerance_mm` of the gap (axes from rounded 3-point arcs are 7e-5 mm apart).
    """
    _occ.load()
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier

    if deadline is not None:
        deadline("pairs")
    shape = solid._handle
    faces = _faces(shape)
    lo = np.min([f.bbox_min_mm for f in inv.faces], axis=0)
    hi = np.max([f.bbox_max_mm for f in inv.faces], axis=0)
    diagonal = float(np.linalg.norm(hi - lo))
    deflection = max(CHORD_FRACTION * diagonal, 1e-3)
    intersector = _IndexedIntersector(shape, deadline=deadline, phase="pairs")
    classifier = BRepClass3d_SolidClassifier(shape)
    samples = _Samples(shape, faces, deflection, deadline=deadline)
    if deadline is not None:
        deadline("pairs")
    pairs: list[FacingPair] = []
    tested = 0
    complete = True

    planes = [f for f in inv.faces if f.kind == "plane" and f.normal is not None and f.origin_mm is not None]
    by_normal: dict[tuple[float, float, float], list] = {}
    for f in planes:
        if deadline is not None:
            deadline("pairs")
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
                if deadline is not None:
                    deadline("pairs")
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
                nudge = min(1e-5, gap * 1e-4)
                for source, target, out in ((a, b, n), (b, a, -n)):
                    wall = None
                    for point in samples.of(source.id, SAMPLES_PER_FACE):
                        if deadline is not None:
                            deadline("pairs")
                        if not _inside(faces[target.id - 1], point - out * gap):
                            continue  # the point's shadow misses the other face: air or another feature is across from it
                        if not _in_material(classifier, point - out * nudge):
                            continue
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
            if deadline is not None:
                deadline("pairs")
            gap = outer.radius_mm - inner.radius_mm
            if not GAP_MIN_MM < gap <= max_gap_mm:
                continue
            outer_axis = np.array(outer.axis)
            if np.linalg.norm(np.cross(axis, outer_axis)) > AXIS_SIN:
                continue
            tolerance = coaxial_tolerance_mm(gap)
            offset = np.array(outer.start_mm) - base
            if np.linalg.norm(offset - (offset @ axis) * axis) > tolerance:
                continue
            ends = sorted(float((np.array(p) - base) @ axis) for p in (inner.start_mm, inner.end_mm))
            outer_ends = sorted(float((np.array(p) - base) @ axis) for p in (outer.start_mm, outer.end_mm))
            low, high = max(ends[0], outer_ends[0]), min(ends[1], outer_ends[1])
            if high - low <= SAME_MM:
                continue
            if tested >= MAX_PAIR_TESTS:
                complete = False
                break
            tested += 1
            on_axis = base + (low + high) / 2 * axis
            for radial, span in _angular_overlap(inner, outer):
                inside = on_axis + radial * inner.radius_mm
                outside = on_axis + radial * outer.radius_mm
                if not (_inside(faces[inner.face_id - 1], inside) and _inside(faces[outer.face_id - 1], outside)):
                    continue
                nudge = min(1e-5, gap * 1e-4)
                start = inside + nudge * radial
                if not _in_material(classifier, start):
                    continue
                found = _hits(intersector, start, radial, gap + tolerance)
                if not found or abs(found[0] - (gap - nudge)) > tolerance:
                    continue
                pairs.append(
                    FacingPair(
                        kind="cylinder",
                        face_ids=(inner.face_id, outer.face_id),
                        gap_mm=_r(gap),
                        point_mm=_tuple(on_axis + radial * (inner.radius_mm + outer.radius_mm) / 2.0),
                        inner_radius_mm=_r(inner.radius_mm),
                        outer_radius_mm=_r(outer.radius_mm),
                        span_rad=_r(span),
                    )
                )
                break
        if not complete:
            break
    if deadline is not None:
        deadline("pairs")
    return PairSearch(pairs=tuple(pairs), tested=tested, complete=complete)


def _in_material(classifier, point):
    from OCP.TopAbs import TopAbs_IN

    classifier.Perform(_pnt(point), 1e-7)
    return classifier.State() == TopAbs_IN


def normal_walls(
    solid: Solid,
    inv: Inventory,
    *,
    max_gap_mm: float,
    refine_below_mm: float,
    skip_faces: frozenset[int] = frozenset(),
    deadline: Callable[[str], None] | None = None,
) -> NormalWallSearch:
    """Local wall thickness along the inward normal of every face, for walls that are not parallel planes or coaxial
    cylinders (tapered fins, a hole near a face, cones, free-form faces).

    From up to :data:`WALL_SAMPLES_PER_FACE` sample points per face (mesh-triangle centres spread over the face and
    projected onto it) a ray runs from just inside the material along the inward normal; the face where it leaves the
    material is the face across, the ray's length in the material is the chord.  The shortest chord of each face pair
    within ``max_gap_mm`` is kept.  The pairs whose chord is at most ``refine_below_mm`` (and the shortest pair of all)
    are confirmed, the middle of the chord must be in the material, and refined: when the exact closest distance between
    the two faces (``BRepExtrema``) runs along both face normals (within :data:`PERPENDICULAR_DEG`) and through the
    material it replaces the chord and the wall is ``exact``.  Faces in ``skip_faces`` cast no rays (they were measured
    exactly by :func:`facing_pairs`); rays from other faces still meet them.
    """
    _occ.load()
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier

    if deadline is not None:
        deadline("pairs")
    shape = solid._handle
    faces = _faces(shape)
    lo = np.min([f.bbox_min_mm for f in inv.faces], axis=0)
    hi = np.max([f.bbox_max_mm for f in inv.faces], axis=0)
    deflection = max(CHORD_FRACTION * float(np.linalg.norm(hi - lo)), 1e-3)
    intersector = _IndexedIntersector(shape, deadline=deadline, phase="pairs")
    classifier = BRepClass3d_SolidClassifier(shape)
    samples = _Samples(shape, faces, deflection, deadline=deadline)
    normals = _Normals(faces)
    nudge = min(1e-5, max_gap_mm * 1e-4)
    best: dict[tuple[int, int], tuple[float, np.ndarray]] = {}
    rays = sampled = 0
    missing: list[int] = []
    complete = True
    for info in inv.faces:
        if deadline is not None:
            deadline("pairs")
        if info.id in skip_faces:
            continue
        points = samples.spread(info.id, WALL_SAMPLES_PER_FACE)
        if not points:
            missing.append(info.id)
            continue
        sampled += 1
        for point in points:
            if rays >= MAX_WALL_RAYS:
                complete = False
                break
            try:
                on, normal = normals.surface_point(info.id, point)
            except Exception:  # OCCT cannot project onto a degenerate patch of the face: no ray from this point
                continue
            start = on - nudge * normal
            rays += 1
            hit = intersector.first_hit(start, -normal, max_gap_mm)
            if hit is None or hit[0] <= SAME_MM:
                continue  # no face within reach, or the ray grazed the face it started from
            distance, other = hit
            key = (min(info.id, other), max(info.id, other))
            chord = distance + nudge
            if key not in best or chord < best[key][0]:
                best[key] = (chord, start - normal * (distance / 2.0))
        if not complete:
            break
    walls: list[NormalWall] = []
    refined = 0
    for rank, (key, (chord, middle)) in enumerate(sorted(best.items(), key=lambda item: (item[1][0], item[0]))):
        if deadline is not None:
            deadline("pairs")
        thickness, point, exact = chord, middle, False
        if (chord <= refine_below_mm or rank == 0) and refined < MAX_WALL_REFINEMENTS:
            refined += 1
            if not _in_material(classifier, middle):
                continue  # the chord is not material: not a wall
            if key[0] != key[1]:
                closest = _closest_wall(faces, normals, classifier, key)
                if closest is not None and closest[0] <= chord + SAME_MM:
                    thickness, point, exact = closest[0], closest[1], True
        walls.append(NormalWall(face_ids=key, thickness_mm=_r(thickness), point_mm=_tuple(point), exact=exact, chord_mm=_r(chord)))
    if deadline is not None:
        deadline("pairs")
    return NormalWallSearch(
        walls=tuple(walls), rays=rays, faces_sampled=sampled, faces_without_samples=tuple(missing), complete=complete
    )


def _closest_wall(faces, normals: _Normals, classifier, key: tuple[int, int]) -> tuple[float, np.ndarray] | None:
    """The exact closest distance between two faces when its segment is a wall thickness: normal to both faces and through
    the material.  ``(distance, middle of the segment)`` or None."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    a, b = key
    try:
        extrema = BRepExtrema_DistShapeShape(faces[a - 1], faces[b - 1])
    except Exception:  # OCCT failed on this face pair: keep the chord, which is a measured value too
        return None
    if not extrema.IsDone():
        return None
    limit = math.cos(math.radians(PERPENDICULAR_DEG))
    found: tuple[float, np.ndarray] | None = None
    for index in range(1, extrema.NbSolution() + 1):
        p, q = _array(extrema.PointOnShape1(index)), _array(extrema.PointOnShape2(index))
        length = float(np.linalg.norm(q - p))
        if length <= GAP_MIN_MM:
            continue
        along = (q - p) / length
        try:
            _, at_p = normals.surface_point(a, p)
            _, at_q = normals.surface_point(b, q)
        except Exception:  # no normal at that point (a degenerate patch): not a confirmed wall
            continue
        if -float(along @ at_p) < limit or float(along @ at_q) < limit:
            continue  # the segment does not leave both faces into the material along their normals: an edge, not a wall
        middle = (p + q) / 2.0
        if not _in_material(classifier, middle):
            continue
        if found is None or length < found[0]:
            found = (length, middle)
    return found


def _angular_overlap(inner: CylinderFact, outer: CylinderFact):
    """Interior directions in the common angular intervals, including seam wrapping."""
    if inner.span_rad >= 2 * math.pi - 1e-6:
        return [(np.array(outer.radial), outer.span_rad)]
    if outer.span_rad >= 2 * math.pi - 1e-6:
        return [(np.array(inner.radial), inner.span_rad)]
    first = np.array(inner.radial)
    second = np.cross(np.array(inner.axis), first)
    angle = math.atan2(float(second @ outer.radial), float(first @ outer.radial))
    found = []
    for shift in (-2 * math.pi, 0.0, 2 * math.pi):
        low = max(-inner.span_rad / 2, angle + shift - outer.span_rad / 2)
        high = min(inner.span_rad / 2, angle + shift + outer.span_rad / 2)
        if high - low > 1e-6:
            mid = (low + high) / 2
            found.append((math.cos(mid) * first + math.sin(mid) * second, high - low))
    return found


# --------------------------------------------------------------------------- reach


def face_access(
    solid: Solid, inv: Inventory, directions: dict[str, Vec3], *, kinds: tuple[str, ...] = ("plane",),
    deadline: Callable[[str], None] | None = None,
) -> tuple[FaceAccess, ...]:
    """For each face of the given kinds, the ``directions`` from which a point of the face is reachable in a straight line.

    The test point is the centre of the face's largest mesh triangle, moved 1e-5 of the part size out along the
    outward normal.  A direction counts when it does not point into the face (non-negative dot product with the
    normal) and the ray from the point along it leaves the part without meeting any face.  One point per face, so a
    face that is only partly shadowed counts as reachable where its sample is.  ``clearance_mm`` is the air from the
    point along the outward normal to the next face (the width of a slot in front of a slot wall), None when that ray
    leaves the part.
    """
    _occ.load()
    if deadline is not None:
        deadline("access")
    shape = solid._handle
    faces = _faces(shape)
    lo = np.min([f.bbox_min_mm for f in inv.faces], axis=0)
    hi = np.max([f.bbox_max_mm for f in inv.faces], axis=0)
    diagonal = float(np.linalg.norm(hi - lo))
    nudge = max(1e-5 * diagonal, 1e-5)
    deflection = max(CHORD_FRACTION * diagonal, 1e-3)
    reach = 2.0 * diagonal + 1.0
    intersector = _IndexedIntersector(shape, deadline=deadline, phase="access")
    samples = _Samples(shape, faces, deflection, deadline=deadline)
    unit = {label: _unit(np.array(v, dtype=np.float64)) for label, v in directions.items()}
    found = []
    for info in inv.faces:
        if deadline is not None:
            deadline("access")
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
        across = _hits(intersector, start, normal, reach)
        found.append(
            FaceAccess(
                face_id=info.id,
                point_mm=_tuple(sample),
                open_directions=reachable,
                normal=_tuple(normal),
                clearance_mm=_r(across[0] + nudge) if across else None,
            )
        )
    if deadline is not None:
        deadline("access")
    return tuple(found)


__all__ = [
    "CylinderFact",
    "EdgeFact",
    "FaceAccess",
    "FacingPair",
    "NormalWall",
    "NormalWallSearch",
    "PairSearch",
    "coaxial_tolerance_mm",
    "cylinder_facts",
    "edge_facts",
    "face_access",
    "face_extents",
    "facing_pairs",
    "normal_walls",
]
