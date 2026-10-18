"""Building solids: primitives, extruded and revolved profiles, hole tools, booleans, fillets and chamfers.

Lengths are millimetres, angles radians.  Kernel failures raise ``ToolFailure('geometry.build_failed')`` with the
kernel's message in ``details['occt']``; an edge selector that matches nothing raises ``geometry.empty_selection``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._files import _count
from eng_toolkit.brep._query import _array, _bbox, _faces
from eng_toolkit.brep._types import Solid, Vec3
from eng_toolkit.errors import ToolFailure

Vec2 = tuple[float, float]
Mode = Literal["add", "cut", "intersect"]
MODES: tuple[str, ...] = ("add", "cut", "intersect")
EDGE_TERMS: tuple[str, ...] = ("all", "|x", "|y", "|z", ">x", "<x", ">y", "<y", ">z", "<z", "convex", "concave", "line", "circle")
TWO_PI = 2.0 * math.pi
PARALLEL = 1.0 - 1e-9  # |cos| of a line direction that counts as parallel to an axis
EXTREME_MM = 1e-6  # an edge lies in a bounding-box face when its coordinates are this close to the extreme
SMOOTH = 1e-6  # |sin| of the dihedral angle below which two faces meet smoothly
AXES = {"x": 0, "y": 1, "z": 2}

# (u direction, v direction, normal) of each sketch plane
PLANE_FRAMES: dict[str, tuple[Vec3, Vec3, Vec3]] = {
    "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "xz": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "yz": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}


@dataclass(frozen=True)
class Rect2:
    size: Vec2
    at: Vec2 = (0.0, 0.0)  # minimum corner, or the centre when ``centered``
    centered: bool = False


@dataclass(frozen=True)
class Circle2:
    diameter: float
    at: Vec2 = (0.0, 0.0)  # centre


@dataclass(frozen=True)
class Polygon2:
    points: tuple[Vec2, ...]


@dataclass(frozen=True)
class Slot2:
    length: float  # overall, round ends included
    width: float
    at: Vec2 = (0.0, 0.0)  # centre
    angle: float = 0.0  # of the long axis from u, radians


Profile2 = Rect2 | Circle2 | Polygon2 | Slot2


@dataclass(frozen=True)
class SketchPlane:
    """``xy``, ``xz`` or ``yz`` with (u, v) = (x, y), (x, z), (y, z); ``offset`` moves along the normal +z, +y, +x."""

    name: Literal["xy", "xz", "yz"]
    offset: float = 0.0


@dataclass(frozen=True)
class EdgeSelector:
    text: str  # as the caller wrote it
    terms: tuple[str, ...]  # validated, intersected


@dataclass(frozen=True)
class EdgeMatch:
    count: int  # edges matched by every term
    per_term: dict[str, int]  # edges each term matches on its own


def parse_edge_selector(text: str) -> EdgeSelector:
    """Split on ``&`` and check every term; pure, needs no engine."""
    if not isinstance(text, str):
        raise ToolFailure("input.invalid", f"an edge selector is a string of terms joined by '&', not {type(text).__name__}", {"selector": text, "allowed": list(EDGE_TERMS)})
    terms = tuple(part.strip() for part in text.split("&"))
    for term in terms:
        if term not in EDGE_TERMS:
            shown = term or "(empty)"
            raise ToolFailure(
                "input.invalid",
                f"edge selector {text!r}: {shown!r} is not a term; use terms from {list(EDGE_TERMS)} joined by '&'",
                {"selector": text, "term": term, "allowed": list(EDGE_TERMS)},
            )
    return EdgeSelector(text=text, terms=terms)


# --------------------------------------------------------------------------- helpers


def _pnt(v):
    from OCP.gp import gp_Pnt

    return gp_Pnt(float(v[0]), float(v[1]), float(v[2]))


def _direction(v, what: str):
    from OCP.gp import gp_Dir

    vector = np.asarray(v, dtype=np.float64)
    if vector.shape != (3,) or not np.isfinite(vector).all() or not np.linalg.norm(vector) > 0.0:
        raise ToolFailure("input.invalid", f"{what}: the direction {tuple(float(c) for c in np.ravel(vector))} is not a non-zero vector")
    return gp_Dir(*(float(c) for c in vector))


def _positive(value: float, what: str) -> float:
    if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0):
        raise ToolFailure("input.invalid", f"{what} must be a positive length in mm, got {value!r}")
    return float(value)


def _failure(message: str, exc: BaseException | None = None, *, why: str = "") -> ToolFailure:
    occt = str(exc).strip() if exc is not None and str(exc).strip() else why
    return ToolFailure("geometry.build_failed", message, {"occt": occt or "the kernel did not complete the operation"})


def _checked(shape, what: str) -> Solid:
    """Wrap the result of a boolean, fillet or chamfer: it must hold a solid and pass BRepCheck."""
    result = _wrap(_simplify(shape))
    if result.solids == 0:
        raise _failure(f"{what}: nothing is left of the part", why="the result holds no solid")
    if not _valid(result._handle):
        raise _failure(f"{what}: the kernel produced an invalid solid", why="BRepCheck_Analyzer: the result is not a valid solid")
    return result


def _wrap(shape) -> Solid:
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID

    return Solid(_handle=shape, solids=_count(shape, TopAbs_SOLID), faces=_count(shape, TopAbs_FACE))


def _simplify(shape):
    """A compound holding exactly one solid becomes that solid: fillets and chamfers want a solid."""
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if shape.ShapeType() == TopAbs_COMPOUND and _count(shape, TopAbs_SOLID) == 1:
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        return TopoDS.Solid(explorer.Current())
    return shape


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _valid(shape) -> bool:
    from OCP.BRepCheck import BRepCheck_Analyzer

    return bool(BRepCheck_Analyzer(shape).IsValid())


def validity(solid: Solid) -> tuple[str, ...]:
    """BRepCheck problems of the shape; an empty tuple means it is valid."""
    _occ.load()
    if _valid(solid._handle):
        return ()
    return (f"BRepCheck_Analyzer reports an invalid shape ({solid.solids} solids, {solid.faces} faces)",)


def _as_solid(shape, what: str) -> Solid:
    """A freshly built primitive or sweep: it must be a valid solid with positive volume (a reversed one is turned inside out)."""
    if shape is None or shape.IsNull():
        raise _failure(f"{what}: the kernel produced no shape")
    wrapped = _wrap(shape)
    if wrapped.solids != 1:
        raise _failure(f"{what}: expected one solid, got {wrapped.solids}")
    volume = _volume(shape)
    if volume < 0.0:
        shape = shape.Reversed()
        volume = -volume
    if not volume > 0.0 or not _valid(shape):
        raise _failure(f"{what}: the profile does not make a valid solid (self-crossing, degenerate or crossing the axis)")
    return _wrap(shape)


# --------------------------------------------------------------------------- primitives


def make_box(size: Vec3, at: Vec3 = (0.0, 0.0, 0.0)) -> Solid:
    """A block with its minimum corner at ``at``."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    dx, dy, dz = (_positive(c, "box size") for c in size)
    return _as_solid(BRepPrimAPI_MakeBox(_pnt(at), dx, dy, dz).Shape(), "box")


