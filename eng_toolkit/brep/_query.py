"""Questions asked of a ``Solid``: mass properties, the face and hole inventory, distances, angles and wall thickness."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._types import (
    Entity,
    FaceInfo,
    FaceRef,
    HoleInfo,
    HoleRef,
    Inventory,
    MassProperties,
    PointRef,
    Solid,
    Vec3,
)
from eng_toolkit.errors import ToolFailure

TWO_PI = 2.0 * math.pi
DIGITS = 9
SAME_MM = 1e-6  # lengths closer than this are the same place
AXIS_PARALLEL = 1.0 - 1e-12  # |cos| of two axes that are the same direction
HIT_TOLERANCE_MM = 1e-7
RIM_RAY_FRACTIONS = (0.5, 0.7, 0.9)  # end rays run these fractions of the radius off the axis
WALL_RAY_DIRECTIONS = 3  # radial rays from the axis that must meet the wall at the radius for a cylinder to be a hole


def _r(value: float) -> float:
    return round(float(value), DIGITS) + 0.0  # + 0.0 turns -0.0 into 0.0


def _vec(v) -> Vec3:
    return (_r(v.X()), _r(v.Y()), _r(v.Z()))


def _tuple(v: np.ndarray) -> Vec3:
    return (_r(v[0]), _r(v[1]), _r(v[2]))


def _pnt(v):
    from OCP.gp import gp_Pnt

    return gp_Pnt(float(v[0]), float(v[1]), float(v[2]))


def _dir(v):
    from OCP.gp import gp_Dir

    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _array(v) -> np.ndarray:
    return np.array([v.X(), v.Y(), v.Z()], dtype=np.float64)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _perpendicular(axis: np.ndarray) -> np.ndarray:
    helper = np.zeros(3)
    helper[int(np.argmin(np.abs(axis)))] = 1.0
    return _unit(np.cross(axis, helper))


# --------------------------------------------------------------------------- shape access


def _faces(shape) -> list:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        found.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return found


def _bbox(shape) -> tuple[Vec3, Vec3]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)  # not the triangulation, not the tolerance: the exact box
    if box.IsVoid():
        raise ToolFailure("input.invalid", "the shape has no extent")
    return _vec(box.CornerMin()), _vec(box.CornerMax())


def _unique_count(shape, kind) -> int:
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
    from OCP.TopExp import TopExp

    found = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, kind, found)
    return int(found.Extent())


# --------------------------------------------------------------------------- mass properties


def mass_properties(solid: Solid) -> MassProperties:
    """Exact B-rep properties.  ``volume_mm3`` is None unless the shape holds a closed solid of positive volume."""
    _occ.load()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    shape = solid._handle
    surface = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface)
    volume: float | None = None
    centre = surface.CentreOfMass()
    if solid.solids > 0:
        measured = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, measured)
        if measured.Mass() > 0.0:
            volume = float(measured.Mass())
            centre = measured.CentreOfMass()
    lo, hi = _bbox(shape)
    return MassProperties(
        volume_mm3=volume,
        area_mm2=float(surface.Mass()),
        centroid_mm=_vec(centre),
        bbox_min_mm=lo,
        bbox_max_mm=hi,
    )


# --------------------------------------------------------------------------- inventory


@dataclass(frozen=True)
class _Cylinder:
    """What hole detection needs of one concave cylinder face."""

    face_id: int
    axis: np.ndarray
    location: np.ndarray
    radius: float
    span: float  # angle covered, radians
    v0: float  # axial extent along ``axis`` from ``location``
    v1: float


@dataclass
class _Candidate:
    """Concave cylinder faces on one axis line, with one radius and one axial extent: the pieces of one bore."""

    origin: np.ndarray
    axis: np.ndarray
    radius: float
    t0: float
    t1: float
    span: float = 0.0
    face_ids: list[int] = field(default_factory=list)

    def interval_of(self, c: _Cylinder) -> tuple[float, float]:
        sign = 1.0 if float(c.axis @ self.axis) >= 0.0 else -1.0
        base = float((c.location - self.origin) @ self.axis)
        a, b = base + sign * c.v0, base + sign * c.v1
        return min(a, b), max(a, b)

    def accepts(self, c: _Cylinder) -> bool:
        if abs(float(c.axis @ self.axis)) < AXIS_PARALLEL or abs(c.radius - self.radius) > SAME_MM:
            return False
        offset = c.location - self.origin
        if np.linalg.norm(offset - (offset @ self.axis) * self.axis) > SAME_MM:
            return False
        t0, t1 = self.interval_of(c)
        return abs(t0 - self.t0) <= SAME_MM and abs(t1 - self.t1) <= SAME_MM


def _kind_of(surface_type) -> str:
    from OCP.GeomAbs import (
        GeomAbs_BezierSurface,
        GeomAbs_BSplineSurface,
        GeomAbs_Cone,
        GeomAbs_Cylinder,
        GeomAbs_Plane,
        GeomAbs_Sphere,
        GeomAbs_Torus,
    )

    for known, name in (
        (GeomAbs_Plane, "plane"),
        (GeomAbs_Cylinder, "cylinder"),
        (GeomAbs_Cone, "cone"),
        (GeomAbs_Sphere, "sphere"),
        (GeomAbs_Torus, "torus"),
        (GeomAbs_BSplineSurface, "bspline"),
        (GeomAbs_BezierSurface, "bspline"),
    ):
        if surface_type == known:
            return name
    return "other"


def _outward_normal(face) -> tuple[np.ndarray, np.ndarray] | None:
    """A point of the face (mid-parameter) and the unit normal that points out of the material there."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec

    props = BRepGProp_Face(face)
    u0, u1, v0, v1 = props.Bounds()
    point, normal = gp_Pnt(), gp_Vec()
    props.Normal((u0 + u1) / 2.0, (v0 + v1) / 2.0, point, normal)  # follows the face orientation
    vector = _array(normal)
    length = np.linalg.norm(vector)
    if not math.isfinite(length) or length < 1e-12:
        return None
    return _array(point), vector / length


