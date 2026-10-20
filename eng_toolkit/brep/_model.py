"""Building solids: primitives, extruded and revolved profiles, hole tools, booleans, fillets and chamfers.

Lengths are millimetres, angles radians.  Kernel failures raise ``ToolFailure('geometry.build_failed')`` with the
kernel's message in ``details['occt']``; an edge selector that matches nothing raises ``geometry.empty_selection``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._created import Created
from eng_toolkit.brep._files import _count
from eng_toolkit.brep._path import Path2, PathSegment, resolve
from eng_toolkit.brep._query import _array, _bbox, _faces
from eng_toolkit.brep._types import Solid, Vec3
from eng_toolkit.errors import ToolFailure

Vec2 = tuple[float, float]
Mode = Literal["add", "cut", "intersect"]
MODES: tuple[str, ...] = ("add", "cut", "intersect")
EDGE_TERMS: tuple[str, ...] = ("all", "|x", "|y", "|z", ">x", "<x", ">y", "<y", ">z", "<z", "convex", "concave", "line", "circle")
FACE_TERMS: tuple[str, ...] = ("all", ">x", "<x", ">y", "<y", ">z", "<z", "plane", "cylinder")
OF_PREFIX = "of:"  # the term of:<feature name> is valid in an edge selector and in a face selector
OF_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
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


Profile2 = Rect2 | Circle2 | Polygon2 | Slot2 | Path2


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
class FaceSelector:
    text: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class EdgeMatch:
    count: int  # edges matched by every term
    per_term: dict[str, int]  # edges each term matches on its own


def _parse_terms(text: str, allowed: tuple[str, ...], what: str) -> tuple[str, ...]:
    shown_allowed = [*allowed, f"{OF_PREFIX}<feature name>"]
    if not isinstance(text, str):
        raise ToolFailure("input.invalid", f"{what} is a string of terms joined by '&', not {type(text).__name__}", {"selector": text, "allowed": shown_allowed})
    terms = tuple(part.strip() for part in text.split("&"))
    for term in terms:
        if term in allowed:
            continue
        if term.startswith(OF_PREFIX) and OF_NAME.fullmatch(term[len(OF_PREFIX) :]):
            continue
        shown = term or "(empty)"
        raise ToolFailure(
            "input.invalid",
            f"{what} {text!r}: {shown!r} is not a term; use terms from {shown_allowed} joined by '&'",
            {"selector": text, "term": term, "allowed": shown_allowed},
        )
    return terms


def parse_edge_selector(text: str) -> EdgeSelector:
    """Split on ``&`` and check every term (the fixed terms and ``of:<feature name>``); pure, needs no engine."""
    return EdgeSelector(text=text, terms=_parse_terms(text, EDGE_TERMS, "edge selector"))


def parse_face_selector(text: str) -> FaceSelector:
    """The same for faces: the terms of :data:`FACE_TERMS` and ``of:<feature name>``."""
    return FaceSelector(text=text, terms=_parse_terms(text, FACE_TERMS, "face selector"))


def selector_features(selector: EdgeSelector | FaceSelector) -> tuple[str, ...]:
    """The feature names a selector refers to with ``of:`` terms."""
    return tuple(term[len(OF_PREFIX) :] for term in selector.terms if term.startswith(OF_PREFIX))


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


def _path_wire(profile: Path2, plane: SketchPlane):
    from dataclasses import replace

    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeArcOfCircle

    pieces, problems = resolve(profile)
    if problems:
        raise ToolFailure("input.invalid", f"path: {problems[0].message}")
    pieces[-1] = replace(pieces[-1], end=profile.start)  # closed within the tolerance: make it exact so the wire closes

    def lift(p) -> np.ndarray:
        return _lift(plane, float(p[0]), float(p[1]))

    wire = BRepBuilderAPI_MakeWire()
    for piece in pieces:
        a, b = _pnt(lift(piece.start)), _pnt(lift(piece.end))
        if piece.via is None:
            edge = BRepBuilderAPI_MakeEdge(a, b).Edge()
        else:
            edge = BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(a, _pnt(lift(piece.via)), b).Value()).Edge()
        wire.Add(edge)
    if not wire.IsDone():
        raise _failure("path: the outline could not be joined into one closed wire")
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
    elif isinstance(profile, Path2):
        wire = _path_wire(profile, plane)
    else:
        raise ToolFailure("input.invalid", f"unknown profile {type(profile).__name__}")
    face = BRepBuilderAPI_MakeFace(wire, True)
    if not face.IsDone():
        raise _failure("profile: the outline does not bound a planar face")
    return face.Face()


def _facing(face, normal: np.ndarray) -> float:
    """+1 when the face's own normal points along ``normal``, -1 when it points the other way (a profile drawn clockwise)."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Vec

    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    where, vec = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(0.5 * (u0 + u1), 0.5 * (v0 + v1), where, vec)
    return 1.0 if float(_array(vec) @ normal) > 0.0 else -1.0


