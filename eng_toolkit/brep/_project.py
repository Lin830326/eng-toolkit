"""Hidden-line projection of a B-rep into clean 2D drawing primitives.

Lengths are millimetres in view coordinates, ``(u, v) = (p . x_axis, p . y_axis)``; angles are radians.  The exact
algorithm (``HLRBRep_Algo``) is the default; a shape with more faces than ``exact_face_limit`` falls back to
``HLRBRep_PolyAlgo`` on a tessellation, which returns only straight lines.

OCCT returns more than a drawing wants, so the result is cleaned.  Duplicates are dropped, and collinear and
concyclic pieces of equal visibility are merged.  A hidden piece that lies under visible ones is dropped, because
OCCT reports every back edge that coincides with a front edge as hidden.  Smooth edges (the tangent lines between a
fillet and its neighbour, and cylinder and sphere seams) are not drawn and are only counted; a seam that lies on a
silhouette comes back among the visible edges, so it is kept.

Known fault of the exact algorithm: the silhouette of a fillet around a hole rim can come back visible although
a face in front of it hides it (tests/test_brep_project.py pins the case).  The polygonal algorithm gets it right,
so the exact result is verified against it: the part is tessellated, projected polygonally, and used only to answer
"is this point of an exact visible primitive visible?".  A stretch of a visible primitive that the polygonal visible
lines do not cover moves to the hidden lines when the polygonal hidden lines cover it, and is dropped otherwise.
The exact primitives stay the geometry; nothing the polygonal result has and the exact one lacks is added.  When the
two disagree by more than a few percent the exact result is returned unchanged and marked ``exact_unverified``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

import numpy as np

from eng_toolkit.brep import _occ
from eng_toolkit.brep._files import _count
from eng_toolkit.brep._types import Solid, Vec3
from eng_toolkit.errors import ToolFailure

Vec2 = tuple[float, float]
ViewName = Literal["front", "back", "top", "bottom", "left", "right", "iso"]

TWO_PI = 2.0 * math.pi
DIGITS = 9  # decimals of a coordinate in mm
ANGLE_DIGITS = 12
COVER_MM = 1e-3  # a hidden piece this close to visible lines is under them
MERGE_MM = 1e-4  # pieces closer than this touch; the quantum of the duplicate key
ANGLE_TOL = 1e-6  # directions closer than this (rad) are parallel when merging lines
CURVE_DEFLECTION_MM = 0.01  # sampling of curves that are neither lines, circles nor ellipses, and the Line2 test
FLAT_MM = 2e-4  # chord error of a visible arc flattened for the coverage test
SAMPLE_GAP_MM = 0.5  # largest gap between the samples of a hidden piece
MIN_LENGTH_MM = 1e-6
POLY_DEFLECTION_MM = 0.01
POLY_ANGLE_RAD = 0.2
BLOCK = 1_000_000  # distances computed at once in the coverage test

# The visibility check.  The tessellation deflection bounds the chord error, so a silhouette of the tessellated part
# lies at most that far from the true one; a point is covered when it is within VERIFY_CHORD_FACTOR times that of a
# polygonal line, but never less than VERIFY_FLOOR_MM.  The deflection is 1e-4 of the part's diagonal, between the
# 0.01 mm of the polygonal fallback and 0.03 mm: coarser would let the tolerance (0.09 mm) approach the width of a
# drawn line, finer costs time for nothing the check can resolve.
VERIFY_DEFLECTION_RATIO = 1e-4
VERIFY_MIN_DEFLECTION_MM = 0.01
VERIFY_MAX_DEFLECTION_MM = 0.03
VERIFY_ANGLE_RAD = 0.5
VERIFY_FLOOR_MM = 0.02
VERIFY_CHORD_FACTOR = 3.0
VERIFY_MIN_SAMPLES = 5
VERIFY_GAP_MM = 2.0  # largest distance between the samples of a primitive
VERIFY_BISECTIONS = 10
VERIFY_DROP_LIMIT = 0.02  # share of the visible length that may be dropped, or that the polygonal result may have extra
VERIFY_MOVE_LIMIT = 0.5  # share of the visible length that may move to hidden: those pieces have a hidden polygonal line under them
GRID_MM = 1.0  # smallest cell of the index of polygonal segments
QUERY_BLOCK = 4096  # points looked up at once


@dataclass(frozen=True)
class ViewFrame:
    normal: Vec3  # unit, towards the viewer
    x_axis: Vec3  # model direction drawn to the right
    y_axis: Vec3  # model direction drawn up: normal x x_axis


def _frame(normal: Vec3, x_axis: Vec3) -> ViewFrame:
    n = np.asarray(normal, dtype=np.float64)
    x = np.asarray(x_axis, dtype=np.float64)
    n, x = n / np.linalg.norm(n), x / np.linalg.norm(x)
    y = np.cross(n, x)

    def clean(v: np.ndarray) -> Vec3:
        return (float(v[0]) + 0.0, float(v[1]) + 0.0, float(v[2]) + 0.0)  # + 0.0 turns -0.0 into 0.0

    return ViewFrame(clean(n), clean(x), clean(y))


# The one table of view frames.  The projector is built from (normal, x_axis) as gp_Ax2(origin, normal, x_axis):
# the third argument of gp_Ax2 is the X direction, not "up".
VIEW_FRAMES: dict[ViewName, ViewFrame] = {
    "front": _frame((0, -1, 0), (1, 0, 0)),
    "back": _frame((0, 1, 0), (-1, 0, 0)),
    "top": _frame((0, 0, 1), (1, 0, 0)),
    "bottom": _frame((0, 0, -1), (1, 0, 0)),
    "right": _frame((1, 0, 0), (0, 1, 0)),
    "left": _frame((-1, 0, 0), (0, -1, 0)),
    "iso": _frame((1, -1, 1), (1, 1, 0)),
}


@dataclass(frozen=True)
class Line2:
    a: Vec2
    b: Vec2


@dataclass(frozen=True)
class Arc2:
    """Counter-clockwise from ``start_rad`` (in [0, 2*pi)) to ``end_rad`` (``start_rad`` + span); a full circle spans 2*pi."""

    center: Vec2
    radius: float
    start_rad: float
    end_rad: float


@dataclass(frozen=True)
class Ellipse2:
    """Counter-clockwise; ``major_axis`` is the vector from the centre to the end of the major axis, the angles are curve parameters."""

    center: Vec2
    major_axis: Vec2
    ratio: float  # minor radius / major radius
    start_rad: float
    end_rad: float


@dataclass(frozen=True)
class Poly2:
    points: tuple[Vec2, ...]


Prim2 = Line2 | Arc2 | Ellipse2 | Poly2


@dataclass(frozen=True)
class Projection:
    view: ViewName
    direction: Vec3  # from the eye towards the part
    x_axis: Vec3  # model direction drawn as view +x
    y_axis: Vec3  # model direction drawn as view +y
    visible: tuple[Prim2, ...]  # sharp edges and silhouettes
    hidden: tuple[Prim2, ...]  # without the pieces that lie under a visible primitive
    bbox_min: Vec2  # of the visible primitives, in view coordinates
    bbox_max: Vec2
    dropped: dict[str, int]
    """hidden_coincident: hidden pieces under visible ones; tangent: smooth edges (G1); seam: edges of continuity CN
    (cylinder seams); verified_hidden: stretches of exact visible primitives the polygonal check moved to the hidden
    lines; verified_removed: stretches it dropped because no polygonal line, visible or hidden, lies there."""
    method: Literal["exact", "exact_unverified", "polygonal"]
    """``exact``: the exact algorithm, checked against the polygonal one.  ``exact_unverified``: the exact algorithm
    and the check could not confirm it (the polygonal projection failed, would have dropped or hidden too much of the
    result, or has visible lines the exact one lacks); the exact result is returned as it was.  ``polygonal``: more
    faces than ``exact_face_limit``, so the whole projection is polygonal."""
    approximated: int = 0  # curves replaced by a Poly2
    verified: bool = False  # the exact result was checked against the polygonal projection and kept or corrected


# --------------------------------------------------------------------------- projecting


def project(solid: Solid, view: ViewName, *, hidden: bool = True, exact_face_limit: int = 5000) -> Projection:
    """Project ``solid`` along the fixed frame of ``view`` into lines, arcs, ellipses and polylines.

    ``hidden=False`` returns no hidden primitives.  A shape with no faces raises ``drawing.no_geometry``; a
    kernel failure raises ``drawing.projection_failed``.
    """
    frame = VIEW_FRAMES.get(view)
    if frame is None:
        raise ToolFailure("input.invalid", f"view {view!r} is not one of {list(VIEW_FRAMES)}", {"view": view, "allowed": list(VIEW_FRAMES)})
    _occ.load()
    shape = solid._handle
    faces = _face_count(shape)
    if faces == 0:
        raise ToolFailure("drawing.no_geometry", f"nothing to project: the shape has no faces ({view} view)", {"view": view})
    method = "exact" if faces <= exact_face_limit else "polygonal"
    verified = False
    moved = removed = 0
    try:
        raw = _run_hlr(shape, frame, method)
        sampled: set[Prim2] = set()
        visible = [p for edge in raw.visible for p in _prims(edge, sampled)]
        hidden_prims = [p for edge in raw.hidden for p in _prims(edge, sampled)] if hidden else []
        visible, kept, covered = _clean(visible, hidden_prims, sampled)
        if method == "exact":
            verdict = _verify(shape, frame, visible, sampled)
            if verdict is None:
                method = "exact_unverified"
            else:
                verified = True
                moved, removed = len(verdict.moved), verdict.removed
                if verdict.changed:
                    visible = verdict.visible
                    sampled.update(p for p in (*visible, *verdict.moved) if isinstance(p, Poly2))
                    kept, again = _rehide(kept, verdict.moved if hidden else [], visible, sampled)
                    covered += again
    except ToolFailure:
        raise
    except Exception as exc:  # OCCT raises its own exception types
        raise ToolFailure(
            "drawing.projection_failed",
            f"the {view} view could not be projected ({method} algorithm): {exc}",
            {"view": view, "method": method, "occt": str(exc)},
        ) from exc
    visible_sorted = tuple(sorted(visible, key=_order))
    hidden_sorted = tuple(sorted(kept, key=_order))
    lo, hi = _bounds(visible_sorted)
    return Projection(
        view=view,
        direction=tuple(-c + 0.0 for c in frame.normal),  # type: ignore[arg-type]
        x_axis=frame.x_axis,
        y_axis=frame.y_axis,
        visible=visible_sorted,
        hidden=hidden_sorted,
        bbox_min=lo,
        bbox_max=hi,
        dropped={"hidden_coincident": covered, "tangent": raw.tangent, "seam": raw.seam, "verified_hidden": moved, "verified_removed": removed},
        method=method,
        approximated=sum(isinstance(p, Poly2) for p in (*visible_sorted, *hidden_sorted)),
        verified=verified,
    )


def _face_count(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE

    if shape is None or shape.IsNull():
        return 0
    return _count(shape, TopAbs_FACE)


@dataclass
class _Raw:
    visible: list  # TopoDS_Edge: sharp edges and silhouettes
    hidden: list
    tangent: int  # smooth (Rg1) edges, visible and hidden
    seam: int  # edges of continuity CN (cylinder and sphere seams), visible and hidden


def _projector(frame: ViewFrame):
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector

    return HLRAlgo_Projector(gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(*frame.normal), gp_Dir(*frame.x_axis)))


def _edges(compound) -> Iterator:
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if compound is None or compound.IsNull():
        return
    explorer = TopExp_Explorer(compound, TopAbs_EDGE)
    while explorer.More():
        yield TopoDS.Edge(explorer.Current())
        explorer.Next()


def _poly_hlr(shape, frame: ViewFrame, deflection: float, angle: float):
    """HLRBRep_PolyHLRToShape of the shape tessellated to ``deflection`` mm and ``angle`` rad (the tessellation stays on the shape)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape

    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    poly = HLRBRep_PolyAlgo()
    poly.Load(shape)
    poly.Projector(_projector(frame))
    poly.Update()
    out = HLRBRep_PolyHLRToShape()
    out.Update(poly)
    return out


