"""Design-for-manufacturing rules for ``geometry_check_dfm``: limits, rule checks, findings.

The geometry comes from :mod:`eng_toolkit.brep` (exact B-rep facts: how faces meet, which faces face each other across
material, holes).  This module holds no OpenCASCADE code: it compares those facts with limits and says, for every
finding, whether the number is exact or a heuristic.  Lengths are millimetres throughout; the tool converts on output.

Limits are the toolkit's own defaults (listed in :data:`LIMIT_DEFAULTS`).  They are rules of thumb, not values from a
standard, and no finding claims conformance to one.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from eng_toolkit import brep
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import UnitsError, parse_quantity

PROCESSES: tuple[str, ...] = ("cnc_milling", "sheet_metal")
RECOGNISED_UNSUPPORTED: tuple[str, ...] = (
    "turning",
    "injection_molding",
    "additive",
    "casting",
    "cnc_3axis",
    "cnc_5axis",
    "laser_cutting",
)
SEVERITIES: tuple[str, ...] = ("error", "warn", "info")
AXES: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
TOLERANCE_MM = 1e-6  # lengths closer than this are equal: used for limit comparisons
PARALLEL = 1e-6  # |sin| of the angle below which two directions count as parallel
FULL_TURN = 2.0 * math.pi - 1e-6
MAX_FACES = 2500  # kept unchanged; performance verified through 2006 faces (see docs/geometry-dfm/design.md)
LIMIT_MAX_LENGTH_MM = 1e5  # a limit above 100 m is not a manufacturing limit; refusing it also keeps the arithmetic finite
LIMIT_MAX_RATIO = 1e6
LIMIT_MAX_COUNT = 6  # there are six axis directions
LIMIT_MAX_ANGLE_DEG = 90.0  # near_tangent_deg: a turn of more than 90 degrees is a corner by any reading
WALL_SEARCH_FACTOR = 5.0  # walls are searched up to this multiple of the minimum-wall limit
WALL_REFINE_FACTOR = 2.0  # ray-sampled walls up to this multiple of min_wall get an exact closest distance
SLOT_WALL_SIN = 0.1  # planar faces whose normal is this close to perpendicular to the tool axis are slot walls (6 degrees)
MAX_FACE_IDS_LISTED = 50  # face ids a finding lists in geometry_check_dfm's output (the full list stays in-process)
MAX_LIMITS_CHARS = 4096  # the limits JSON text
# A bend whose inner and outer cylinders are further apart than 1e-3 of the thickness is still paired when their axes are
# within this distance: 5 x the resolution of the coordinates the arcs were written with.  An STEP file does not keep
# that resolution (an arc through three points becomes an exact circle), so 0.01 mm (two decimals) is assumed:
# via points to 3 decimals leave the axes 3e-3 mm apart, to 2 decimals up to 4e-2 mm.
BEND_RESOLUTION_MM = 0.01
BEND_RESOLUTION_FACTOR = 5.0
BEND_RELAXED_AXIS_MM = BEND_RESOLUTION_FACTOR * BEND_RESOLUTION_MM

# key -> (kind, default, description); kind "length" takes a string with a unit, "ratio", "count" and "angle" (degrees)
# a plain number
NEAR_TANGENT = (
    "angle",
    5.0,
    "degrees: a concave edge whose faces turn by less than this (material angle between 180 and 180 + this) is a "
    "near-tangent kink, not a corner, and is counted, not reported",
)
LIMIT_DEFAULTS: dict[str, dict[str, tuple[str, Any, str]]] = {
    "cnc_milling": {
        "min_wall": ("length", "0.8 mm", "thinnest wall or floor between two faces with material between them"),
        "tool_radius": (
            "length",
            "3 mm",
            "RADIUS (not the diameter) of the smallest end mill that must reach every internal corner: a 6 mm end mill is '3 mm'",
        ),
        "max_hole_depth_ratio": ("ratio", 10.0, "hole depth divided by diameter"),
        "min_hole_diameter": ("length", "1 mm", "smallest hole diameter"),
        "min_access_directions": ("count", 1, "planar faces reachable from fewer of the six axis directions are reported (heuristic)"),
        "near_tangent_deg": NEAR_TANGENT,
    },
    "sheet_metal": {
        "max_thickness": ("length", "10 mm", "largest sheet thickness searched for"),
        "min_bend_radius_ratio": ("ratio", 1.0, "inner bend radius divided by sheet thickness"),
        "min_hole_edge_ratio": ("ratio", 1.5, "distance from a hole's wall to the nearest edge or cutout, divided by sheet thickness"),
        "near_tangent_deg": NEAR_TANGENT,
    },
}

NOT_PERFORMED: dict[str, tuple[str, ...]] = {
    "cnc_milling": (
        "corner radius at the bottom of a pocket (floor to wall) and pocket depth against tool diameter",
        "undercuts, T-slots and tool length: only a one-sample-point reach test per planar face is made (cnc.tool_access, heuristic)",
        "a thin spot between the sample points of walls that are not parallel planes or coaxial cylinders (tapered, conical "
        "or free-form walls, a hole near a face): those are measured along the inward normal from up to 12 points per face",
        "walls thinner than the search range of 5 times min_wall are the only ones looked for",
        "threads, counterbores and countersinks (a counterbore is listed as a separate blind hole), drill-point cones",
        "tolerances, surface finish, fixturing and set-ups, material, tool deflection, cost",
    ),
    "sheet_metal": (
        "flat pattern and unfolding, bend allowance and K-factor, springback",
        "hole-to-bend distance, bend-to-bend distance, bend relief and minimum flange length",
        "minimum hole diameter and slot width against thickness",
        "hems, jogs, lances, countersinks and other formed features (reported as off-thickness faces when seen)",
        "material, grain direction, tooling, tolerances",
    ),
}


@dataclass
class Finding:
    rule: str
    severity: str
    exact: bool
    message: str
    measured: float | None = None  # a length in mm, or a plain number when ``measured_unit`` says so
    limit: float | None = None
    measured_unit: str = "mm"  # "mm", "ratio" or "count"
    test: str = ""  # how measured and limit compare when the rule passes
    face_ids: list[int] = field(default_factory=list)
    edge_ids: list[int] = field(default_factory=list)
    location_mm: tuple[float, float, float] | None = None
    basis: str = ""  # what makes it exact or heuristic
    extra: dict[str, Any] = field(default_factory=dict)
    # Every face the finding concerns, when ``face_ids`` lists only the first ones (sheet.thickness_uniform lists 50 and
    # gives the count in ``extra['faces_total']``).  For in-process callers (geometry_view colours all of them); it is
    # never written into a tool result, so geometry_check_dfm's output is unchanged.
    full_face_ids: list[int] | None = field(default=None, repr=False, compare=False)


DEFAULT_TOOL_AXIS = "+z"
TOOL_AXIS_DEFAULTED_WARNING = "tool_axis defaulted to +z: the shop's setup may differ"


def summarise(findings: list[Finding], counts: Any, by_rule: dict[str, dict[str, int]]) -> dict[str, Any]:
    """The ``summary`` of ``geometry_check_dfm``: counts by severity and rule, and ``not_evaluated``, one sentence per rule
    that skipped part of the model ("4 bend-like faces not evaluated"), repeated in ``headline`` so that "errors 0,
    warnings 0" cannot hide a check that did not run."""
    not_evaluated = sorted(
        {f"{int(f.extra['not_evaluated'])} bend-like faces not evaluated" for f in findings if f.rule == "sheet.bend_unrecognised"}
    )
    headline = f"errors {counts['error']}, warnings {counts['warn']}, info {counts['info']}"
    if not_evaluated:
        headline += "; " + "; ".join(not_evaluated)
    return {
        "headline": headline,
        "errors": counts["error"],
        "warnings": counts["warn"],
        "info": counts["info"],
        "total": len(findings),
        "by_rule": by_rule,
        "not_evaluated": not_evaluated,
        "exact_findings": sum(1 for f in findings if f.exact),
        "heuristic_findings": sum(1 for f in findings if not f.exact),
    }