def _sense_from(centre_line_point: np.ndarray, centre_line_axis: np.ndarray | None, face_point: np.ndarray, normal: np.ndarray) -> str:
    away = face_point - centre_line_point
    if centre_line_axis is not None:
        away = away - (away @ centre_line_axis) * centre_line_axis
    if np.linalg.norm(away) < 1e-12:
        return "mixed"
    return "convex" if float(normal @ away) > 0.0 else "concave"


def _describe(face_id: int, face) -> tuple[FaceInfo, _Cylinder | None]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    surface = BRepAdaptor_Surface(face)
    kind = _kind_of(surface.GetType())
    area = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, area)
    lo, hi = _bbox(face)
    located = _outward_normal(face)

    sense = "mixed"
    normal = axis = origin = None
    radius: float | None = None
    cylinder: _Cylinder | None = None
    if kind == "plane":
        sense = "flat"
        origin = _vec(surface.Plane().Location())
        normal = _tuple(located[1]) if located else None
    elif kind in ("cylinder", "cone", "torus"):
        geometry = {"cylinder": surface.Cylinder, "cone": surface.Cone, "torus": surface.Torus}[kind]()
        line = geometry.Axis()
        axis_vec, base = _array(line.Direction()), _array(line.Location())
        axis, origin = _tuple(axis_vec), _vec(line.Location())
        if kind == "cylinder":
            radius = float(geometry.Radius())
        if kind != "torus" and located:
            sense = _sense_from(base, axis_vec, located[0], located[1])
        if kind == "cylinder":
            cylinder = _Cylinder(
                face_id=face_id,
                axis=axis_vec,
                location=base,
                radius=radius,
                span=float(surface.LastUParameter() - surface.FirstUParameter()),
                v0=float(surface.FirstVParameter()),
                v1=float(surface.LastVParameter()),
            )
    elif kind == "sphere":
        sphere = surface.Sphere()
        radius = float(sphere.Radius())
        origin = _vec(sphere.Location())
        if located:
            sense = _sense_from(_array(sphere.Location()), None, located[0], located[1])

    info = FaceInfo(
        id=face_id,
        kind=kind,
        area_mm2=float(area.Mass()),
        sense=sense,
        normal=normal,
        axis=axis,
        origin_mm=origin,
        radius_mm=None if radius is None else _r(radius),
        bbox_min_mm=lo,
        bbox_max_mm=hi,
    )
    return info, cylinder


def _load_intersector(shape):
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, HIT_TOLERANCE_MM)
    return intersector


def _hits(intersector, origin: np.ndarray, direction: np.ndarray, reach: float) -> list[float]:
    """Distances along the ray at which it meets the shape, ascending; hits closer than ``SAME_MM`` (an edge seen by two faces) count once."""
    from OCP.gp import gp_Lin

    intersector.Perform(gp_Lin(_pnt(origin), _dir(direction)), 0.0, reach)
    found = sorted(float(intersector.WParameter(k)) for k in range(1, intersector.NbPnt() + 1))
    merged: list[float] = []
    for w in found:
        if not merged or w - merged[-1] > SAME_MM:
            merged.append(w)
    return merged