def make_prism(profile: Profile2, plane: SketchPlane, distance: float, taper: float | None = None) -> Solid:
    """Extrude a profile along the plane normal; a negative ``distance`` goes the other way.

    ``taper`` (radians, None or 0 for a straight extrusion; 0 up to but excluding 90 degrees) leans every side wall inward along the
    direction of the extrusion.  Widening is not offered: the kernel rounds the corners of a widening draft, so the result would not
    be the straight-walled frustum that was asked for.  A taper that narrows the profile to nothing before the distance is reached
    is refused (the kernel would stop at the apex without saying so).
    """
    _occ.load()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from OCP.LocOpe import LocOpe_DPrism

    if not (math.isfinite(distance) and distance != 0.0):
        raise ToolFailure("input.invalid", f"extrude distance must be non-zero, got {distance!r}")
    face = _profile_face(profile, plane)
    normal = _frame(plane)[2]
    if taper is None or taper == 0.0:
        try:
            shape = BRepPrimAPI_MakePrism(face, gp_Vec(*(float(c) for c in normal * distance))).Shape()
        except Exception as exc:  # noqa: BLE001 - OCCT raises its own exception types
            raise _failure("extrude: the kernel could not sweep the profile", exc) from exc
        return _as_solid(shape, "extrude")
    if not (math.isfinite(taper) and 0.0 < taper < math.pi / 2.0):
        raise ToolFailure("input.invalid", f"taper must be an angle above 0 and below 90 degrees (it narrows along the extrusion), got {taper!r}", {"field": "taper"})
    # The kernel extrudes along the face's own normal, narrowing when height and angle have the same sign.
    along = math.copysign(1.0, distance) * _facing(face, normal)
    try:
        shape = LocOpe_DPrism(face, along * abs(distance) / math.cos(taper), taper * along).Shape()  # its height runs along the sloping wall
    except Exception as exc:  # noqa: BLE001
        raise _failure("extrude: the kernel could not build the tapered extrusion", exc) from exc
    solid = _as_solid(shape, "extrude")
    axis = int(np.argmax(np.abs(normal)))
    lo, hi = _bbox(solid._handle)
    if abs((hi[axis] - lo[axis]) - abs(distance)) > 1e-6 * max(abs(distance), 1.0):
        raise ToolFailure(
            "input.invalid",
            f"extrude: a taper of {math.degrees(taper):g} degrees closes the profile to a point after {hi[axis] - lo[axis]:g} mm, "
            f"before the distance of {abs(distance):g} mm; use a smaller taper or distance",
            {"field": "taper"},
        )
    return solid


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


def join_tools(tools: Sequence[Solid]) -> Solid:
    """One compound holding the pieces of every tool (a compound tool is opened one level), for a single :func:`combine`."""
    _occ.load()
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for tool in tools:
        for piece in _children(tool._handle):
            builder.Add(compound, piece)
    return _wrap(compound)


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

    def matches(self, term: str, created: Mapping[str, Created] | None = None) -> list[bool]:
        n = len(self)
        if term == "all":
            return [True] * n
        if term.startswith(OF_PREFIX):
            return _of(term, created).edge_flags(self.edges)
        if term in ("line", "circle"):
            return [k == term for k in self.kind]
        if term in ("convex", "concave"):
            return [c == term for c in self.convexity()]
        axis = AXES[term[1]]
        if term[0] == "|":
            return [d is not None and abs(float(d[axis])) > PARALLEL for d in self.direction]
        extreme = self.hi[axis] if term[0] == ">" else self.lo[axis]
        return [abs(float(lo[axis]) - extreme) <= EXTREME_MM and abs(float(hi[axis]) - extreme) <= EXTREME_MM for lo, hi in self.box]


def _of(term: str, created: Mapping[str, Created] | None) -> Created:
    name = term[len(OF_PREFIX) :]
    if created is None or name not in created:
        raise ToolFailure("input.invalid", f"{term!r}: no recorded geometry of a feature named {name!r}; the feature must be built earlier in the same spec", {"selector": term})
    return created[name]


def _select(shape, selector: EdgeSelector, created: Mapping[str, Created] | None = None) -> tuple[list, EdgeMatch]:
    table = _Edges(shape)
    per_term: dict[str, list[bool]] = {term: table.matches(term, created) for term in selector.terms}
    chosen = [all(per_term[term][i] for term in selector.terms) for i in range(len(table))]
    edges = [edge for edge, keep in zip(table.edges, chosen) if keep]
    return edges, EdgeMatch(count=len(edges), per_term={term: sum(flags) for term, flags in per_term.items()})