def all_face_ids(finding: Finding) -> list[int]:
    """The complete face list of a finding: ``full_face_ids`` when the rule kept one, else ``face_ids``."""
    return list(finding.full_face_ids) if finding.full_face_ids is not None else list(finding.face_ids)


@dataclass
class Report:
    findings: list[Finding]
    measurements: dict[str, Any]
    checked: list[str]
    not_performed: list[str]
    warnings: list[str]


def _bad(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", message, details)


def limit_pointer(key: str) -> str:
    """The JSON pointer (RFC 6901) of one key of ``limits``: ``/limits/min_wall``."""
    return "/limits/" + key.replace("~", "~0").replace("/", "~1")


def _shown(text: str, limit: int = 64) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _limit_error(family: str, key: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure(family, f"limits.{_shown(key)}: {message}", {"param": limit_pointer(key), **details})


def _within(key: str, value: float, ceiling: float, unit: str) -> None:
    suffix = f" {unit}" if unit else ""
    if not math.isfinite(value):
        raise _limit_error("input.out_of_range", key, f"is not a finite number; the largest value accepted is {ceiling:g}{suffix}", maximum=ceiling)
    if value > ceiling:
        raise _limit_error("input.out_of_range", key, f"{value:g}{suffix} is above the largest value accepted ({ceiling:g}{suffix})", maximum=ceiling)


def _parse_limit(key: str, kind: str, value: Any) -> float:
    """One limit, validated on its own: every refusal names ``/limits/<key>``, and a number too large for a float (a
    400-digit integer), an infinity or a NaN is ``input.out_of_range`` rather than an arithmetic error."""
    if kind == "length":
        if not isinstance(value, str):
            raise _limit_error("units.bare_number", key, 'give a length with a unit, e.g. "1 mm"')
        name = f"limits.{_shown(key)}"
        try:
            quantity = parse_quantity(value, name=name, dimension="length")
            millimetres = float(quantity.to("millimeter").magnitude)
        except UnitsError as exc:
            # parse_quantity's only input.invalid is a magnitude that is not finite (inf, NaN, 1e400)
            family = "input.out_of_range" if exc.family == "input.invalid" else exc.family
            raise ToolFailure(family, exc.message, {**exc.details, "param": limit_pointer(key)}) from None
        except (ArithmeticError, ValueError, TypeError) as exc:  # OverflowError: an integer beyond any float
            raise _limit_error("input.out_of_range", key, f"the value is outside the supported range ({type(exc).__name__})", maximum=LIMIT_MAX_LENGTH_MM) from None
        _within(key, millimetres, LIMIT_MAX_LENGTH_MM, "mm")
        if millimetres <= 0.0:
            raise _limit_error("input.invalid", key, f"must be > 0, got {_shown(value)!r}", received=_shown(value))
        return millimetres
    ceiling = float({"count": LIMIT_MAX_COUNT, "angle": LIMIT_MAX_ANGLE_DEG}.get(kind, LIMIT_MAX_RATIO))
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _limit_error("input.invalid", key, "must be a number")
    try:
        number = float(value)
    except OverflowError:
        raise _limit_error("input.out_of_range", key, f"the value is outside the supported range (largest accepted {ceiling:g})", maximum=ceiling) from None
    except ValueError:
        raise _limit_error("input.invalid", key, f"{_shown(str(value))!r} is not a number") from None
    _within(key, number, ceiling, "")
    if number <= 0.0:
        raise _limit_error("input.invalid", key, f"must be a positive number, got {_shown(repr(value))}")
    return number


def parse_limits(process: str, raw: str) -> dict[str, float]:
    """The limits for ``process``: defaults overridden by the JSON object ``raw``.  Lengths come back in mm.

    The one validator of the limits of ``geometry_check_dfm`` and ``geometry_view``: every refusal carries
    ``details.param``, ``/limits`` for the text as a whole and ``/limits/<key>`` for one key (an unknown key, a value
    that is not a positive number or length, or one out of range, also after the conversion to millimetres).
    """
    table = LIMIT_DEFAULTS[process]
    given: dict[str, Any] = {}
    if len(raw) > MAX_LIMITS_CHARS:
        raise _bad(f"limits: {len(raw)} characters; at most {MAX_LIMITS_CHARS}", param="/limits", limit=MAX_LIMITS_CHARS)
    if raw.strip():
        try:
            parsed = jsonsafe.loads(raw)
        except json.JSONDecodeError as exc:
            raise _bad(f"limits: not valid JSON ({exc.msg} at position {exc.pos})", param="/limits") from None
        except RecursionError:
            raise _bad("limits: nested too deeply to be read", param="/limits") from None
        except ValueError as exc:  # an integer with more digits than Python converts
            raise ToolFailure("input.out_of_range", f"limits: a number is outside the supported range ({_shown(str(exc), 120)})", {"param": "/limits"}) from None
        if not isinstance(parsed, dict):
            raise _bad("limits: must be a JSON object such as {\"min_wall\": \"1 mm\"}", param="/limits")
        unknown = sorted(set(parsed) - set(table))
        if unknown:
            raise ToolFailure(
                "input.invalid",
                f"limits: {[_shown(k) for k in unknown[:10]]}{' ...' if len(unknown) > 10 else ''} are not limits of process "
                f"{process!r}; allowed: {sorted(table)}",
                {"param": limit_pointer(unknown[0]), "allowed": sorted(table), "unknown": [_shown(k) for k in unknown[:100]]},
            )
        given = parsed
    return {key: _parse_limit(key, kind, given.get(key, default)) for key, (kind, default, _) in table.items()}


def defaults_used(process: str, raw: str) -> list[str]:
    given = set(jsonsafe.loads(raw)) if raw.strip() else set()
    return [key for key in LIMIT_DEFAULTS[process] if key not in given]


def require_closed_solids(solid: brep.Solid, volume_mm3: float | None, *, purpose: str) -> brep.Closure:
    """Refuse (``geometry.not_a_solid`` at ``/model_path``) a file that is not made of closed solids only: no solid with
    positive volume, a solid with an open shell (free edges), or faces outside every solid.  The one check of
    ``geometry_check_dfm`` and ``geometry_view``, on :func:`eng_toolkit.brep.closure`; ``purpose`` says why the tool
    needs closed solids (it completes the sentence of the message)."""
    if solid.solids == 0 or volume_mm3 is None:
        raise ToolFailure(
            "geometry.not_a_solid",
            f"model_path holds {solid.faces} faces but no closed solid with positive volume (solids found: {solid.solids}); "
            f"{purpose}, so a shell or loose surfaces are refused. Export the part as a solid",
            {"param": "/model_path", "solids": solid.solids, "faces": solid.faces},
        )
    closed = brep.closure(solid)
    if not closed.closed:
        raise ToolFailure(
            "geometry.not_a_solid",
            "model_path is not made of closed solids only: " + " and ".join(closed.problems()) + f"; {purpose}: an open shell "
            "encloses no material and a loose face is not part of any solid. Export the part as closed solids",
            {
                "param": "/model_path",
                "solids": closed.solids,
                "shells": closed.shells,
                "open_shells": closed.open_shells,
                "free_edges": closed.free_edges,
                "faces_outside_solids": closed.faces_outside_solids,
                "loose_face_ids": list(closed.loose_face_ids[:100]),  # the first 100, numbered as geometry_measure numbers faces
            },
        )
    return closed


def unsupported_process(process: str) -> ToolFailure:
    known = process.strip().lower() in RECOGNISED_UNSUPPORTED
    why = "is recognised but not supported in this version" if known else "is not a known process"
    return ToolFailure(
        "geometry.unsupported_process",
        f"process {process!r} {why}; no check is guessed for it. Supported: {list(PROCESSES)}",
        {"param": "process", "supported": list(PROCESSES), "recognised_unsupported": list(RECOGNISED_UNSUPPORTED)},
    )


def _unit(vector) -> np.ndarray:
    v = np.array(vector, dtype=np.float64)
    return v / np.linalg.norm(v)


def _parallel(a, b) -> bool:
    return float(np.linalg.norm(np.cross(_unit(a), _unit(b)))) < PARALLEL


def _less(measured: float, limit: float) -> bool:
    return measured < limit - 1e-9 * max(1.0, abs(limit))


def _hole_face_ids(inv: brep.Inventory) -> set[int]:
    return {face for hole in inv.holes for face in hole.face_ids}


def near_tangent_edge(edge: brep.EdgeFact, threshold_deg: float) -> bool:
    """A concave edge whose two faces turn by less than ``threshold_deg``: a near-tangent kink (a tapered fin flank meeting
    its tip arc at 182 degrees), not a corner.  A round tool leaves only :func:`uncut_depth_mm` there."""
    return edge.material_angle_deg - 180.0 < threshold_deg


def uncut_depth_mm(material_angle_deg: float, tool_radius_mm: float) -> float:
    """Material a round tool of ``tool_radius_mm`` leaves in a sharp concave corner, from the corner along its bisector:
    r (1 / sin(a / 2) - 1) with a = 360 - material angle, the angle of the air.  0.414 r in a 90 degree pocket corner,
    2e-4 mm for a 1.5 mm tool at a 2 degree kink."""
    half_air = math.radians(min(180.0, max(1e-6, 360.0 - material_angle_deg))) / 2.0  # finite for a 360 degree slit too
    return float(f"{tool_radius_mm * (1.0 / math.sin(half_air) - 1.0):.9g}")


def _geometric_less(measured: float, limit: float, scale: float, tolerance_mm: float | None = None) -> bool:
    """``measured`` below ``limit`` by more than the geometric tolerance of ``scale`` (:func:`brep.coaxial_tolerance_mm`,
    or ``tolerance_mm`` when a bend was paired by the relaxed rule): a bend radius drawn through rounded points
    (2.99993 mm for 3) is not below a 3 mm limit."""
    return measured < limit - max(brep.coaxial_tolerance_mm(scale), tolerance_mm or 0.0)


# --------------------------------------------------------------------------- cnc_milling


def check_cnc_milling(
    solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float], tool_axis: str,
    *, deadline: Callable[[str], None] | None = None,
) -> Report:
    axis = AXES[tool_axis]
    findings: list[Finding] = []
    warnings: list[str] = []
    tool_radius = limits["tool_radius"]

    # internal corners: sharp concave edges parallel to the tool axis, and concave cylinder fillets around it
    edges = brep.edge_facts(solid, deadline=deadline)
    cylinders = brep.cylinder_facts(solid, inv, deadline=deadline)
    in_holes = _hole_face_ids(inv)
    sharp = fillets = near_tangent = 0
    for edge in edges:
        if deadline is not None:
            deadline("rules")
        if edge.convexity == "concave" and edge.kind == "line" and _parallel(edge.direction, axis):
            if near_tangent_edge(edge, limits["near_tangent_deg"]):
                near_tangent += 1
                continue
            sharp += 1
            findings.append(
                Finding(
                    rule="cnc.internal_corner_radius",
                    severity="error",
                    exact=True,
                    message=(
                        f"Sharp internal corner along the tool axis (radius 0, faces turning by "
                        f"{edge.material_angle_deg - 180.0:.3g} degrees): an end mill of radius {tool_radius:g} mm "
                        f"cannot cut it square and leaves {uncut_depth_mm(edge.material_angle_deg, tool_radius):.3g} mm of material in it"
                    ),
                    measured=0.0,
                    limit=tool_radius,
                    test="corner radius >= tool_radius",
                    face_ids=list(edge.face_ids),
                    edge_ids=[edge.id],
                    location_mm=edge.midpoint_mm,
                    basis="concave straight edge between two faces, parallel to the tool axis (exact B-rep topology)",
                    extra={
                        "material_angle_deg": edge.material_angle_deg,
                        "edge_length_mm": edge.length_mm,
                        "uncut_depth_mm": uncut_depth_mm(edge.material_angle_deg, tool_radius),
                    },
                )
            )
    for cyl in cylinders:
        if deadline is not None:
            deadline("rules")
        if cyl.sense != "concave" or cyl.face_id in in_holes or cyl.span_rad >= FULL_TURN or not _parallel(cyl.axis, axis):
            continue
        fillets += 1
        if _less(cyl.radius_mm, tool_radius):
            findings.append(
                Finding(
                    rule="cnc.internal_corner_radius",
                    severity="error",
                    exact=True,
                    message=f"Internal corner radius {cyl.radius_mm:g} mm is smaller than the tool radius {tool_radius:g} mm",
                    measured=cyl.radius_mm,
                    limit=tool_radius,
                    test="corner radius >= tool_radius",
                    face_ids=[cyl.face_id],
                    location_mm=cyl.mid_point_mm,
                    basis="concave cylinder face with its axis parallel to the tool axis (exact radius)",
                    extra={"span_deg": math.degrees(cyl.span_rad)},
                )
            )

    # holes
    for hole in inv.holes:
        if deadline is not None:
            deadline("rules")
        ratio = hole.depth_mm / hole.diameter_mm
        if ratio > limits["max_hole_depth_ratio"] + 1e-9:
            findings.append(
                Finding(
                    rule="cnc.hole_depth_ratio",
                    severity="error",
                    exact=True,
                    message=(
                        f"Hole {hole.id} (diameter {hole.diameter_mm:g} mm, depth {hole.depth_mm:g} mm) has depth/diameter "
                        f"{ratio:.6g}, above {limits['max_hole_depth_ratio']:g}"
                    ),
                    measured=ratio,
                    measured_unit="ratio",
                    limit=limits["max_hole_depth_ratio"],
                    test="depth/diameter <= max_hole_depth_ratio",
                    face_ids=list(hole.face_ids),
                    location_mm=hole.entry_mm,
                    basis="detected round hole: cylindrical depth over diameter (exact; a drill-point cone and any counterbore are not counted)",
                    extra={"hole_id": hole.id, "diameter_mm": hole.diameter_mm, "depth_mm": hole.depth_mm, "thru": hole.thru},
                )
            )
        if _less(hole.diameter_mm, limits["min_hole_diameter"]):
            findings.append(
                Finding(
                    rule="cnc.min_hole_diameter",
                    severity="error",
                    exact=True,
                    message=f"Hole {hole.id} diameter {hole.diameter_mm:g} mm is below {limits['min_hole_diameter']:g} mm",
                    measured=hole.diameter_mm,
                    limit=limits["min_hole_diameter"],
                    test="diameter >= min_hole_diameter",
                    face_ids=list(hole.face_ids),
                    location_mm=hole.entry_mm,
                    basis="detected round hole diameter (exact)",
                    extra={"hole_id": hole.id},
                )
            )

    # walls
    search = limits["min_wall"] * WALL_SEARCH_FACTOR
    search_result = brep.facing_pairs(solid, inv, cylinders, max_gap_mm=search, deadline=deadline)
    if not search_result.complete:
        warnings.append(
            f"The wall search stopped after {search_result.tested} candidate face pairs; thin walls beyond them may be missing"
        )
    thin = [p for p in search_result.pairs if _less(p.gap_mm, limits["min_wall"])]
    for pair in sorted(thin, key=lambda p: (p.gap_mm, p.face_ids)):
        if deadline is not None:
            deadline("rules")
        what = "between parallel planar faces" if pair.kind == "plane" else "between coaxial cylinders (a tube or bend wall)"
        findings.append(
            Finding(
                rule="cnc.min_wall",
                severity="error",
                exact=True,
                message=f"Wall {pair.gap_mm:g} mm thick {what}, below {limits['min_wall']:g} mm",
                measured=pair.gap_mm,
                limit=limits["min_wall"],
                test="thickness >= min_wall",
                face_ids=list(pair.face_ids),
                location_mm=pair.point_mm,
                basis=(
                    "exact distance between two parallel planes, checked by a ray through the material"
                    if pair.kind == "plane"
                    else "exact difference of two coaxial radii, checked by a radial ray through the material"
                ),
                extra={"face_area_mm2": pair.face_area_mm2, "kind": pair.kind},
            )
        )
    if near_tangent:
        warnings.append(
            f"{near_tangent} concave edges along the tool axis whose faces turn by less than near_tangent_deg "
            f"({limits['near_tangent_deg']:g} degrees) were not reported as sharp corners: they are near-tangent kinks "
            "(e.g. a tapered flank meeting a tip arc) where an end mill leaves a negligible cusp "
            "(measurements.near_tangent_edges_skipped)"
        )

    # walls that are not parallel planes or coaxial cylinders: rays along the inward normal of every other face
    paired = frozenset(face for p in search_result.pairs for face in p.face_ids)
    rays = brep.normal_walls(
        solid,
        inv,
        max_gap_mm=search,
        refine_below_mm=min(search, WALL_REFINE_FACTOR * limits["min_wall"]),
        skip_faces=paired,
        deadline=deadline,
    )
    if not rays.complete:
        warnings.append(f"The wall rays stopped after {rays.rays} rays; thin walls on the faces not reached may be missing")
    for wall in sorted(rays.walls, key=lambda w: (w.thickness_mm, w.face_ids)):
        if deadline is not None:
            deadline("rules")
        if not _less(wall.thickness_mm, limits["min_wall"]):
            continue
        a, b = wall.face_ids
        between = f"F{a} ({inv.faces[a - 1].kind})" + (f" and F{b} ({inv.faces[b - 1].kind})" if b != a else " across itself")
        findings.append(
            Finding(
                rule="cnc.min_wall",
                severity="error" if wall.exact else "warn",
                exact=wall.exact,
                message=f"Wall {wall.thickness_mm:g} mm thick between {between}, below {limits['min_wall']:g} mm",
                measured=wall.thickness_mm,
                limit=limits["min_wall"],
                test="thickness >= min_wall",
                face_ids=sorted({a, b}),
                location_mm=wall.point_mm,
                basis=(
                    "exact closest distance between the two faces (BRepExtrema), normal to both and through the material; "
                    "the pair was found by rays along the inward normal from sample points"
                    if wall.exact
                    else "heuristic: the shortest chord through the material along the inward normal of a face's sample "
                    "points; the wall may be thinner between the points"
                ),
                extra={"kind": "normal_ray", "chord_mm": wall.chord_mm},
            )
        )
    walls_found = [p.gap_mm for p in search_result.pairs] + [w.thickness_mm for w in rays.walls]
    smallest = min(walls_found, default=None)
    if smallest is None:
        warnings.append(
            f"No wall thinner than the search range of {search:g} mm ({WALL_SEARCH_FACTOR:g} x min_wall) was found: "
            f"{search_result.tested} parallel face pairs tested and {rays.rays} rays cast along the inward normal of "
            f"{rays.faces_sampled} faces, so measurements.smallest_wall_mm is null; every wall is thicker than {search:g} mm "
            "where it was measured"
        )
    if rays.faces_without_samples:
        listed = ", ".join(f"F{i}" for i in rays.faces_without_samples[:20])
        warnings.append(
            f"{len(rays.faces_without_samples)} faces could not be sampled for the wall rays (the mesher gave them no "
            f"triangle: {listed}{' ...' if len(rays.faces_without_samples) > 20 else ''}); walls behind them were not measured"
        )

    # tool access
    access = brep.face_access(solid, inv, AXES, deadline=deadline)
    slot_walls = [
        a for a in access
        if a.clearance_mm is not None and a.normal is not None and abs(float(np.dot(a.normal, axis))) < SLOT_WALL_SIN
    ]
    narrowest = min(slot_walls, key=lambda a: (a.clearance_mm, a.face_id), default=None)
    if narrowest is not None and 2.0 * tool_radius > narrowest.clearance_mm + TOLERANCE_MM:
        warnings.append(
            f"tool_radius {tool_radius:g} mm is a {2.0 * tool_radius:g} mm diameter end mill, wider than the narrowest gap "
            f"measured in front of a wall along the tool axis ({narrowest.clearance_mm:g} mm from F{narrowest.face_id}, one "
            f"ray along the face normal, heuristic). tool_radius is the RADIUS: if {tool_radius:g} mm is the tool's "
            f"diameter, pass tool_radius '{tool_radius / 2.0:g} mm'"
        )
    blocked = [a for a in access if len(a.open_directions) < limits["min_access_directions"]]
    for a in blocked:
        if deadline is not None:
            deadline("rules")
        findings.append(
            Finding(
                rule="cnc.tool_access",
                severity="warn",
                exact=False,
                message=(
                    f"Planar face F{a.face_id} is reachable from {len(a.open_directions)} of the 6 axis directions "
                    f"({', '.join(a.open_directions) or 'none'}), fewer than {limits['min_access_directions']:g}"
                ),
                measured=float(len(a.open_directions)),
                measured_unit="count",
                limit=limits["min_access_directions"],
                test="reachable directions >= min_access_directions",
                face_ids=[a.face_id],
                location_mm=a.point_mm,
                basis="heuristic: straight rays from one sample point per face; no tool shape, length or partial shadowing",
                extra={"open_directions": list(a.open_directions)},
            )
        )

    measurements = {
        "sharp_internal_corners": sharp,
        "near_tangent_edges_skipped": near_tangent,
        "internal_fillets_checked": fillets,
        "holes": len(inv.holes),
        "smallest_wall_mm": smallest,
        "wall_search_range_mm": search,
        "face_pairs_tested": search_result.tested,
        "wall_rays_cast": rays.rays,
        "wall_faces_sampled": rays.faces_sampled,
        "planar_faces_reach_tested": len(access),
        "narrowest_slot_mm": None if narrowest is None else narrowest.clearance_mm,
    }
    checked = [
        "cnc.internal_corner_radius: concave straight edges and concave cylinders parallel to the tool axis; concave edges "
        f"whose faces turn by less than near_tangent_deg ({limits['near_tangent_deg']:g} degrees) are near-tangent and skipped",
        "cnc.min_wall: parallel planar faces and coaxial cylinders with material between them (exact), and every other face "
        "by rays along its inward normal from up to 12 sample points, refined to the exact closest distance where it is "
        "normal to both faces",
        "cnc.hole_depth_ratio and cnc.min_hole_diameter: every detected round hole",
        "cnc.tool_access: planar faces, one sample point, six axis directions (heuristic)",
    ]
    return Report(findings, measurements, checked, list(NOT_PERFORMED["cnc_milling"]), warnings)


# --------------------------------------------------------------------------- sheet_metal


def _cluster(pairs: list[brep.FacingPair], inv: brep.Inventory) -> list[tuple[float, float, list[brep.FacingPair]]]:
    """``(thickness, weight, pairs)`` per thickness; weight is the face area the pairs cover.  Gaps within the geometric
    tolerance of the thickness (:func:`brep.coaxial_tolerance_mm`, 1e-3 of it) are one thickness: a bend whose radii come
    from rounded points is 3.00007 mm thick in a 3 mm sheet.  A cylinder pair accepted only by the relaxed axis rule
    (arcs through via points of 2 or 3 decimals) joins the nearest thickness within that relaxed tolerance, after the
    other pairs have set the thicknesses."""

    def weight_of(pair: brep.FacingPair) -> float:
        return pair.face_area_mm2 if pair.kind == "plane" else inv.faces[pair.face_ids[0] - 1].area_mm2

    clusters: list[tuple[float, float, list[brep.FacingPair]]] = []
    for pair in sorted((p for p in pairs if not p.relaxed), key=lambda p: p.gap_mm):
        if clusters and abs(pair.gap_mm - clusters[-1][0]) <= brep.coaxial_tolerance_mm(clusters[-1][0]):
            gap, total, members = clusters[-1]
            clusters[-1] = (gap, total + weight_of(pair), members + [pair])
        else:
            clusters.append((pair.gap_mm, weight_of(pair), [pair]))
    for pair in sorted((p for p in pairs if p.relaxed), key=lambda p: p.gap_mm):
        near = min(range(len(clusters)), key=lambda i: abs(clusters[i][0] - pair.gap_mm), default=None)
        if near is not None and abs(pair.gap_mm - clusters[near][0]) <= max(
            brep.coaxial_tolerance_mm(clusters[near][0]), pair.axis_tolerance_mm or 0.0
        ):
            gap, total, members = clusters[near]
            clusters[near] = (gap, total + weight_of(pair), members + [pair])
        else:
            clusters.append((pair.gap_mm, weight_of(pair), [pair]))
    return sorted(clusters, key=lambda c: c[0])


def _merge_bends(
    pairs: list[brep.FacingPair], cylinders: dict[int, brep.CylinderFact], tolerance_mm: float
) -> list[list[brep.FacingPair]]:
    """The bend cylinder pairs grouped into bends: two pairs are one bend when they share a face (ribs split the inner face
    of a bend into pieces that keep one outer face), or lie on one axis with the same inner and outer radii (to
    ``tolerance_mm``)."""

    def same_line(p: brep.FacingPair, q: brep.FacingPair) -> bool:
        first, second = cylinders.get(p.face_ids[0]), cylinders.get(q.face_ids[0])
        if first is None or second is None:
            return False
        axis = np.array(first.axis)
        if float(np.linalg.norm(np.cross(axis, np.array(second.axis)))) > PARALLEL:
            return False
        offset = np.array(second.start_mm) - np.array(first.start_mm)
        return float(np.linalg.norm(offset - (offset @ axis) * axis)) <= tolerance_mm

    group = list(range(len(pairs)))

    def root(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i, p in enumerate(pairs):
        for j in range(i + 1, len(pairs)):
            q = pairs[j]
            shared = bool(set(p.face_ids) & set(q.face_ids))
            alike = (
                abs(float(p.inner_radius_mm) - float(q.inner_radius_mm)) <= tolerance_mm
                and abs(float(p.outer_radius_mm) - float(q.outer_radius_mm)) <= tolerance_mm
                and same_line(p, q)
            )
            if shared or alike:
                group[root(j)] = root(i)
    merged: dict[int, list[brep.FacingPair]] = {}
    for i, p in enumerate(pairs):
        merged.setdefault(root(i), []).append(p)
    return sorted(merged.values(), key=lambda members: min(p.face_ids for p in members))


def _outer_corner(a: int, b: int, partners: dict[int, set[int]], neighbours: dict[int, set[int]], convex: set) -> bool:
    """True when the faces across the sheet from ``a`` and from ``b`` meet at a convex edge, directly or through one face
    with convex edges to both: the outer corner of a bend.  A rib standing on a plate has none (a joint, not a bend)."""
    for across_a in partners.get(a, ()):
        for across_b in partners.get(b, ()):
            if across_a == across_b:
                continue
            if frozenset((across_a, across_b)) in convex:
                return True
            for middle in neighbours.get(across_a, set()) & neighbours.get(across_b, set()):
                if frozenset((across_a, middle)) in convex and frozenset((middle, across_b)) in convex:
                    return True
    return False


def _faces_on_one_side(
    solid: brep.Solid, inv: brep.Inventory, edges: list[brep.EdgeFact], tolerance_mm: float
) -> dict[tuple[int, int], bool]:
    """``(edge id, face id) -> True`` when the planar face lies wholly on one side of the edge's line within its plane.
    Both faces of a bend end at the bend; a plate under a rib flush with its edge runs on past the rib on both sides."""
    requests = []
    keys = []
    for edge in edges:
        for face_id in edge.face_ids:
            normal = inv.faces[face_id - 1].normal
            if normal is None:
                continue
            across = np.cross(np.array(normal, dtype=np.float64), np.array(edge.direction, dtype=np.float64))
            requests.append((face_id, edge.midpoint_mm, tuple(float(c) for c in across)))
            keys.append((edge.id, face_id))
    found = {key: False for edge in edges for key in ((edge.id, edge.face_ids[0]), (edge.id, edge.face_ids[1]))}
    for key, (low, high) in zip(keys, brep.face_extents(solid, requests) if requests else []):
        found[key] = low >= -tolerance_mm or high <= tolerance_mm
    return found


def _joins_two_legs(touching: set[int], partners: dict[int, set[int]]) -> bool:
    """True when two of the sheet faces a face touches are not across the sheet from each other (two legs of a bend, not
    the two sides of one cut edge)."""
    ordered = sorted(touching)
    return any(second not in partners.get(first, set()) for i, first in enumerate(ordered) for second in ordered[i + 1:])


def check_sheet_metal(
    solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float],
    *, deadline: Callable[[str], None] | None = None,
) -> Report:
    findings: list[Finding] = []
    warnings: list[str] = []
    cylinders = brep.cylinder_facts(solid, inv, deadline=deadline)
    search = brep.facing_pairs(
        solid, inv, cylinders, max_gap_mm=limits["max_thickness"], relaxed_axis_mm=BEND_RELAXED_AXIS_MM, deadline=deadline
    )
    if deadline is not None:
        deadline("rules")
    if not search.complete:
        warnings.append(f"The thickness search stopped after {search.tested} candidate face pairs; the result may be incomplete")
    clusters = _cluster(list(search.pairs), inv)
    checked = [
        "sheet.thickness and sheet.thickness_uniform: parallel planes and coaxial cylinders with material between them",
        "sheet.min_bend_radius: coaxial concave/convex cylinder pairs of the sheet thickness (axes within 1e-3 of the "
        f"thickness, or within {BEND_RELAXED_AXIS_MM:g} mm with a sheet.bend_tolerance warning), the faces of one bend counted "
        "once, and sharp bends between sheet faces; cylinders joining two legs that pair with nothing give sheet.bend_unrecognised",
        "sheet.joint: concave edges where one sheet stands on another (ribs, gussets, T-joints), reported as a fact, not as bends",
        "sheet.hole_to_edge: exact distance from each hole wall to the nearest edge or cutout face",
    ]
    measurements: dict[str, Any] = {"thickness_candidates_mm": [c[0] for c in clusters], "face_pairs_tested": search.tested}
    if not clusters:
        findings.append(
            Finding(
                rule="sheet.thickness",
                severity="error",
                exact=False,
                message=(
                    f"No two faces with material between them closer than {limits['max_thickness']:g} mm were found, so no "
                    "sheet thickness exists; this does not look like sheet metal"
                ),
                limit=limits["max_thickness"],
                test="a thickness <= max_thickness exists",
                basis="heuristic: absence of any parallel-plane or coaxial-cylinder pair within max_thickness",
            )
        )
        return Report(findings, measurements, checked, list(NOT_PERFORMED["sheet_metal"]), warnings)

    thickness, _, dominant = max(clusters, key=lambda c: c[1])
    covered = {face for pair in dominant for face in pair.face_ids}
    edges = brep.edge_facts(solid, deadline=deadline)
    neighbours: dict[int, set[int]] = {}
    for edge in edges:
        if deadline is not None:
            deadline("rules")
        a, b = edge.face_ids
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    in_holes = _hole_face_ids(inv)
    edge_faces = {
        f.id for f in inv.faces if f.id not in covered and len(neighbours.get(f.id, set()) & covered) >= 2
    }
    off = sorted({f.id for f in inv.faces} - covered - edge_faces)
    first = dominant[0]
    findings.append(
        Finding(
            rule="sheet.thickness",
            severity="info",
            exact=True,
            message=f"Sheet thickness {thickness:g} mm: {len(dominant)} face pairs of this thickness cover {len(covered)} faces",
            measured=thickness,
            face_ids=list(first.face_ids),
            location_mm=first.point_mm,
            basis=(
                "exact gap of anti-parallel planes or coaxial cylinders; the thickness with the largest covered area is taken "
                "as the sheet (heuristic choice among candidates)"
            ),
            extra={"pairs": len(dominant), "faces_covered": sorted(covered)},
        )
    )
    others = [c for c in clusters if abs(c[0] - thickness) > brep.coaxial_tolerance_mm(thickness)]
    if off:
        other_pairs = [p for c in others for p in c[2] if set(p.face_ids) & set(off)]
        findings.append(
            Finding(
                rule="sheet.thickness_uniform",
                severity="error",
                exact=False,
                message=(
                    f"{len(off)} faces are neither part of a {thickness:g} mm thickness pair nor a cut edge between two such "
                    f"faces"
                    + (
                        f"; they pair at other thicknesses {sorted({p.gap_mm for p in other_pairs})} mm"
                        if other_pairs
                        else ""
                    )
                    + ": the thickness is not uniform, or a formed feature is present"
                ),
                measured=float(len(off)),
                measured_unit="count",
                limit=0.0,
                test="faces off the sheet thickness == 0",
                face_ids=off[:MAX_FACE_IDS_LISTED],
                location_mm=inv.faces[off[0] - 1].origin_mm or tuple(inv.faces[off[0] - 1].bbox_min_mm),
                basis=(
                    "heuristic interpretation of exact data: a face is an edge face when it touches two faces of the sheet "
                    "thickness; the rest is reported"
                ),
                extra={"other_thicknesses_mm": sorted({p.gap_mm for p in other_pairs}), "faces_total": len(off)},
                full_face_ids=list(off),
            )
        )

    # bends: concentric cylinder pairs of the sheet thickness, one bend however many faces it is split into
    by_face = {c.face_id: c for c in cylinders}
    bend_pairs = [p for p in dominant if p.kind == "cylinder" and p.span_rad is not None and p.span_rad < FULL_TURN]
    strict_tolerance = brep.coaxial_tolerance_mm(thickness)
    relaxed_used = max((p.axis_tolerance_mm or 0.0 for p in bend_pairs if p.relaxed), default=0.0)
    bends = _merge_bends(bend_pairs, by_face, max(strict_tolerance, relaxed_used))
    min_radius = limits["min_bend_radius_ratio"] * thickness
    bend_pairing: list[dict[str, Any]] = []
    for bend in bends:
        if deadline is not None:
            deadline("rules")
        pair = min(bend, key=lambda p: (p.inner_radius_mm, p.face_ids))
        radius = float(pair.inner_radius_mm)
        faces = sorted({face for p in bend for face in p.face_ids})
        relaxed = [p for p in bend if p.relaxed]
        offset = max(p.axis_offset_mm or 0.0 for p in bend)
        tolerance = max(p.axis_tolerance_mm or 0.0 for p in relaxed) if relaxed else strict_tolerance
        bend_pairing.append(
            {
                "face_ids": faces,
                "axis_offset_mm": offset,
                "tolerance_mm": tolerance,
                "rule": "relaxed" if relaxed else "strict",
            }
        )
        if _geometric_less(radius, min_radius, thickness, tolerance if relaxed else None):
            findings.append(
                Finding(
                    rule="sheet.min_bend_radius",
                    severity="error",
                    exact=True,
                    message=(
                        f"Bend inner radius {radius:.4g} mm is {radius / thickness:.3g} times the thickness {thickness:.4g} mm, "
                        f"below {limits['min_bend_radius_ratio']:g}"
                        + (f" (one bend whose faces are split in {len(bend)} by other features)" if len(bend) > 1 else "")
                    ),
                    measured=radius,
                    limit=min_radius,
                    test="inner radius >= min_bend_radius_ratio * thickness",
                    face_ids=faces,
                    location_mm=pair.point_mm,
                    basis=(
                        "exact inner radius of a concave/convex cylinder pair whose radii differ by the sheet thickness; "
                        + (
                            f"radii within {tolerance:.3g} mm of the limit count as equal to it (relaxed pairing, see sheet.bend_tolerance)"
                            if relaxed
                            else "radii within 1e-3 of the thickness of the limit count as equal to it"
                        )
                    ),
                    extra={
                        "bend_angle_deg": math.degrees(max(p.span_rad for p in bend)),
                        "outer_radius_mm": pair.outer_radius_mm,
                        "ratio": radius / thickness,
                        "face_pairs": len(bend),
                        "axis_offset_mm": offset,
                        "axis_tolerance_mm": tolerance,
                        "pairing": "relaxed" if relaxed else "strict",
                    },
                )
            )
        if relaxed:
            findings.append(
                Finding(
                    rule="sheet.bend_tolerance",
                    severity="warn",
                    exact=False,
                    message=(
                        f"Bend {', '.join(f'F{f}' for f in faces)} was recognised with a relaxed tolerance: its inner and outer "
                        f"cylinders are {offset:.3g} mm off-axis, the exact rule allows {strict_tolerance:.3g} mm (1e-3 of the "
                        f"thickness) and the relaxed rule {tolerance:.3g} mm. Arcs drawn through rounded 'via' points are not "
                        f"exactly concentric, so the inner radius {radius:.4g} mm is good to about {offset:.2g} mm; "
                        "rebuild the bend with radius and turn for an exact check"
                    ),
                    measured=offset,
                    limit=tolerance,
                    test="axis offset <= relaxed tolerance",
                    face_ids=faces,
                    location_mm=pair.point_mm,
                    basis=(
                        "heuristic: the concave and convex cylinders are parallel, their radii differ by the sheet thickness "
                        f"within {tolerance:.3g} mm and a radial ray crosses only these two faces; the axes are not on one line"
                    ),
                    extra={
                        "axis_offset_mm": offset,
                        "exact_tolerance_mm": strict_tolerance,
                        "relaxed_tolerance_mm": tolerance,
                        "inner_radius_mm": radius,
                        "face_pairs": len(bend),
                    },
                )
            )
    planar_covered = {f for p in dominant if p.kind == "plane" for f in p.face_ids}
    partners: dict[int, set[int]] = {}
    for p in dominant:
        a, b = p.face_ids
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)
    convex = {frozenset(edge.face_ids) for edge in edges if edge.convexity == "convex"}
    sharp_bends = 0
    joints: list[brep.EdgeFact] = []
    inside = [
        edge for edge in edges
        if edge.convexity == "concave" and edge.kind == "line" and set(edge.face_ids) <= planar_covered
        and not near_tangent_edge(edge, limits["near_tangent_deg"])
    ]
    one_sided = _faces_on_one_side(solid, inv, inside, brep.coaxial_tolerance_mm(thickness))
    for edge in inside:
        if deadline is not None:
            deadline("rules")
        a, b = edge.face_ids
        if not (one_sided[(edge.id, a)] and one_sided[(edge.id, b)] and _outer_corner(a, b, partners, neighbours, convex)):
            joints.append(edge)
            continue
        sharp_bends += 1
        findings.append(
            Finding(
                rule="sheet.min_bend_radius",
                severity="warn",
                exact=False,
                message=(
                    f"Sharp bend (inside radius 0) between two sheet faces, below {min_radius:g} mm "
                    f"({limits['min_bend_radius_ratio']:g} times the thickness)"
                ),
                measured=0.0,
                limit=min_radius,
                test="inner radius >= min_bend_radius_ratio * thickness",
                face_ids=[a, b],
                edge_ids=[edge.id],
                location_mm=edge.midpoint_mm,
                basis=(
                    "heuristic: a concave straight edge between two sheet-thickness planes whose faces across the sheet meet "
                    "at the outer corner is read as a bend with no radius"
                ),
                extra={"material_angle_deg": edge.material_angle_deg},
            )
        )
    if joints:
        joint_faces = sorted({face for edge in joints for face in edge.face_ids})
        findings.append(
            Finding(
                rule="sheet.joint",
                severity="info",
                exact=False,
                message=(
                    f"{len(joints)} concave edges where sheet faces meet without a bend: a rib, gusset or T-joint (welded or "
                    "otherwise joined), not a bend, so no bend radius applies"
                ),
                measured=float(len(joints)),
                measured_unit="count",
                face_ids=joint_faces[:MAX_FACE_IDS_LISTED],
                edge_ids=[edge.id for edge in joints][:MAX_FACE_IDS_LISTED],
                location_mm=joints[0].midpoint_mm,
                basis=(
                    "heuristic: a concave straight edge between two sheet faces is a bend only when the faces across the "
                    "sheet from them meet at a convex edge, directly or through one convex face (the outer corner); "
                    "otherwise one sheet stands on the other"
                ),
                extra={"joints": len(joints), "edge_ids_total": len(joints)},
                full_face_ids=joint_faces,
            )
        )
    unrecognised = sorted(
        f for f in edge_faces
        if inv.faces[f - 1].kind == "cylinder" and f not in in_holes and _joins_two_legs(neighbours.get(f, set()) & covered, partners)
    )
    if unrecognised:
        count = len(unrecognised)
        listed = ", ".join(f"F{f}" for f in unrecognised[:20]) + (" ..." if count > 20 else "")
        findings.append(
            Finding(
                rule="sheet.bend_unrecognised",
                severity="warn",
                exact=False,
                message=(
                    f"{count} bend-like faces not evaluated: {listed} join two different legs of the sheet but have no inner or "
                    f"outer partner at the sheet thickness within {BEND_RELAXED_AXIS_MM:g} mm, so their bend radius was not "
                    "checked. The bend inner and outer radii are not concentric (arcs drawn through rounded 'via' points?) or "
                    "this is not a bend; rebuild the arcs with radius and turn so both are exactly concentric, then run the "
                    "check again"
                ),
                measured=float(count),
                measured_unit="count",
                test="bend-like faces not evaluated == 0",
                limit=0.0,
                face_ids=unrecognised[:MAX_FACE_IDS_LISTED],
                location_mm=inv.faces[unrecognised[0] - 1].origin_mm or tuple(inv.faces[unrecognised[0] - 1].bbox_min_mm),
                basis=(
                    "heuristic: a cylinder face that joins two sheet legs but is in no inner/outer pair at the sheet thickness "
                    "is read as a bend that was skipped"
                ),
                extra={"faces_total": count, "not_evaluated": count, "pairing_tolerance_mm": BEND_RELAXED_AXIS_MM},
                full_face_ids=list(unrecognised),
            )
        )
        warnings.append(
            f"{count} bend-like faces not evaluated ({listed}): no bend radius was checked there (rule sheet.bend_unrecognised)"
        )

    # holes against edges and cutouts
    hole_faces = in_holes
    candidates = [f for f in inv.faces if f.id in edge_faces and f.id not in hole_faces]
    normals = [np.array(f.normal) for f in inv.faces if f.id in planar_covered and f.normal is not None]
    min_edge = limits["min_hole_edge_ratio"] * thickness
    holes_checked = 0
    for hole in inv.holes:
        if deadline is not None:
            deadline("rules")
        if not any(_parallel(hole.axis, n) for n in normals):
            continue
        holes_checked += 1
        nearest = _nearest_edge_face(solid, inv, hole, candidates, max_distance_mm=min_edge, deadline=deadline)
        if nearest is None:
            continue
        distance, face_id = nearest
        if _less(distance, min_edge):
            findings.append(
                Finding(
                    rule="sheet.hole_to_edge",
                    severity="error",
                    exact=True,
                    message=(
                        f"Hole {hole.id} (diameter {hole.diameter_mm:g} mm) is {distance:g} mm from the edge or cutout F{face_id}, "
                        f"below {min_edge:g} mm ({limits['min_hole_edge_ratio']:g} times the thickness)"
                    ),
                    measured=distance,
                    limit=min_edge,
                    test="distance >= min_hole_edge_ratio * thickness",
                    face_ids=list(hole.face_ids) + [face_id],
                    location_mm=hole.entry_mm,
                    basis="exact minimum distance between the hole wall and the edge face (BRepExtrema), measured wall to edge",
                    extra={"hole_id": hole.id, "edge_face_id": face_id},
                )
            )
    measurements.update(
        {
            "thickness_mm": thickness,
            "other_thicknesses_mm": [c[0] for c in others],
            "bends": len(bends),
            "bend_face_pairs": len(bend_pairs),
            "sharp_bends": sharp_bends,
            "joints": len(joints),
            "unrecognised_bend_faces": unrecognised,
            "bend_pairing": {
                "exact_tolerance_mm": strict_tolerance,
                "relaxed_tolerance_mm": BEND_RELAXED_AXIS_MM,
                "bends": bend_pairing,
            },
            "holes_checked_against_edges": holes_checked,
            "off_thickness_faces": len(off),
        }
    )
    return Report(findings, measurements, checked, list(NOT_PERFORMED["sheet_metal"]), warnings)