def _run_hlr(shape, frame: ViewFrame, method: str) -> _Raw:
    if method == "exact":
        from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(_projector(frame))
        algo.Update()
        algo.Hide()
        out = HLRBRep_HLRToShape(algo)
    else:
        out = _poly_hlr(shape, frame, POLY_DEFLECTION_MM, POLY_ANGLE_RAD)
    return _Raw(
        # the outline compounds carry the silhouettes
        visible=[*_edges(out.VCompound()), *_edges(out.OutLineVCompound())],
        hidden=[*_edges(out.HCompound()), *_edges(out.OutLineHCompound())],
        tangent=sum(1 for c in (out.Rg1LineVCompound(), out.Rg1LineHCompound()) for _ in _edges(c)),
        seam=sum(1 for c in (out.RgNLineVCompound(), out.RgNLineHCompound()) for _ in _edges(c)),
    )


# --------------------------------------------------------------------------- edges to primitives


def _r(value: float) -> float:
    return round(float(value), DIGITS) + 0.0


def _angle(value: float) -> float:
    return round(float(value), ANGLE_DIGITS) + 0.0


def _undirected(p: Vec2, q: Vec2) -> Line2:
    return Line2(*sorted((p, q)))  # the smaller end first, so a line has one spelling


def _line(a, b) -> Line2 | None:
    p, q = (_r(a[0]), _r(a[1])), (_r(b[0]), _r(b[1]))
    return None if math.dist(p, q) < MIN_LENGTH_MM else _undirected(p, q)