def select_edges(solid: Solid, selector: EdgeSelector, created: Mapping[str, Created] | None = None) -> EdgeMatch:
    """How many edges the selector picks, and how many each term matches on its own."""
    _occ.load()
    return _select(solid._handle, selector, created)[1]


def _chosen(solid: Solid, selector: EdgeSelector, created: Mapping[str, Created] | None = None) -> list:
    edges, match = _select(solid._handle, selector, created)
    if not edges:
        raise ToolFailure(
            "geometry.empty_selection",
            f"edge selector {selector.text!r} matches no edge; edges matched by each term: {match.per_term}",
            {"selector": selector.text, "matches_per_term": match.per_term},
        )
    return edges


def _modify(solid: Solid, selector: EdgeSelector, size: float, what: str, maker_name: str, created: Mapping[str, Created] | None = None) -> tuple[Solid, int]:
    _occ.load()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet

    _positive(size, f"{what} size")
    edges = _chosen(solid, selector, created)
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


def fillet(solid: Solid, edges: EdgeSelector, radius: float, created: Mapping[str, Created] | None = None) -> tuple[Solid, int]:
    """Round the selected edges; returns the result and the number of edges used."""
    return _modify(solid, edges, radius, "fillet", "fillet", created)


def chamfer(solid: Solid, edges: EdgeSelector, distance: float, created: Mapping[str, Created] | None = None) -> tuple[Solid, int]:
    """Bevel the selected edges symmetrically; returns the result and the number of edges used."""
    return _modify(solid, edges, distance, "chamfer", "chamfer", created)


# --------------------------------------------------------------------------- mirror and shell


def mirror(solid: Solid, plane: SketchPlane) -> Solid:
    """The reflection of a solid in the plane ``xy`` (z = offset), ``xz`` (y = offset) or ``yz`` (x = offset); a copy."""
    _occ.load()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax2, gp_Trsf

    normal = _frame(plane)[2]
    if not math.isfinite(plane.offset):
        raise ToolFailure("input.invalid", f"mirror offset must be a finite length, got {plane.offset!r}")
    motion = gp_Trsf()
    motion.SetMirror(gp_Ax2(_pnt(normal * plane.offset), _direction(normal, "mirror plane")))
    try:
        reflected = BRepBuilderAPI_Transform(solid._handle, motion, True).Shape()
    except Exception as exc:  # noqa: BLE001
        raise _failure("mirror: the kernel could not reflect the shape", exc) from exc
    return _as_solid(reflected, "mirror")