def make_cylinder(diameter: float, height: float, at: Vec3, axis: Vec3) -> Solid:
    """A cylinder whose base centre is ``at`` and which rises ``height`` along ``axis``."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2

    radius = _positive(diameter, "cylinder diameter") / 2.0
    length = _positive(height, "cylinder height")
    frame = gp_Ax2(_pnt(at), _direction(axis, "cylinder axis"))
    return _as_solid(BRepPrimAPI_MakeCylinder(frame, radius, length).Shape(), "cylinder")


def make_sphere(diameter: float, at: Vec3) -> Solid:
    """A ball whose centre is ``at``."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    radius = _positive(diameter, "sphere diameter") / 2.0
    return _as_solid(BRepPrimAPI_MakeSphere(_pnt(at), radius).Shape(), "sphere")


def make_cone(diameter: float, top_diameter: float, height: float, at: Vec3, axis: Vec3) -> Solid:
    """A cone or frustum: ``diameter`` at the base centre ``at``, ``top_diameter`` (0 for a point) ``height`` along ``axis``."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2

    bottom = _positive(diameter, "cone diameter")
    if not (isinstance(top_diameter, (int, float)) and math.isfinite(top_diameter) and top_diameter >= 0.0):
        raise ToolFailure("input.invalid", f"cone top diameter must be a length in mm of 0 or more, got {top_diameter!r}")
    length = _positive(height, "cone height")
    direction = _direction(axis, "cone axis")
    if top_diameter == bottom:
        return make_cylinder(bottom, length, at, axis)
    frame = gp_Ax2(_pnt(at), direction)
    try:
        shape = BRepPrimAPI_MakeCone(frame, bottom / 2.0, float(top_diameter) / 2.0, length).Shape()
    except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
        raise _failure("cone: the kernel could not build the cone", exc) from exc
    return _as_solid(shape, "cone")


# --------------------------------------------------------------------------- profiles


def _frame(plane: SketchPlane) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames = PLANE_FRAMES.get(plane.name)
    if frames is None:
        raise ToolFailure("input.invalid", f"sketch plane {plane.name!r} is not one of {list(PLANE_FRAMES)}")
    return tuple(np.array(v) for v in frames)  # type: ignore[return-value]


def _lift(plane: SketchPlane, u: float, v: float) -> np.ndarray:
    ux, vx, nx = _frame(plane)
    return u * ux + v * vx + plane.offset * nx


def _polygon_wire(points3: Sequence[np.ndarray]):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon

    maker = BRepBuilderAPI_MakePolygon()
    for p in points3:
        maker.Add(_pnt(p))
    maker.Close()
    if not maker.IsDone():
        raise _failure("profile: the outline is not a closed polygon")
    return maker.Wire()


def _slot_wire(profile: Slot2, plane: SketchPlane):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeArcOfCircle

    length = _positive(profile.length, "slot length")
    width = _positive(profile.width, "slot width")
    if not length > width:
        raise ToolFailure("input.invalid", f"slot length {length:g} mm must be greater than its width {width:g} mm")
    cu, cv = profile.at
    along = np.array([math.cos(profile.angle), math.sin(profile.angle)])
    across = np.array([-along[1], along[0]])
    half = (length - width) / 2.0
    r = width / 2.0
    centre = np.array([cu, cv])
    left, right = centre - half * along, centre + half * along

    def lift(p: np.ndarray) -> np.ndarray:
        return _lift(plane, float(p[0]), float(p[1]))

    p1, p2 = left - r * across, right - r * across
    p3, p4 = right + r * across, left + r * across
    wire = BRepBuilderAPI_MakeWire()
    wire.Add(BRepBuilderAPI_MakeEdge(_pnt(lift(p1)), _pnt(lift(p2))).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_pnt(lift(p2)), _pnt(lift(right + r * along)), _pnt(lift(p3))).Value()).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(_pnt(lift(p3)), _pnt(lift(p4))).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_pnt(lift(p4)), _pnt(lift(left - r * along)), _pnt(lift(p1))).Value()).Edge())
    if not wire.IsDone():
        raise _failure("slot: the outline could not be closed")
    return wire.Wire()


def _profile_face(profile: Profile2, plane: SketchPlane):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ

    if isinstance(profile, Rect2):
        du, dv = (_positive(c, "rect size") for c in profile.size)
        u0, v0 = profile.at
        if profile.centered:
            u0, v0 = u0 - du / 2.0, v0 - dv / 2.0
        wire = _polygon_wire([_lift(plane, u, v) for u, v in ((u0, v0), (u0 + du, v0), (u0 + du, v0 + dv), (u0, v0 + dv))])
    elif isinstance(profile, Circle2):
        radius = _positive(profile.diameter, "circle diameter") / 2.0
        centre = _lift(plane, *profile.at)
        circle = gp_Circ(gp_Ax2(_pnt(centre), _direction(_frame(plane)[2], "sketch plane")), radius)
        wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circle).Edge()).Wire()
    elif isinstance(profile, Polygon2):
        if len(profile.points) < 3:
            raise ToolFailure("input.invalid", "a polygon needs at least 3 points")
        wire = _polygon_wire([_lift(plane, u, v) for u, v in profile.points])
    elif isinstance(profile, Slot2):
        wire = _slot_wire(profile, plane)
    else:
        raise ToolFailure("input.invalid", f"unknown profile {type(profile).__name__}")
    face = BRepBuilderAPI_MakeFace(wire, True)
    if not face.IsDone():
        raise _failure("profile: the outline does not bound a planar face")
    return face.Face()


def make_prism(profile: Profile2, plane: SketchPlane, distance: float) -> Solid:
    """Extrude a profile along the plane normal; a negative ``distance`` goes the other way."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    if not (math.isfinite(distance) and distance != 0.0):
        raise ToolFailure("input.invalid", f"extrude distance must be non-zero, got {distance!r}")
    face = _profile_face(profile, plane)
    normal = _frame(plane)[2]
    try:
        shape = BRepPrimAPI_MakePrism(face, gp_Vec(*(float(c) for c in normal * distance))).Shape()
    except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
        raise _failure("extrude: the kernel could not sweep the profile", exc) from exc
    return _as_solid(shape, "extrude")