def _arc(center, radius: float, start: float, span: float) -> Arc2 | None:
    if radius * span < MIN_LENGTH_MM:
        return None
    c = (_r(center[0]), _r(center[1]))
    if span >= TWO_PI - 1e-9:
        return Arc2(c, _r(radius), 0.0, _angle(TWO_PI))
    s = start % TWO_PI
    return Arc2(c, _r(radius), _angle(s), _angle(s + span))


def _poly(points: np.ndarray) -> Poly2:
    ring = tuple((_r(x), _r(y)) for x, y in points)
    return Poly2(min(ring, ring[::-1]))  # undirected


def _distance_to_segment(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(float(ab @ ab), 1e-30), 0.0, 1.0)
    return np.linalg.norm(points - (a + t[:, None] * ab), axis=1)


def _line_or_poly(points: np.ndarray) -> Prim2 | None:
    """A sampled curve is a line when it is straight, also when it runs there and back (a circle seen edge on)."""
    far = points[int(np.argmax(np.linalg.norm(points - points[0], axis=1)))]
    other = points[int(np.argmax(np.linalg.norm(points - far, axis=1)))]
    if np.linalg.norm(far - other) < MIN_LENGTH_MM:
        return None
    if float(_distance_to_segment(points, other, far).max()) <= CURVE_DEFLECTION_MM:
        return _line(other, far)
    return _poly(points)