class _IndexedIntersector:
    """OCCT's ray-query interface with conservative face-box pruning and lazy face intersectors."""

    def __init__(self, shape, *, deadline: Callable[[str], None] | None = None, phase: str = "inventory"):
        from OCP.Bnd import Bnd_Box
        from OCP.BRep import BRep_Tool
        from OCP.BRepBndLib import BRepBndLib
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX

        self._deadline = deadline
        self._phase = phase
        self._faces = _faces(shape)
        boxes = []
        for face in self._faces:
            if deadline is not None:
                deadline(phase)
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box, False)  # analytic bounds, including topology tolerances; independent of meshing
            boxes.append((*_array(box.CornerMin()), *_array(box.CornerMax())))
        bounds = np.array(boxes)
        tolerance = max(BRep_Tool.MaxTolerance_s(shape, kind) for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX))
        # Pruning must also retain hits admitted by edge/vertex tolerances and by floating-point rounding.
        margin = tolerance + HIT_TOLERANCE_MM + 16 * np.finfo(float).eps * np.maximum(1.0, np.max(np.abs(bounds), axis=1))
        self._lo = bounds[:, :3] - margin[:, None]
        self._hi = bounds[:, 3:] + margin[:, None]
        self._cache: dict[int, object] = {}
        self._parameters: list[float] = []

    def Perform(self, line, start: float, end: float):
        if self._deadline is not None:
            self._deadline(self._phase)
        origin, direction = _array(line.Location()), _array(line.Direction())
        near = np.full(len(self._faces), start, dtype=float)
        far = np.full(len(self._faces), end, dtype=float)
        for axis in range(3):
            if direction[axis] == 0.0:
                outside = (origin[axis] < self._lo[:, axis]) | (origin[axis] > self._hi[:, axis])
                far[outside] = -np.inf
            else:
                a = (self._lo[:, axis] - origin[axis]) / direction[axis]
                b = (self._hi[:, axis] - origin[axis]) / direction[axis]
                near = np.maximum(near, np.minimum(a, b))
                far = np.minimum(far, np.maximum(a, b))
        found = []
        for index in np.flatnonzero(near <= far):
            if self._deadline is not None:
                self._deadline(self._phase)
            if index not in self._cache:
                self._cache[index] = _load_intersector(self._faces[index])
            intersector = self._cache[index]
            intersector.Perform(line, start, end)
            found.extend(float(intersector.WParameter(k)) for k in range(1, intersector.NbPnt() + 1))
        self._parameters = found  # keep raw hits; _hits sorts and merges once across all faces
        if self._deadline is not None:
            self._deadline(self._phase)

    def NbPnt(self) -> int:
        return len(self._parameters)

    def WParameter(self, index: int) -> float:
        return self._parameters[index - 1]


def _bore(candidate: _Candidate, intersector, diagonal: float) -> tuple[np.ndarray, np.ndarray, bool] | None:
    """``(entry, drilling direction, thru)`` of a full-circumference concave cylinder, or None if it is not a hole.

    An end is open when none of three rays starting just beyond it, off the axis by half to nine tenths of the
    radius, meets anything: they run out of the part.  Off the axis, so that the apex of a drill-point cone
    cannot hide a blind hole's bottom; at three offsets, so that the edge of a smaller bore beyond a
    counterbore cannot graze every ray.  Both ends open is a through hole, one open end a blind hole, none an
    enclosed void.  A cylinder around a boss (an O-ring groove wall) is not a bore: from the axis, a radial
    ray must meet the wall at the radius.
    """
    a, origin, r = candidate.axis, candidate.origin, candidate.radius
    reach = 2.0 * diagonal + 1.0
    nudge = max(1e-6 * diagonal, SAME_MM)
    first = _perpendicular(a)
    second = np.cross(a, first)
    sides = [
        fraction * r * (math.cos(angle) * first + math.sin(angle) * second)
        for fraction, angle in zip(RIM_RAY_FRACTIONS, (0.0, TWO_PI / 3.0, 2.0 * TWO_PI / 3.0))
    ]
    lo_end, hi_end = origin + candidate.t0 * a, origin + candidate.t1 * a

    def open_at(end: np.ndarray, outward: float) -> bool:
        return not any(_hits(intersector, end + outward * nudge * a + side, outward * a, reach) for side in sides)

    open_lo, open_hi = open_at(lo_end, -1.0), open_at(hi_end, 1.0)
    if not (open_lo or open_hi):
        return None

    middle = origin + 0.5 * (candidate.t0 + candidate.t1) * a
    walled = False
    for k in range(WALL_RAY_DIRECTIONS):
        angle = TWO_PI * k / WALL_RAY_DIRECTIONS
        found = _hits(intersector, middle, math.cos(angle) * first + math.sin(angle) * second, reach)
        if found and abs(found[0] - r) <= 1e-6 * max(1.0, r):
            walled = True
            break
    if not walled:
        return None

    if open_lo and open_hi:
        drilling = a.copy()
        if drilling[int(np.argmax(np.abs(drilling)))] > 0.0:
            drilling = -drilling  # a through hole has no entry of its own: drill from the positive side of its dominant axis
        entry = lo_end if float((hi_end - lo_end) @ drilling) > 0.0 else hi_end
        return entry, drilling, True
    if open_lo:
        return lo_end, a.copy(), False
    return hi_end, -a, False