def make_revolution(profile: Profile2, plane: SketchPlane, axis: Literal["u", "v"], angle: float) -> Solid:
    """Turn a profile about the ``u`` or ``v`` axis of its plane (through the plane origin) by ``angle``."""
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1

    if axis not in ("u", "v"):
        raise ToolFailure("input.invalid", f"revolve axis must be 'u' or 'v', got {axis!r}")
    if not (math.isfinite(angle) and 0.0 < angle <= TWO_PI * (1.0 + 1e-9)):
        raise ToolFailure("input.invalid", f"revolve angle must be in (0, 2*pi] rad, got {angle!r}")
    face = _profile_face(profile, plane)
    ux, vx, nx = _frame(plane)
    origin = plane.offset * nx
    line = gp_Ax1(_pnt(origin), _direction(ux if axis == "u" else vx, "revolve axis"))
    try:
        if angle >= TWO_PI * (1.0 - 1e-12):
            shape = BRepPrimAPI_MakeRevol(face, line).Shape()
        else:
            shape = BRepPrimAPI_MakeRevol(face, line, float(angle)).Shape()
    except Exception as exc:  # noqa: BLE001
        raise _failure("revolve: the kernel could not turn the profile", exc) from exc
    return _as_solid(shape, "revolve")


# --------------------------------------------------------------------------- holes and booleans