def _prims(edge, sampled: set[Prim2]) -> list[Prim2]:
    """The primitive of one edge; a curve replaced by samples is also added to ``sampled``."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Line

    curve = BRepAdaptor_Curve(edge)
    kind = curve.GetType()
    first, last = curve.FirstParameter(), curve.LastParameter()
    span = last - first
    if kind == GeomAbs_Line:
        found = _line((curve.Value(first).X(), curve.Value(first).Y()), (curve.Value(last).X(), curve.Value(last).Y()))
    elif kind == GeomAbs_Circle:
        circle = curve.Circle()
        centre = circle.Location()
        x = circle.XAxis().Direction()
        base = math.atan2(x.Y(), x.X())
        if circle.Axis().Direction().Z() > 0.0:
            found = _arc((centre.X(), centre.Y()), circle.Radius(), base + first, span)
        else:  # seen from behind the circle runs clockwise: its counter-clockwise arc starts at the far end
            found = _arc((centre.X(), centre.Y()), circle.Radius(), base - last, span)
    elif kind == GeomAbs_Ellipse:
        ellipse = curve.Ellipse()
        centre = ellipse.Location()
        x = ellipse.XAxis().Direction()
        major = ellipse.MajorRadius()
        start = first if ellipse.Axis().Direction().Z() > 0.0 else -last
        full = span >= TWO_PI - 1e-9
        s = 0.0 if full else start % TWO_PI
        found = Ellipse2(
            (_r(centre.X()), _r(centre.Y())),
            (_r(major * x.X()), _r(major * x.Y())),
            _angle(ellipse.MinorRadius() / major),
            _angle(s),
            _angle(TWO_PI if full else s + span),
        )
    else:
        sampler = GCPnts_QuasiUniformDeflection(curve, CURVE_DEFLECTION_MM)
        if sampler.IsDone() and sampler.NbPoints() >= 2:
            points = np.array([(sampler.Value(i).X(), sampler.Value(i).Y()) for i in range(1, sampler.NbPoints() + 1)])
        else:
            points = np.array([(p.X(), p.Y()) for p in (curve.Value(first + span * i / 64) for i in range(65))])
        found = _line_or_poly(points)
        if found is not None:
            sampled.add(found)
    return [] if found is None else [found]


# --------------------------------------------------------------------------- geometry of primitives


def _arc_points(center: Vec2, radius: float, start: float, end: float, count: int) -> np.ndarray:
    t = np.linspace(start, end, count + 1)
    return np.column_stack((center[0] + radius * np.cos(t), center[1] + radius * np.sin(t)))


def _ellipse_at(e: Ellipse2, parameters: np.ndarray) -> np.ndarray:
    a = math.hypot(*e.major_axis)
    ux, uy = e.major_axis[0] / a, e.major_axis[1] / a
    major, minor = a * np.cos(parameters), a * e.ratio * np.sin(parameters)
    return np.column_stack((e.center[0] + major * ux - minor * uy, e.center[1] + major * uy + minor * ux))


def _ellipse_points(e: Ellipse2, count: int) -> np.ndarray:
    return _ellipse_at(e, np.linspace(e.start_rad, e.end_rad, count + 1))


def _flatten(prim: Prim2, tolerance: float) -> np.ndarray:
    """Vertices of a polyline within ``tolerance`` of the primitive."""
    if isinstance(prim, Line2):
        return np.array([prim.a, prim.b])
    if isinstance(prim, Poly2):
        return np.array(prim.points)
    if isinstance(prim, Arc2):
        radius, span = prim.radius, prim.end_rad - prim.start_rad
        step = 2.0 * math.acos(max(1.0 - tolerance / radius, -1.0))
        return _arc_points(prim.center, radius, prim.start_rad, prim.end_rad, max(2, math.ceil(span / max(step, 1e-6))))
    a, span = math.hypot(*prim.major_axis), prim.end_rad - prim.start_rad
    step = math.sqrt(8.0 * tolerance / a)  # the deviation of a chord is at most a * step^2 / 8
    return _ellipse_points(prim, max(8, math.ceil(span / max(step, 1e-6))))


def _samples(prim: Prim2) -> np.ndarray:
    """Points along a primitive, at most SAMPLE_GAP_MM apart; the midpoints of the gaps are among them."""
    if isinstance(prim, Line2):
        count = max(2, math.ceil(math.dist(prim.a, prim.b) / SAMPLE_GAP_MM))
        return np.linspace(prim.a, prim.b, count + 1)
    if isinstance(prim, Arc2):
        span = prim.end_rad - prim.start_rad
        return _arc_points(prim.center, prim.radius, prim.start_rad, prim.end_rad, max(2, math.ceil(prim.radius * span / SAMPLE_GAP_MM)))
    if isinstance(prim, Ellipse2):
        span = prim.end_rad - prim.start_rad
        return _ellipse_points(prim, max(2, math.ceil(math.hypot(*prim.major_axis) * span / SAMPLE_GAP_MM)))
    return np.array(prim.points)  # the vertices lie on the curve, the chords between them do not


def _param_extent(centre: float, cos_coefficient: float, sin_coefficient: float, start: float, end: float) -> tuple[float, float]:
    """Extremes of centre + A cos t + B sin t for t from start to end."""
    if end - start >= TWO_PI - 1e-9:
        amplitude = math.hypot(cos_coefficient, sin_coefficient)
        return centre - amplitude, centre + amplitude
    values = [centre + cos_coefficient * math.cos(t) + sin_coefficient * math.sin(t) for t in (start, end)]
    peak = math.atan2(sin_coefficient, cos_coefficient)
    for t in (peak, peak + math.pi):
        if (t - start) % TWO_PI <= end - start:
            values.append(centre + cos_coefficient * math.cos(t) + sin_coefficient * math.sin(t))
    return min(values), max(values)


def _extent(prim: Prim2) -> tuple[float, float, float, float]:
    if isinstance(prim, Line2):
        return min(prim.a[0], prim.b[0]), min(prim.a[1], prim.b[1]), max(prim.a[0], prim.b[0]), max(prim.a[1], prim.b[1])
    if isinstance(prim, Poly2):
        xs, ys = zip(*prim.points)
        return min(xs), min(ys), max(xs), max(ys)
    if isinstance(prim, Arc2):
        x0, x1 = _param_extent(prim.center[0], prim.radius, 0.0, prim.start_rad, prim.end_rad)
        y0, y1 = _param_extent(prim.center[1], 0.0, prim.radius, prim.start_rad, prim.end_rad)
        return x0, y0, x1, y1
    a = math.hypot(*prim.major_axis)
    ux, uy = prim.major_axis[0] / a, prim.major_axis[1] / a
    b = a * prim.ratio
    x0, x1 = _param_extent(prim.center[0], a * ux, -b * uy, prim.start_rad, prim.end_rad)
    y0, y1 = _param_extent(prim.center[1], a * uy, b * ux, prim.start_rad, prim.end_rad)
    return x0, y0, x1, y1


def _bounds(prims: tuple[Prim2, ...]) -> tuple[Vec2, Vec2]:
    if not prims:
        return (0.0, 0.0), (0.0, 0.0)
    boxes = [_extent(p) for p in prims]
    return (
        (_r(min(b[0] for b in boxes)), _r(min(b[1] for b in boxes))),
        (_r(max(b[2] for b in boxes)), _r(max(b[3] for b in boxes))),
    )


def _order(prim: Prim2) -> tuple:
    """A total order on primitives, so the result does not depend on the order OCCT returns edges in."""
    if isinstance(prim, Line2):
        return (0, prim.a, prim.b)
    if isinstance(prim, Arc2):
        return (1, prim.center, prim.radius, prim.start_rad, prim.end_rad)
    if isinstance(prim, Ellipse2):
        return (2, prim.center, prim.major_axis, prim.ratio, prim.start_rad, prim.end_rad)
    return (3, prim.points)


# --------------------------------------------------------------------------- cleaning


def _q(value: float) -> int:
    return round(value / MERGE_MM)


def _key(prim: Prim2) -> tuple:
    """Direction-agnostic quantised identity: two primitives with one key are the same drawn line."""
    if isinstance(prim, Line2):
        return ("line", *sorted(((_q(prim.a[0]), _q(prim.a[1])), (_q(prim.b[0]), _q(prim.b[1])))))
    if isinstance(prim, Arc2):
        return ("arc", _q(prim.center[0]), _q(prim.center[1]), _q(prim.radius), round(prim.start_rad, 5), round(prim.end_rad, 5))
    if isinstance(prim, Ellipse2):
        return ("ellipse", _q(prim.center[0]), _q(prim.center[1]), _q(prim.major_axis[0]), _q(prim.major_axis[1]), round(prim.ratio, 6), round(prim.start_rad, 5), round(prim.end_rad, 5))
    ring = tuple((_q(x), _q(y)) for x, y in prim.points)
    return ("poly", min(ring, ring[::-1]))


def _dedupe(prims: list[Prim2]) -> list[Prim2]:
    seen: set[tuple] = set()
    kept = []
    for prim in prims:
        key = _key(prim)
        if key not in seen:
            seen.add(key)
            kept.append(prim)
    return kept


def _runs(items: list, key, tolerance: float) -> list[list]:
    """Items sorted by ``key`` and cut wherever two neighbours are further apart than ``tolerance``."""
    runs: list[list] = []
    last = None
    for item in sorted(items, key=key):
        value = key(item)
        if last is None or value - last > tolerance:
            runs.append([])
        runs[-1].append(item)
        last = value
    return runs


@dataclass
class _Span:
    theta: float  # direction of the line in (-ANGLE_TOL, pi - ANGLE_TOL)
    rho: float  # signed distance of the line from the origin
    low: float  # position along the direction of the first end
    high: float
    first: Vec2
    last: Vec2


def _span(line: Line2) -> _Span:
    dx, dy = line.b[0] - line.a[0], line.b[1] - line.a[1]
    theta = math.atan2(dy, dx) % math.pi
    if theta > math.pi - ANGLE_TOL:
        theta -= math.pi
    ux, uy = math.cos(theta), math.sin(theta)
    ta, tb = line.a[0] * ux + line.a[1] * uy, line.b[0] * ux + line.b[1] * uy
    rho = -line.a[0] * uy + line.a[1] * ux
    return _Span(theta, rho, ta, tb, line.a, line.b) if ta <= tb else _Span(theta, rho, tb, ta, line.b, line.a)


def _merge_lines(lines: list[Line2]) -> list[Prim2]:
    merged: list[Prim2] = []
    for same_direction in _runs([_span(line) for line in lines], lambda s: s.theta, ANGLE_TOL):
        for same_line in _runs(same_direction, lambda s: s.rho, MERGE_MM):
            same_line.sort(key=lambda s: (s.low, s.high))
            current = same_line[0]
            for piece in same_line[1:]:
                if piece.low <= current.high + MERGE_MM:  # touching or overlapping
                    if piece.high > current.high:
                        current.high, current.last = piece.high, piece.last
                else:
                    merged.append(_undirected(current.first, current.last))
                    current = piece
            merged.append(_undirected(current.first, current.last))
    return merged


def _merge_arcs(arcs: list[Arc2]) -> list[Prim2]:
    merged: list[Prim2] = []
    for same_radius in _runs(arcs, lambda a: a.radius, MERGE_MM):
        for same_x in _runs(same_radius, lambda a: a.center[0], MERGE_MM):
            for same_circle in _runs(same_x, lambda a: a.center[1], MERGE_MM):
                reference = same_circle[0]
                tolerance = MERGE_MM / reference.radius
                spans = sorted((a.start_rad % TWO_PI, a.end_rad - a.start_rad) for a in same_circle)
                if max(span for _, span in spans) >= TWO_PI - tolerance:
                    merged.append(Arc2(reference.center, reference.radius, 0.0, _angle(TWO_PI)))
                    continue
                runs: list[list[float]] = []
                for start, span in spans:
                    if runs and start <= runs[-1][1] + tolerance:
                        runs[-1][1] = max(runs[-1][1], start + span)
                    else:
                        runs.append([start, start + span])
                if len(runs) > 1 and runs[-1][1] >= runs[0][0] + TWO_PI - tolerance:  # the last run continues into the first
                    last = runs.pop()
                    runs[0] = [last[0], max(last[1], runs[0][1] + TWO_PI)]
                for start, end in runs:
                    if end - start >= TWO_PI - tolerance:
                        merged.append(Arc2(reference.center, reference.radius, 0.0, _angle(TWO_PI)))
                    else:
                        merged.append(Arc2(reference.center, reference.radius, _angle(start), _angle(end)))
    return merged


def _merge(prims: list[Prim2]) -> list[Prim2]:
    lines = [p for p in prims if isinstance(p, Line2)]
    arcs = [p for p in prims if isinstance(p, Arc2)]
    rest = [p for p in prims if not isinstance(p, (Line2, Arc2))]
    return [*_merge_lines(lines), *_merge_arcs(arcs), *rest]


class _Cover:
    """Does a primitive lie within COVER_MM of the primitives added so far?

    They are held as short straight segments.  A curve that was replaced by samples may be CURVE_DEFLECTION_MM off
    the true curve, so the distance to such a primitive, and from one, is allowed that much more.
    """

    def __init__(self) -> None:
        self._blocks: list[np.ndarray] = []
        self._slack: list[np.ndarray] = []
        self._built: tuple[np.ndarray, ...] | None = None

    def add(self, prim: Prim2, sampled: bool) -> None:
        points = _flatten(prim, FLAT_MM)
        self._blocks.append(np.hstack((points[:-1], points[1:])))
        self._slack.append(np.full(len(points) - 1, CURVE_DEFLECTION_MM if sampled else 0.0))
        self._built = None

    def _arrays(self) -> tuple[np.ndarray, ...]:
        if self._built is None:
            segments = np.vstack(self._blocks) if self._blocks else np.empty((0, 4))
            a, b = segments[:, :2], segments[:, 2:]
            self._built = (a, b, np.minimum(a, b), np.maximum(a, b), np.concatenate(self._slack) if self._slack else np.empty(0))
        return self._built

    def covers(self, prim: Prim2, sampled: bool) -> bool:
        a, b, low, high, slack = self._arrays()
        if not len(a):
            return False
        allowed = COVER_MM + (CURVE_DEFLECTION_MM if sampled else 0.0)
        points = _samples(prim)
        reach = allowed + CURVE_DEFLECTION_MM
        near = (high >= points.min(axis=0) - reach).all(axis=1) & (low <= points.max(axis=0) + reach).all(axis=1)
        if not near.any():
            return False
        a, b, slack = a[near], b[near], slack[near]
        ab = b - a
        length2 = np.maximum((ab * ab).sum(axis=1), 1e-30)
        block = max(1, BLOCK // len(a))
        for start in range(0, len(points), block):
            chunk = points[start : start + block]
            offset = chunk[:, None, :] - a[None, :, :]
            t = np.clip((offset * ab[None, :, :]).sum(axis=2) / length2, 0.0, 1.0)
            distance = np.linalg.norm(offset - t[:, :, None] * ab[None, :, :], axis=2)
            if not ((distance - slack).min(axis=1) <= allowed).all():
                return False
        return True


def _length(prim: Prim2) -> float:
    points = _flatten(prim, FLAT_MM)
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _clean(visible: list[Prim2], hidden: list[Prim2], sampled: set[Prim2]) -> tuple[list[Prim2], list[Prim2], int]:
    """Visible primitives deduplicated and merged; hidden ones that lie under them dropped (their count is returned), the rest merged.

    OCCT cuts a curve into visible and hidden fragments where it runs exactly under another curve (the back half
    of an intersection seen from a plane of symmetry), so a sampled visible curve that lies under longer
    visible ones is dropped too.
    """
    visible = _merge(_dedupe(visible))
    cover = _Cover()
    for prim in visible:
        if prim not in sampled:
            cover.add(prim, False)
    kept_visible = [p for p in visible if p not in sampled]
    for prim in sorted((p for p in visible if p in sampled), key=lambda p: (-_length(p), _order(p))):
        if not cover.covers(prim, True):
            cover.add(prim, True)
            kept_visible.append(prim)
    kept, covered = _hide_under(hidden, kept_visible, cover, sampled)
    return kept_visible, kept, covered


def _hide_under(hidden: list[Prim2], visible: list[Prim2], cover: _Cover, sampled: set[Prim2]) -> tuple[list[Prim2], int]:
    """The hidden primitives that do not lie under the visible ones, merged, and how many did."""
    keys = {_key(p) for p in visible}
    kept: list[Prim2] = []
    covered = 0
    for prim in _dedupe(hidden):
        if _key(prim) in keys or cover.covers(prim, prim in sampled):
            covered += 1
        else:
            kept.append(prim)
    return _merge(kept), covered


def _rehide(hidden: list[Prim2], moved: list[Prim2], visible: list[Prim2], sampled: set[Prim2]) -> tuple[list[Prim2], int]:
    """``hidden`` and the pieces ``moved`` to it, without those that lie under the (corrected) ``visible`` ones."""
    cover = _Cover()
    for prim in visible:
        cover.add(prim, prim in sampled)
    return _hide_under([*hidden, *moved], visible, cover, sampled)


# --------------------------------------------------------------------------- the check against the polygonal projection


class _OracleFailure(Exception):
    """The polygonal projection could not be read."""


@dataclass
class _Oracle:
    tolerance: float  # mm: a point this close to a polygonal line is on it
    visible: np.ndarray  # (n, 4): x0, y0, x1, y1 of the polygonal visible lines
    hidden: Callable[[], np.ndarray]  # the polygonal hidden lines, read when a primitive turns out not to be visible


def _deflection(shape) -> float:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    diagonal = 0.0 if box.IsVoid() else box.CornerMin().Distance(box.CornerMax())
    return min(max(VERIFY_DEFLECTION_RATIO * diagonal, VERIFY_MIN_DEFLECTION_MM), VERIFY_MAX_DEFLECTION_MM)


def _segments(edges: list) -> np.ndarray:
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS_Vertex

    rows = np.empty((len(edges), 4))
    first, last = TopoDS_Vertex(), TopoDS_Vertex()
    for i, edge in enumerate(edges):
        TopExp.Vertices_s(edge, first, last)
        p, q = BRep_Tool.Pnt_s(first), BRep_Tool.Pnt_s(last)
        rows[i] = (p.X(), p.Y(), q.X(), q.Y())
    return rows


def _oracle(shape, frame: ViewFrame) -> _Oracle:
    """The polygonal projection of the shape; only its lines are read, and the hidden ones only on demand."""
    deflection = _deflection(shape)
    out = _poly_hlr(shape, frame, deflection, VERIFY_ANGLE_RAD)
    visible = _segments([*_edges(out.VCompound()), *_edges(out.OutLineVCompound())])

    def hidden() -> np.ndarray:
        try:
            return _segments([*_edges(out.HCompound()), *_edges(out.OutLineHCompound())])
        except Exception as exc:  # OCCT raises its own exception types
            raise _OracleFailure(str(exc)) from exc

    return _Oracle(max(VERIFY_FLOOR_MM, VERIFY_CHORD_FACTOR * deflection), visible, hidden)


def _ranges(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For ``counts[i]`` items each: the index i of every item and its number within i."""
    owner = np.repeat(np.arange(len(counts)), counts)
    return owner, np.arange(owner.size) - np.repeat(np.cumsum(counts) - counts, counts)