def _nearest_edge_face(
    solid: brep.Solid,
    inv: brep.Inventory,
    hole: brep.HoleInfo,
    candidates: list[brep.FaceInfo],
    *,
    max_distance_mm: float,
    deadline: Callable[[str], None] | None = None,
) -> tuple[float, int] | None:
    """Nearest edge face relevant to the reporting limit, with an exact distance; nearest-box first."""
    lo = np.min([inv.faces[i - 1].bbox_min_mm for i in hole.face_ids], axis=0)
    hi = np.max([inv.faces[i - 1].bbox_max_mm for i in hole.face_ids], axis=0)

    def box_gap(face: brep.FaceInfo) -> float:
        below = np.maximum(np.array(face.bbox_min_mm) - hi, 0.0)
        above = np.maximum(lo - np.array(face.bbox_max_mm), 0.0)
        return float(np.linalg.norm(np.maximum(below, above)))

    best: tuple[float, int] | None = None
    for face in sorted(candidates, key=box_gap):
        if deadline is not None:
            deadline("distance")
        lower_bound = box_gap(face)
        # These bounds are rounded to 1e-9 mm; leave slack before ruling out a possible finding.
        if lower_bound > max_distance_mm + TOLERANCE_MM or (best is not None and lower_bound >= best[0]):
            break
        value = brep.distance(solid, brep.HoleRef(hole.id), brep.FaceRef(face.id), inv=inv)
        if deadline is not None:
            deadline("distance")
        if best is None or value < best[0]:
            best = (value, face.id)
    return best