def _find_holes(
    shape, cylinders: list[_Cylinder], diagonal: float, *, deadline: Callable[[str], None] | None = None,
) -> tuple[HoleInfo, ...]:
    candidates: list[_Candidate] = []
    origins = np.empty((len(cylinders), 3))
    axes = np.empty((len(cylinders), 3))
    for c in cylinders:
        if deadline is not None:
            deadline("inventory")
        count = len(candidates)
        offsets = c.location - origins[:count]
        projections = np.sum(offsets * axes[:count], axis=1)
        lateral = offsets - projections[:, None] * axes[:count]
        # A component outside the tolerance cannot pass the original norm test. Allow rounding here;
        # accepts() below still makes every grouping decision with the original arithmetic and tolerances.
        margin = SAME_MM + 16 * np.finfo(float).eps * np.maximum(1.0, np.max(np.abs(offsets), axis=1))
        nearby = np.flatnonzero(np.all(np.abs(lateral) <= margin[:, None], axis=1))
        for index in nearby:
            if deadline is not None:
                deadline("inventory")
            existing = candidates[index]
            if existing.accepts(c):
                existing.span += c.span
                existing.face_ids.append(c.face_id)
                break
        else:
            origins[count], axes[count] = c.location, c.axis
            candidates.append(
                _Candidate(c.location, c.axis, c.radius, c.v0, c.v1, span=c.span, face_ids=[c.face_id])
            )
    whole = [c for c in candidates if c.span >= TWO_PI - SAME_MM]
    if not whole:
        return ()
    intersector = _IndexedIntersector(shape, deadline=deadline)
    found = []
    for candidate in whole:
        if deadline is not None:
            deadline("inventory")
        bore = _bore(candidate, intersector, diagonal)
        if bore is not None:
            found.append((candidate, *bore))

    def order(item) -> tuple:
        candidate, entry, drilling, _ = item
        return (
            tuple(round(float(v), 6) + 0.0 for v in drilling),
            round(2.0 * candidate.radius, 6),
            tuple(round(float(v), 6) + 0.0 for v in entry),
        )

    found.sort(key=order)
    return tuple(
        HoleInfo(
            id=f"H{n}",
            face_ids=tuple(sorted(candidate.face_ids)),
            diameter_mm=_r(2.0 * candidate.radius),
            axis=_tuple(drilling),
            entry_mm=_tuple(entry),
            depth_mm=_r(candidate.t1 - candidate.t0),
            thru=thru,
        )
        for n, (candidate, entry, drilling, thru) in enumerate(found, start=1)
    )


def inventory(solid: Solid, *, deadline: Callable[[str], None] | None = None) -> Inventory:
    """Every face with its geometry, and the holes found among the concave full cylinders.

    Face ids are the 1-based order of ``TopExp_Explorer``: valid for the same file bytes and the same OCCT
    version only.  Counterbores and countersinks are not detected.
    """
    _occ.load()
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX

    shape = solid._handle
    infos: list[FaceInfo] = []
    cylinders: list[_Cylinder] = []
    for face_id, face in enumerate(_faces(shape), start=1):
        if deadline is not None:
            deadline("inventory")
        info, cylinder = _describe(face_id, face)
        infos.append(info)
        if cylinder is not None and info.sense == "concave":
            cylinders.append(cylinder)
    lo, hi = _bbox(shape)
    diagonal = float(np.linalg.norm(np.array(hi) - np.array(lo)))
    return Inventory(
        faces=tuple(infos),
        holes=_find_holes(shape, cylinders, diagonal, deadline=deadline),
        edges=_unique_count(shape, TopAbs_EDGE),
        vertices=_unique_count(shape, TopAbs_VERTEX),
    )


# --------------------------------------------------------------------------- entities


def _label(ref: Entity) -> str:
    if isinstance(ref, FaceRef):
        return f"F{ref.id}"
    if isinstance(ref, HoleRef):
        return ref.id
    return "P(" + ", ".join(f"{v:g}" for v in ref.xyz_mm) + ")"