def _cell_key(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
    return (ix + (1 << 30)) * (1 << 31) + (iy + (1 << 30))


class _Segments:
    """Is a point within a distance of any of many segments?  A uniform grid, so a lookup meets only the segments near it.

    Segments are cut into pieces no longer than a cell, and each piece is entered in every cell its bounding box
    reaches when grown by ``reach``, the largest distance that will be asked about.
    """

    def __init__(self, segments: np.ndarray, reach: float) -> None:
        self.reach = reach
        self._cell = max(GRID_MM, 4.0 * reach)
        a, b = segments[:, :2], segments[:, 2:]
        pieces = np.maximum(1, np.ceil(np.linalg.norm(b - a, axis=1) / self._cell)).astype(np.int64)
        owner, step = _ranges(pieces)
        along = (b - a)[owner]
        self._p = a[owner] + along * (step / pieces[owner])[:, None]
        self._q = a[owner] + along * ((step + 1) / pieces[owner])[:, None]
        low = np.floor((np.minimum(self._p, self._q) - reach) / self._cell).astype(np.int64)
        high = np.floor((np.maximum(self._p, self._q) + reach) / self._cell).astype(np.int64)
        keys, ids = [], []
        for dx in range(3):  # a piece is at most one cell long, so its grown box spans at most three
            for dy in range(3):
                ix, iy = low[:, 0] + dx, low[:, 1] + dy
                inside = (ix <= high[:, 0]) & (iy <= high[:, 1])
                keys.append(_cell_key(ix[inside], iy[inside]))
                ids.append(np.flatnonzero(inside))
        keys, ids = np.concatenate(keys), np.concatenate(ids)
        order = np.argsort(keys, kind="stable")
        self._keys, self._ids = keys[order], ids[order]

    def within(self, points: np.ndarray, allowed: float | np.ndarray) -> np.ndarray:
        """Per point: is it at most ``allowed`` (a distance, or one per point) from a segment?"""
        limit = np.broadcast_to(np.asarray(allowed, dtype=np.float64), (len(points),))
        if len(points) and float(limit.max()) > self.reach + 1e-12:
            raise ValueError(f"asked about {float(limit.max())} mm but the index reaches {self.reach} mm")
        found = np.zeros(len(points), dtype=bool)
        if not self._keys.size:
            return found
        for start in range(0, len(points), QUERY_BLOCK):
            chunk = points[start : start + QUERY_BLOCK]
            key = _cell_key(np.floor(chunk[:, 0] / self._cell).astype(np.int64), np.floor(chunk[:, 1] / self._cell).astype(np.int64))
            first = np.searchsorted(self._keys, key, side="left")
            who, step = _ranges(np.searchsorted(self._keys, key, side="right") - first)
            if not who.size:
                continue
            piece = self._ids[first[who] + step]
            p, along = self._p[piece], self._q[piece] - self._p[piece]
            offset = chunk[who] - p
            t = np.clip((offset * along).sum(axis=1) / np.maximum((along * along).sum(axis=1), 1e-30), 0.0, 1.0)
            close = np.linalg.norm(offset - t[:, None] * along, axis=1) <= limit[start + who]
            found[start + who[close]] = True
        return found


def _plain_length(prim: Prim2) -> float:
    """Length of a primitive; exact for all but the ellipse."""
    if isinstance(prim, Line2):
        return math.dist(prim.a, prim.b)
    if isinstance(prim, Arc2):
        return prim.radius * (prim.end_rad - prim.start_rad)
    if isinstance(prim, Poly2):
        return float(np.linalg.norm(np.diff(np.array(prim.points), axis=0), axis=1).sum())
    return _length(prim)


def _fractions(length: float) -> np.ndarray:
    """Where along a primitive of ``length`` to look, as fractions of it: odd in number so the middle one is the midpoint,
    at least VERIFY_MIN_SAMPLES, at most VERIFY_GAP_MM apart, and none at either end."""
    count = max(VERIFY_MIN_SAMPLES, math.ceil(length / VERIFY_GAP_MM))
    count += 1 - count % 2
    return (np.arange(count) + 0.5) / count


def _walk(prim: Poly2) -> tuple[np.ndarray, np.ndarray]:
    points = np.array(prim.points)
    return points, np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))))