def make_hole_tool(
    entries: Sequence[Vec3],
    axis: Vec3,
    diameter: float,
    depth: float | None,
    counterbore: tuple[float, float] | None,
    countersink: tuple[float, float] | None,
    reach_mm: float,
) -> Solid:
    """The material a drill removes, one tool per entry point, as a compound for :func:`combine` ``mode='cut'``.

    ``axis`` is the drilling direction, into the material; ``depth`` None drills ``reach_mm`` (past the part).
    ``counterbore`` is (diameter, depth) and ``countersink`` (diameter, included angle in radians), both measured from the entry.
    The pieces of a tool may overlap each other; ``combine`` hands them to the kernel as separate cutting tools.
    """
    _occ.load()
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2
    from OCP.TopoDS import TopoDS_Compound

    radius = _positive(diameter, "hole diameter") / 2.0
    length = _positive(reach_mm if depth is None else depth, "hole depth")
    direction = _direction(axis, "hole axis")
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    if not entries:
        raise ToolFailure("input.invalid", "a hole needs at least one entry point")
    for entry in entries:
        frame = gp_Ax2(_pnt(entry), direction)
        builder.Add(compound, BRepPrimAPI_MakeCylinder(frame, radius, length).Shape())
        if counterbore is not None:
            cb_diameter, cb_depth = counterbore
            builder.Add(compound, BRepPrimAPI_MakeCylinder(frame, _positive(cb_diameter, "counterbore diameter") / 2.0, _positive(cb_depth, "counterbore depth")).Shape())
        if countersink is not None:
            cs_diameter, cs_angle = countersink
            cs_radius = _positive(cs_diameter, "countersink diameter") / 2.0
            if not (0.0 < cs_angle < math.pi and cs_radius > radius):
                raise ToolFailure("input.invalid", "a countersink needs a diameter above the hole's and an included angle between 0 and 180 degrees")
            builder.Add(compound, BRepPrimAPI_MakeCone(frame, cs_radius, radius, (cs_radius - radius) / math.tan(cs_angle / 2.0)).Shape())
    return _wrap(compound)


def _children(shape) -> list:
    from OCP.TopAbs import TopAbs_COMPOUND
    from OCP.TopoDS import TopoDS_Iterator

    if shape.ShapeType() != TopAbs_COMPOUND:
        return [shape]
    found = []
    iterator = TopoDS_Iterator(shape)
    while iterator.More():
        found.append(iterator.Value())
        iterator.Next()
    return found