@dataclass(frozen=True)
class Deadline:
    """Cooperative wall-clock budget; native operations finish before the next check."""

    max_seconds: int
    started: float = field(default_factory=lambda: time.monotonic())

    def __call__(self, phase: str) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed >= self.max_seconds:
            raise ToolFailure(
                "geometry.time_budget_exceeded",
                f"Time budget exceeded in {phase}: used {elapsed:.3f} seconds, limit {self.max_seconds} seconds",
                {"phase": phase, "elapsed_s": elapsed, "max_seconds": self.max_seconds},
            )


def run(
    process: str, solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float], tool_axis: str,
    *, deadline: Callable[[str], None] | None = None,
) -> Report:
    if deadline is not None:
        deadline("rules")
    if process == "cnc_milling":
        return check_cnc_milling(solid, inv, limits, tool_axis, deadline=deadline)
    if process == "sheet_metal":
        return check_sheet_metal(solid, inv, limits, deadline=deadline)
    raise unsupported_process(process)


__all__ = [
    "AXES",
    "DEFAULT_TOOL_AXIS",
    "LIMIT_DEFAULTS",
    "MAX_FACES",
    "MAX_FACE_IDS_LISTED",
    "NOT_PERFORMED",
    "PROCESSES",
    "TOOL_AXIS_DEFAULTED_WARNING",
    "Finding",
    "Report",
    "all_face_ids",
    "defaults_used",
    "limit_pointer",
    "parse_limits",
    "require_closed_solids",
    "run",
    "summarise",
    "unsupported_process",
]