def _at(prim: Prim2, t: np.ndarray) -> np.ndarray:
    """Points of a primitive at fractions ``t`` of its length (of its angle or parameter for an arc and an ellipse)."""
    if isinstance(prim, Line2):
        return np.column_stack((prim.a[0] + (prim.b[0] - prim.a[0]) * t, prim.a[1] + (prim.b[1] - prim.a[1]) * t))
    if isinstance(prim, Arc2):
        angle = prim.start_rad + (prim.end_rad - prim.start_rad) * t
        return np.column_stack((prim.center[0] + prim.radius * np.cos(angle), prim.center[1] + prim.radius * np.sin(angle)))
    if isinstance(prim, Ellipse2):
        return _ellipse_at(prim, prim.start_rad + (prim.end_rad - prim.start_rad) * t)
    points, along = _walk(prim)
    s = t * along[-1]
    return np.column_stack((np.interp(s, along, points[:, 0]), np.interp(s, along, points[:, 1])))


def _piece(prim: Prim2, t0: float, t1: float) -> Prim2 | None:
    """The stretch of ``prim`` from fraction ``t0`` to ``t1``, of the same kind; ``None`` when it is shorter than MIN_LENGTH_MM."""
    if t1 - t0 < 1e-9:
        return None
    if t0 <= 0.0 and t1 >= 1.0:
        return prim
    if isinstance(prim, Line2):
        ends = _at(prim, np.array([t0, t1]))
        return _line(ends[0], ends[1])
    if isinstance(prim, Arc2):
        span = prim.end_rad - prim.start_rad
        return _arc(prim.center, prim.radius, prim.start_rad + span * t0, span * (t1 - t0))
    if isinstance(prim, Ellipse2):
        span = prim.end_rad - prim.start_rad
        start = (prim.start_rad + span * t0) % TWO_PI
        found = Ellipse2(prim.center, prim.major_axis, prim.ratio, _angle(start), _angle(start + span * (t1 - t0)))
        return found if _length(found) >= MIN_LENGTH_MM else None
    points, along = _walk(prim)
    ends = _at(prim, np.array([t0, t1]))
    inner = (along > t0 * along[-1]) & (along < t1 * along[-1])
    return _line_or_poly(np.vstack((ends[:1], points[inner], ends[1:])))