def combine(base: Solid, tool: Solid, mode: Mode) -> Solid:
    """Add (fuse), cut or intersect.  A compound ``tool`` is treated as several tools (see :func:`make_hole_tool`).

    The kernel runs serially, so face order does not depend on thread timing, and coplanar faces of the result are merged.
    """
    _occ.load()
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.collections import List_TopoDS_Shape
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    makers = {"add": BRepAlgoAPI_Fuse, "cut": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}
    if mode not in makers:
        raise ToolFailure("input.invalid", f"combine mode must be one of {list(MODES)}, got {mode!r}", {"allowed": list(MODES)})
    operation = makers[mode]()
    objects, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    objects.Append(base._handle)
    for piece in _children(tool._handle):
        tools.Append(piece)
    operation.SetArguments(objects)
    operation.SetTools(tools)
    operation.SetRunParallel(False)
    try:
        operation.Build()
        shape = operation.Shape() if operation.IsDone() else None
    except Exception as exc:  # noqa: BLE001
        raise _failure(f"{mode}: the boolean operation failed", exc) from exc
    if shape is None or shape.IsNull():
        raise _failure(f"{mode}: the boolean operation failed")
    unifier = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unifier.Build()
    return _checked(unifier.Shape(), mode)


# --------------------------------------------------------------------------- rigid motion


def transform(solid: Solid, rotation: Sequence[Sequence[float]], translation: Vec3) -> Solid:
    """Rotate about the origin by the 3 x 3 ``rotation`` matrix (given by rows), then translate; the result is a copy."""
    _occ.load()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    try:
        matrix = np.asarray(rotation, dtype=np.float64)
        shift = np.asarray(translation, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ToolFailure("input.invalid", f"transform: the rotation must be 3 x 3 numbers and the translation 3 numbers ({exc})") from exc
    if matrix.shape != (3, 3) or shift.shape != (3,) or not (np.isfinite(matrix).all() and np.isfinite(shift).all()):
        raise ToolFailure("input.invalid", f"transform: the rotation must be a finite 3 x 3 matrix and the translation 3 finite numbers, got shapes {matrix.shape} and {shift.shape}")
    if not (np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-9) and np.linalg.det(matrix) > 0.0):
        raise ToolFailure("input.invalid", "transform: the matrix is not a rotation (it must be orthonormal with determinant +1; no scaling or mirroring)")
    motion = gp_Trsf()
    motion.SetValues(*(float(c) for row, t in zip(matrix, shift) for c in (*row, t)))
    try:
        moved = BRepBuilderAPI_Transform(solid._handle, motion, True).Shape()
    except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
        raise _failure("transform: the kernel could not move the shape", exc) from exc
    return _wrap(moved)


# --------------------------------------------------------------------------- edges


class _Edges:
    """The unique, non-degenerate edges of a shape, with what the selector terms need to know about each."""

    def __init__(self, shape):
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
        from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS

        self.shape = shape
        self.map = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, self.map)
        self.edges = []
        self.index: list[int] = []  # position in the map of each kept edge
        self.kind: list[str] = []
        self.direction: list[np.ndarray | None] = []
        self.box: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(1, int(self.map.Extent()) + 1):
            edge = TopoDS.Edge(self.map.FindKey(i))
            if BRep_Tool.Degenerated_s(edge):
                continue
            curve = BRepAdaptor_Curve(edge)
            kind = curve.GetType()
            self.edges.append(edge)
            self.index.append(i)
            self.kind.append("line" if kind == GeomAbs_Line else "circle" if kind == GeomAbs_Circle else "other")
            self.direction.append(_array(curve.Line().Direction()) if kind == GeomAbs_Line else None)
            lo, hi = _bbox(edge)
            self.box.append((np.array(lo), np.array(hi)))
        self.lo, self.hi = (np.array(v) for v in _bbox(shape))
        self._convexity: list[str] | None = None

    def __len__(self) -> int:
        return len(self.edges)

    def convexity(self) -> list[str]:
        """``convex``, ``concave`` or ``smooth`` for each edge (``smooth`` also covers seams and edges of one face)."""
        if self._convexity is None:
            self._convexity = self._classify()
        return self._convexity

    def _classify(self) -> list[str]:
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepGProp import BRepGProp_Face
        from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
        from OCP.gp import gp_Pnt, gp_Vec
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FORWARD
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        seen: dict[int, list[tuple[object, object]]] = {}
        for face in _faces(self.shape):
            explorer = TopExp_Explorer(face, TopAbs_EDGE)
            while explorer.More():
                oriented = TopoDS.Edge(explorer.Current())
                position = int(self.map.FindIndex(oriented))
                if position:
                    seen.setdefault(position, []).append((face, oriented))
                explorer.Next()

        def normal_at(face, point: np.ndarray) -> np.ndarray | None:
            projection = GeomAPI_ProjectPointOnSurf(_pnt(point), BRep_Tool.Surface_s(face))
            if projection.NbPoints() == 0:
                return None
            u, v = projection.LowerDistanceParameters()
            where, vec = gp_Pnt(), gp_Vec()
            BRepGProp_Face(face).Normal(u, v, where, vec)
            n = _array(vec)
            length = np.linalg.norm(n)
            return n / length if length > 0.0 else None

        out: list[str] = []
        for position in self.index:
            pair = seen.get(position, [])
            if len(pair) != 2 or pair[0][0].IsSame(pair[1][0]):
                out.append("smooth")
                continue
            (face1, edge1), (face2, _) = pair
            curve = BRepAdaptor_Curve(edge1)
            u = 0.5 * (curve.FirstParameter() + curve.LastParameter())
            where, tangent = gp_Pnt(), gp_Vec()
            curve.D1(u, where, tangent)
            t = _array(tangent)
            if edge1.Orientation() != TopAbs_FORWARD:
                t = -t
            point = _array(where)
            n1, n2 = normal_at(face1, point), normal_at(face2, point)
            if n1 is None or n2 is None or not np.linalg.norm(t) > 0.0:
                out.append("smooth")
                continue
            sine = float(t / np.linalg.norm(t) @ np.cross(n1, n2))
            out.append("convex" if sine > SMOOTH else "concave" if sine < -SMOOTH else "smooth")
        return out

    def matches(self, term: str) -> list[bool]:
        n = len(self)
        if term == "all":
            return [True] * n
        if term in ("line", "circle"):
            return [k == term for k in self.kind]
        if term in ("convex", "concave"):
            return [c == term for c in self.convexity()]
        axis = AXES[term[1]]
        if term[0] == "|":
            return [d is not None and abs(float(d[axis])) > PARALLEL for d in self.direction]
        extreme = self.hi[axis] if term[0] == ">" else self.lo[axis]
        return [abs(float(lo[axis]) - extreme) <= EXTREME_MM and abs(float(hi[axis]) - extreme) <= EXTREME_MM for lo, hi in self.box]