def _face_table(shape) -> tuple[list, list[str], list[tuple[np.ndarray, np.ndarray]]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

    faces = _faces(shape)
    kinds, boxes = [], []
    for face in faces:
        surface = BRepAdaptor_Surface(face).GetType()
        kinds.append("plane" if surface == GeomAbs_Plane else "cylinder" if surface == GeomAbs_Cylinder else "other")
        lo, hi = _bbox(face)
        boxes.append((np.array(lo), np.array(hi)))
    return faces, kinds, boxes


def _select_faces(shape, selector: FaceSelector, created: Mapping[str, Created] | None) -> list:
    faces, kinds, boxes = _face_table(shape)
    bottom, top = (np.array(v) for v in _bbox(shape))
    chosen = [True] * len(faces)
    for term in selector.terms:
        if term == "all":
            continue
        if term.startswith(OF_PREFIX):
            flags = _of(term, created).face_flags(faces)
        elif term in ("plane", "cylinder"):
            flags = [k == term for k in kinds]
        else:
            axis = AXES[term[1]]
            extreme = top[axis] if term[0] == ">" else bottom[axis]
            flags = [abs(float(lo[axis]) - extreme) <= EXTREME_MM and abs(float(hi[axis]) - extreme) <= EXTREME_MM for lo, hi in boxes]
        chosen = [a and b for a, b in zip(chosen, flags)]
    return [face for face, keep in zip(faces, chosen) if keep]


def select_faces(solid: Solid, selector: FaceSelector, created: Mapping[str, Created] | None = None) -> int:
    """How many faces the selector picks."""
    _occ.load()
    return len(_select_faces(solid._handle, selector, created))


def shell(solid: Solid, faces: FaceSelector, thickness: float, created: Mapping[str, Created] | None = None) -> tuple[Solid, int]:
    """Hollow the solid: remove the selected faces and grow walls of ``thickness`` inward from the rest; returns the result and the faces removed.

    Corners of the cavity stay sharp (intersection join).  The result must be one valid solid with less volume than before: the
    kernel can answer a wall that is too thick with the unchanged solid or with an invalid one, and both are refused.
    """
    _occ.load()
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.collections import List_TopoDS_Shape
    from OCP.GeomAbs import GeomAbs_Intersection

    size = _positive(thickness, "shell thickness")
    shape = solid._handle
    picked = _select_faces(shape, faces, created)
    if not picked:
        raise ToolFailure("geometry.empty_selection", f"face selector {faces.text!r} matches no face of the part", {"selector": faces.text})
    lo, hi = (np.array(v) for v in _bbox(shape))
    boxes = [tuple(np.array(v) for v in _bbox(face)) for face in picked]
    for axis, name in enumerate("xyz"):
        removed = any(abs(float(f_lo[axis]) - float(e)) <= EXTREME_MM and abs(float(f_hi[axis]) - float(e)) <= EXTREME_MM for f_lo, f_hi in boxes for e in (lo[axis], hi[axis]))
        room = (hi[axis] - lo[axis]) / (1.0 if removed else 2.0)
        if size >= room * (1.0 - 1e-9):
            raise ToolFailure(
                "input.invalid",
                f"shell: a wall of {size:g} mm does not fit in the {hi[axis] - lo[axis]:g} mm of the part along {name}"
                + ("" if removed else " (walls grow inward from both sides, so the wall must be under half of it)"),
                {"field": "thickness"},
            )
    before = _volume(shape)
    removing = List_TopoDS_Shape()
    for face in picked:
        removing.Append(face)
    maker = BRepOffsetAPI_MakeThickSolid()
    try:
        maker.MakeThickSolidByJoin(shape, removing, -size, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection)
        maker.Build()
        if not maker.IsDone():
            raise _failure(f"shell of {size:g} mm removing {len(picked)} faces is not possible")
        result = maker.Shape()
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _failure(f"shell of {size:g} mm removing {len(picked)} faces is not possible", exc) from exc
    if result.IsNull():
        raise _failure(f"shell of {size:g} mm removing {len(picked)} faces gave no shape")
    hollow = _checked(result, f"shell of {size:g} mm removing {len(picked)} faces")
    after = _volume(hollow._handle)
    if not after < before * (1.0 - 1e-9):
        raise _failure(
            f"shell of {size:g} mm removing {len(picked)} faces left the volume at {after:g} mm^3 (it was {before:g}): the wall does not fit the part",
            why="the offset produced no cavity",
        )
    return hollow, len(picked)


# --------------------------------------------------------------------------- import


def import_solid(path, offset: Vec3 = (0.0, 0.0, 0.0), *, param: str | None = None) -> Solid:
    """The one solid of a STEP, IGES or BREP file (read in millimetres as :func:`read_solid` does), moved by ``offset``.

    A file that holds no closed solid (a shell, loose faces), more than one solid, or a solid the kernel finds invalid is
    refused with ``input.invalid`` (``details.field`` is ``file``).  Anything else in the file next to the solid is dropped.
    """
    _occ.load()
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from eng_toolkit.brep._files import read_solid

    prefix = f"{param}: " if param else ""
    found = read_solid(path, param=param)
    name = path.name if hasattr(path, "name") else str(path)
    if found.solids == 0:
        raise ToolFailure(
            "input.invalid",
            f"{prefix}{name!r} holds no closed solid ({found.faces} faces): import needs a solid, not a shell or loose faces",
            {"field": "file", "solids": 0, "faces": found.faces},
        )
    if found.solids > 1:
        raise ToolFailure(
            "input.invalid",
            f"{prefix}{name!r} holds {found.solids} solids: import takes exactly one; export the body you want on its own",
            {"field": "file", "solids": found.solids},
        )
    if found.roots_transferred < found.roots_in_file:
        raise ToolFailure(
            "input.invalid",
            f"{prefix}only {found.roots_transferred} of {found.roots_in_file} root entities of {name!r} could be transferred, so the solid may be incomplete",
            {"field": "file"},
        )
    explorer = TopExp_Explorer(found._handle, TopAbs_SOLID)
    shape = TopoDS.Solid(explorer.Current())
    volume = _volume(shape)
    if volume < 0.0:
        shape = shape.Reversed()
        volume = -volume
    if not volume > 0.0 or not _valid(shape):
        raise ToolFailure("input.invalid", f"{prefix}the solid in {name!r} has no volume or fails the kernel's validity check", {"field": "file"})
    solid = _wrap(shape)
    if any(float(c) != 0.0 for c in offset):
        solid = transform(solid, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), offset)
    return solid