@dataclass
class _Checked:
    kept: list[Prim2]  # the primitive itself, or the stretches of it that the polygonal visible lines cover
    moved: list[Prim2]  # stretches the polygonal hidden lines cover
    dropped: list[Prim2]  # stretches no polygonal line covers


def _denoise(seen: np.ndarray) -> np.ndarray:
    """``seen`` without runs of a single sample, which take the value of their neighbours, first to last: one point on or off a line is no evidence."""
    seen = seen.copy()
    while True:
        cuts = np.flatnonzero(seen[1:] != seen[:-1]) + 1
        starts, stops = np.concatenate(([0], cuts)), np.concatenate((cuts, [len(seen)]))
        lone = np.flatnonzero(stops - starts == 1)
        if not lone.size or len(starts) == 1:
            return seen
        seen[starts[lone[0]]] ^= True


def _bisect(prim: Prim2, visible: _Segments, allowed: float, covered: np.ndarray, uncovered: np.ndarray) -> np.ndarray:
    """Fractions between a covered and an uncovered one where the cover ends."""
    near, far = covered.copy(), uncovered.copy()
    for _ in range(VERIFY_BISECTIONS):
        middle = (near + far) / 2.0
        on = visible.within(_at(prim, middle), allowed)
        near, far = np.where(on, middle, near), np.where(on, far, middle)
    return (near + far) / 2.0