def _select(shape, selector: EdgeSelector) -> tuple[list, EdgeMatch]:
    table = _Edges(shape)
    per_term: dict[str, list[bool]] = {term: table.matches(term) for term in selector.terms}
    chosen = [all(per_term[term][i] for term in selector.terms) for i in range(len(table))]
    edges = [edge for edge, keep in zip(table.edges, chosen) if keep]
    return edges, EdgeMatch(count=len(edges), per_term={term: sum(flags) for term, flags in per_term.items()})


def select_edges(solid: Solid, selector: EdgeSelector) -> EdgeMatch:
    """How many edges the selector picks, and how many each term matches on its own."""
    _occ.load()
    return _select(solid._handle, selector)[1]


def _chosen(solid: Solid, selector: EdgeSelector) -> list:
    edges, match = _select(solid._handle, selector)
    if not edges:
        raise ToolFailure(
            "geometry.empty_selection",
            f"edge selector {selector.text!r} matches no edge; edges matched by each term: {match.per_term}",
            {"selector": selector.text, "matches_per_term": match.per_term},
        )
    return edges


def _modify(solid: Solid, selector: EdgeSelector, size: float, what: str, maker_name: str) -> tuple[Solid, int]:
    _occ.load()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet

    _positive(size, f"{what} size")
    edges = _chosen(solid, selector)
    maker = (BRepFilletAPI_MakeFillet if maker_name == "fillet" else BRepFilletAPI_MakeChamfer)(solid._handle)
    try:
        for edge in edges:
            maker.Add(float(size), edge)
        maker.Build()
        if not maker.IsDone():
            raise _failure(f"{what} of {size:g} mm on {len(edges)} edges is not possible")
        shape = maker.Shape()  # raises StdFail_NotDone when the kernel could not build it
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _failure(f"{what} of {size:g} mm on {len(edges)} edges is not possible", exc) from exc
    if shape.IsNull():
        raise _failure(f"{what} of {size:g} mm on {len(edges)} edges gave no shape")
    return _checked(shape, f"{what} of {size:g} mm on {len(edges)} edges"), len(edges)


def fillet(solid: Solid, edges: EdgeSelector, radius: float) -> tuple[Solid, int]:
    """Round the selected edges; returns the result and the number of edges used."""
    return _modify(solid, edges, radius, "fillet", "fillet")


def chamfer(solid: Solid, edges: EdgeSelector, distance: float) -> tuple[Solid, int]:
    """Bevel the selected edges symmetrically; returns the result and the number of edges used."""
    return _modify(solid, edges, distance, "chamfer", "chamfer")