def lookup(inv: Inventory, ref: FaceRef | HoleRef) -> FaceInfo | HoleInfo:
    """The inventory entry a reference names; ``geometry.unknown_entity`` (with the face and hole counts) if there is none."""
    if isinstance(ref, FaceRef):
        if 1 <= ref.id <= len(inv.faces):
            return inv.faces[ref.id - 1]
    else:
        for hole in inv.holes:
            if hole.id == ref.id.upper():
                return hole
    what = "face" if isinstance(ref, FaceRef) else "hole"
    raise ToolFailure(
        "geometry.unknown_entity",
        f"{_label(ref)}: the model has no such {what} (it has {len(inv.faces)} faces and {len(inv.holes)} holes)",
        {"ref": _label(ref), "faces": len(inv.faces), "holes": len(inv.holes)},
    )


def _unsupported(ref: Entity, why: str, **details) -> ToolFailure:
    return ToolFailure("geometry.unsupported_query", f"{_label(ref)}: {why}", {"ref": _label(ref), **details})


def _shape_of(solid: Solid, ref: Entity, inv: Inventory):
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.TopoDS import TopoDS_Compound

    if isinstance(ref, PointRef):
        return BRepBuilderAPI_MakeVertex(_pnt(ref.xyz_mm)).Vertex()
    faces = _faces(solid._handle)
    entry = lookup(inv, ref)
    if isinstance(entry, FaceInfo):
        return faces[entry.id - 1]
    hole = entry
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for face_id in hole.face_ids:
        builder.Add(compound, faces[face_id - 1])
    return compound


def distance(solid: Solid, a: Entity, b: Entity, *, inv: Inventory | None = None) -> float:
    """Minimum distance in mm between two entities (a hole is its cylinder wall), by ``BRepExtrema_DistShapeShape``."""
    _occ.load()
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    inv = inv or inventory(solid)
    first, second = _shape_of(solid, a, inv), _shape_of(solid, b, inv)
    measure = BRepExtrema_DistShapeShape(first, second)
    if not measure.IsDone():
        raise ToolFailure(
            "geometry.measure_failed",
            f"OCCT could not measure the distance between {_label(a)} and {_label(b)}",
            {"a": _label(a), "b": _label(b)},
        )
    return float(measure.Value())


def _direction(ref: Entity, inv: Inventory) -> tuple[np.ndarray, bool]:
    """``(unit vector, is_axis)``: a plane's outward normal, or the axis of a cylinder face or a hole."""
    if isinstance(ref, PointRef):
        raise _unsupported(ref, "a point has no direction; use a plane face, a cylinder face or a hole")
    entry = lookup(inv, ref)
    if isinstance(entry, HoleInfo):
        return np.array(entry.axis), True
    face = entry
    if face.kind == "plane" and face.normal is not None:
        return np.array(face.normal), False
    if face.kind == "cylinder" and face.axis is not None:
        return np.array(face.axis), True
    raise _unsupported(ref, f"an angle needs a plane face, a cylinder face or a hole, not a {face.kind} face", kind=face.kind)


def angle(solid: Solid, a: Entity, b: Entity, *, inv: Inventory | None = None) -> float:
    """Angle in radians between two entities.

    Two planes: between their outward normals, 0 to pi.  When either is a cylinder face or a hole, its axis is a
    line without a sense, so the angle is between lines, 0 to pi/2.
    """
    _occ.load()
    inv = inv or inventory(solid)
    first, first_is_axis = _direction(a, inv)
    second, second_is_axis = _direction(b, inv)
    cosine = float(first @ second)
    if first_is_axis or second_is_axis:
        cosine = abs(cosine)
    return math.acos(max(-1.0, min(1.0, cosine)))


def wall_thickness(solid: Solid, at_mm: Vec3, direction: Vec3) -> float | None:
    """Distance in mm between the first two exact hits of the ray from ``at_mm`` along ``direction``; None if it has fewer than two."""
    _occ.load()
    ray = np.array(direction, dtype=np.float64)
    if not np.linalg.norm(ray) > 0.0:
        raise ToolFailure("input.invalid", "the ray direction is the zero vector")
    lo, hi = _bbox(solid._handle)
    origin = np.array(at_mm, dtype=np.float64)
    reach = float(np.linalg.norm(origin - (np.array(lo) + np.array(hi)) / 2.0) + np.linalg.norm(np.array(hi) - np.array(lo)) + 1.0)
    hits = _hits(_load_intersector(solid._handle), origin, _unit(ray), reach)
    return float(hits[1] - hits[0]) if len(hits) >= 2 else None