def _judge(prim: Prim2, t: np.ndarray, points: np.ndarray, seen: np.ndarray, visible: _Segments, hidden: Callable[[], _Segments], allowed: float) -> _Checked:
    """Split ``prim`` into the stretches the polygonal visible lines cover and those they do not.

    ``seen`` says per sample (at fractions ``t``, at ``points``) whether a visible line is within ``allowed``.  A
    stretch without cover moves to hidden when the hidden lines cover all of its samples, and is dropped otherwise.
    """
    seen = _denoise(seen)
    if seen.all():
        return _Checked([prim], [], [])
    flips = np.flatnonzero(np.diff(np.concatenate(([False], ~seen, [False])).astype(np.int8)))
    first, stop = flips[0::2], flips[1::2]  # run i is the samples first[i] .. stop[i] - 1
    left, right = np.zeros(len(first)), np.ones(len(first))
    inner = first > 0
    left[inner] = _bisect(prim, visible, allowed, t[first[inner] - 1], t[first[inner]])
    inner = stop < len(t)
    right[inner] = _bisect(prim, visible, allowed, t[stop[inner]], t[stop[inner] - 1])

    checked = _Checked([], [], [])
    edge = 0.0
    for a, b, lo, hi in zip(first, stop, left, right):
        kept = _piece(prim, edge, lo)
        if kept is not None:
            checked.kept.append(kept)
        edge = hi
        gone = _piece(prim, lo, hi)
        if gone is not None:
            (checked.moved if hidden().within(points[a:b], allowed).all() else checked.dropped).append(gone)
    kept = _piece(prim, edge, 1.0)
    if kept is not None:
        checked.kept.append(kept)
    return checked


def _check(prim: Prim2, visible: _Segments, hidden: Callable[[], _Segments], tolerance: float, sampled: bool) -> _Checked:
    t = _fractions(_plain_length(prim))
    allowed = tolerance + (CURVE_DEFLECTION_MM if sampled else 0.0)
    points = _at(prim, t)
    return _judge(prim, t, points, visible.within(points, allowed), visible, hidden, allowed)


def _extra_share(segments: np.ndarray, visible: list[Prim2], reach: float) -> float:
    """The share of the length of the polygonal ``segments`` that lies further than ``reach`` from every visible primitive."""
    a, b = segments[:, :2], segments[:, 2:]
    length = np.linalg.norm(b - a, axis=1)
    whole = float(length.sum())
    if whole <= 0.0:
        return 0.0
    count = np.maximum(1, np.ceil(length / VERIFY_GAP_MM)).astype(np.int64)
    owner, step = _ranges(count)
    points = a[owner] + (b - a)[owner] * ((step + 0.5) / count[owner])[:, None]
    pieces = [_flatten(prim, reach / 8.0) for prim in visible]
    lines = np.vstack([np.hstack((p[:-1], p[1:])) for p in pieces]) if pieces else np.empty((0, 4))
    near = _Segments(lines, reach).within(points, reach)
    return float((length / count)[owner][~near].sum()) / whole


@dataclass
class _Verdict:
    visible: list[Prim2]  # the visible primitives after the correction
    moved: list[Prim2]  # stretches to add to the hidden ones
    removed: int  # stretches dropped
    changed: bool


def _verify(shape, frame: ViewFrame, visible: list[Prim2], sampled: set[Prim2]) -> _Verdict | None:
    """Check the exact visible primitives against the polygonal projection of the shape.

    ``None`` when the check cannot confirm the exact result: the polygonal projection failed, correcting would
    drop more than VERIFY_DROP_LIMIT or move more than VERIFY_MOVE_LIMIT of the visible length, or the polygonal
    projection has more than VERIFY_DROP_LIMIT of visible length no exact primitive lies on.
    """
    try:
        oracle = _oracle(shape, frame)
    except Exception:  # noqa: BLE001 - OCCT raises its own exception types; the exact result stands without the check
        return None
    reach = oracle.tolerance + CURVE_DEFLECTION_MM
    seen = _Segments(oracle.visible, reach)
    under: list[_Segments] = []

    def hidden() -> _Segments:
        if not under:
            under.append(_Segments(oracle.hidden(), reach))
        return under[0]

    lengths = [_plain_length(p) for p in visible]
    fractions = [_fractions(length) for length in lengths]
    allowed = [oracle.tolerance + (CURVE_DEFLECTION_MM if p in sampled else 0.0) for p in visible]
    points = [_at(p, t) for p, t in zip(visible, fractions)]
    checks: list[_Checked] = []
    try:
        if visible:
            found = seen.within(np.vstack(points), np.repeat(allowed, [len(t) for t in fractions]))
            offset = 0
            for prim, t, where, limit in zip(visible, fractions, points, allowed):
                checks.append(_judge(prim, t, where, found[offset : offset + len(t)], seen, hidden, limit))
                offset += len(t)
    except _OracleFailure:
        return None
    whole = sum(lengths)
    moved_length = sum(_plain_length(p) for c in checks for p in c.moved)
    dropped_length = sum(_plain_length(p) for c in checks for p in c.dropped)
    if dropped_length > VERIFY_DROP_LIMIT * whole or moved_length > VERIFY_MOVE_LIMIT * whole:
        return None
    if _extra_share(oracle.visible, visible, reach) > VERIFY_DROP_LIMIT:
        return None
    return _Verdict(
        visible=[p for c in checks for p in c.kept],
        moved=[p for c in checks for p in c.moved],
        removed=sum(len(c.dropped) for c in checks),
        changed=any(c.moved or c.dropped for c in checks),
    )
