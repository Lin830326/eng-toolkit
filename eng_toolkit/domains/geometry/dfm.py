"""Design-for-manufacturing rules for ``geometry_check_dfm``: limits, rule checks, findings.

The geometry comes from :mod:`eng_toolkit.brep` (exact B-rep facts: how faces meet, which faces face each other across
material, holes).  This module holds no OpenCASCADE code: it compares those facts with limits and says, for every
finding, whether the number is exact or a heuristic.  Lengths are millimetres throughout; the tool converts on output.

Limits are the toolkit's own defaults (listed in :data:`LIMIT_DEFAULTS`).  They are rules of thumb, not values from a
standard, and no finding claims conformance to one.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from eng_toolkit import brep
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import UnitsError, parse_quantity, require_positive

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
TOLERANCE_MM = 1e-6  # lengths closer than this are equal: used for "exactly the sheet thickness" and for limit comparisons
PARALLEL = 1e-6  # |sin| of the angle below which two directions count as parallel
FULL_TURN = 2.0 * math.pi - 1e-6
MAX_FACES = 2500  # kept unchanged; performance verified through 2006 faces (see docs/geometry-dfm/design.md)
LIMIT_MAX_LENGTH_MM = 1e5  # a limit above 100 m is not a manufacturing limit; refusing it also keeps the arithmetic finite
LIMIT_MAX_RATIO = 1e6
LIMIT_MAX_COUNT = 6  # there are six axis directions
WALL_SEARCH_FACTOR = 5.0  # walls are searched up to this multiple of the minimum-wall limit

# key -> (kind, default, description); kind "length" takes a string with a unit, "ratio" and "count" a plain number
LIMIT_DEFAULTS: dict[str, dict[str, tuple[str, Any, str]]] = {
    "cnc_milling": {
        "min_wall": ("length", "0.8 mm", "thinnest wall or floor between two faces with material between them"),
        "tool_radius": ("length", "3 mm", "radius of the smallest end mill that must reach every internal corner"),
        "max_hole_depth_ratio": ("ratio", 10.0, "hole depth divided by diameter"),
        "min_hole_diameter": ("length", "1 mm", "smallest hole diameter"),
        "min_access_directions": ("count", 1, "planar faces reachable from fewer of the six axis directions are reported (heuristic)"),
    },
    "sheet_metal": {
        "max_thickness": ("length", "10 mm", "largest sheet thickness searched for"),
        "min_bend_radius_ratio": ("ratio", 1.0, "inner bend radius divided by sheet thickness"),
        "min_hole_edge_ratio": ("ratio", 1.5, "distance from a hole's wall to the nearest edge or cutout, divided by sheet thickness"),
    },
}

NOT_PERFORMED: dict[str, tuple[str, ...]] = {
    "cnc_milling": (
        "corner radius at the bottom of a pocket (floor to wall) and pocket depth against tool diameter",
        "undercuts, T-slots and tool length: only a one-sample-point reach test per planar face is made (cnc.tool_access, heuristic)",
        "walls between faces that are not parallel planes or coaxial cylinders (cones, tapered or free-form walls)",
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


@dataclass
class Report:
    findings: list[Finding]
    measurements: dict[str, Any]
    checked: list[str]
    not_performed: list[str]
    warnings: list[str]


def _bad(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", message, details)


def _within(key: str, value: float, ceiling: float, unit: str) -> None:
    if value > ceiling:
        raise ToolFailure(
            "input.out_of_range",
            f"limits.{key}: {value:g}{(' ' + unit) if unit else ''} is above the largest value accepted ({ceiling:g}{(' ' + unit) if unit else ''})",
            {"param": f"limits.{key}", "maximum": ceiling},
        )


def parse_limits(process: str, raw: str) -> dict[str, float]:
    """The limits for ``process``: defaults overridden by the JSON object ``raw``.  Lengths come back in mm."""
    table = LIMIT_DEFAULTS[process]
    given: dict[str, Any] = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _bad(f"limits: not valid JSON ({exc.msg} at position {exc.pos})", param="limits") from exc
        if not isinstance(parsed, dict):
            raise _bad("limits: must be a JSON object such as {\"min_wall\": \"1 mm\"}", param="limits")
        unknown = sorted(set(parsed) - set(table))
        if unknown:
            raise _bad(
                f"limits: {unknown} are not limits of process {process!r}; allowed: {sorted(table)}",
                param="limits",
                allowed=sorted(table),
            )
        given = parsed
    out: dict[str, float] = {}
    for key, (kind, default, _) in table.items():
        value = given.get(key, default)
        try:
            if kind == "length":
                if not isinstance(value, str):
                    raise UnitsError("units.bare_number", f"limits.{key}: give a length with a unit, e.g. \"1 mm\"", {"param": f"limits.{key}"})
                quantity = require_positive(parse_quantity(value, name=f"limits.{key}", dimension="length"), name=f"limits.{key}")
                out[key] = float(quantity.to("millimeter").magnitude)
                _within(key, out[key], LIMIT_MAX_LENGTH_MM, "mm")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    raise _bad(f"limits.{key}: must be a number", param=f"limits.{key}")
                number = float(value)
                if not math.isfinite(number) or number <= 0.0:
                    raise _bad(f"limits.{key}: must be a positive number, got {value!r}", param=f"limits.{key}")
                out[key] = number
                _within(key, number, LIMIT_MAX_COUNT if kind == "count" else LIMIT_MAX_RATIO, "")
        except (UnitsError, ToolFailure):
            raise
        except ValueError as exc:
            raise _bad(f"limits.{key}: {value!r} is not a number", param=f"limits.{key}") from exc
    return out


def defaults_used(process: str, raw: str) -> list[str]:
    given = set(json.loads(raw)) if raw.strip() else set()
    return [key for key in LIMIT_DEFAULTS[process] if key not in given]


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


# --------------------------------------------------------------------------- cnc_milling


def check_cnc_milling(solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float], tool_axis: str) -> Report:
    axis = AXES[tool_axis]
    findings: list[Finding] = []
    warnings: list[str] = []
    tool_radius = limits["tool_radius"]

    # internal corners: sharp concave edges parallel to the tool axis, and concave cylinder fillets around it
    edges = brep.edge_facts(solid)
    cylinders = brep.cylinder_facts(solid, inv)
    in_holes = _hole_face_ids(inv)
    sharp = fillets = 0
    for edge in edges:
        if edge.convexity == "concave" and edge.kind == "line" and _parallel(edge.direction, axis):
            sharp += 1
            findings.append(
                Finding(
                    rule="cnc.internal_corner_radius",
                    severity="error",
                    exact=True,
                    message=(
                        f"Sharp internal corner along the tool axis (radius 0): an end mill of radius {tool_radius:g} mm "
                        "cannot cut it square"
                    ),
                    measured=0.0,
                    limit=tool_radius,
                    test="corner radius >= tool_radius",
                    face_ids=list(edge.face_ids),
                    edge_ids=[edge.id],
                    location_mm=edge.midpoint_mm,
                    basis="concave straight edge between two faces, parallel to the tool axis (exact B-rep topology)",
                    extra={"material_angle_deg": edge.material_angle_deg, "edge_length_mm": edge.length_mm},
                )
            )
    for cyl in cylinders:
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
    search_result = brep.facing_pairs(solid, inv, cylinders, max_gap_mm=search)
    if not search_result.complete:
        warnings.append(
            f"The wall search stopped after {search_result.tested} candidate face pairs; thin walls beyond them may be missing"
        )
    thin = [p for p in search_result.pairs if _less(p.gap_mm, limits["min_wall"])]
    for pair in sorted(thin, key=lambda p: (p.gap_mm, p.face_ids)):
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
    smallest = min((p.gap_mm for p in search_result.pairs), default=None)

    # tool access
    access = brep.face_access(solid, inv, AXES)
    blocked = [a for a in access if len(a.open_directions) < limits["min_access_directions"]]
    for a in blocked:
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
        "internal_fillets_checked": fillets,
        "holes": len(inv.holes),
        "smallest_wall_mm": smallest,
        "wall_search_range_mm": search,
        "face_pairs_tested": search_result.tested,
        "planar_faces_reach_tested": len(access),
    }
    checked = [
        "cnc.internal_corner_radius: concave straight edges and concave cylinders parallel to the tool axis",
        "cnc.min_wall: parallel planar faces and coaxial cylinders with material between them",
        "cnc.hole_depth_ratio and cnc.min_hole_diameter: every detected round hole",
        "cnc.tool_access: planar faces, one sample point, six axis directions (heuristic)",
    ]
    return Report(findings, measurements, checked, list(NOT_PERFORMED["cnc_milling"]), warnings)


# --------------------------------------------------------------------------- sheet_metal


def _cluster(pairs: list[brep.FacingPair], inv: brep.Inventory) -> list[tuple[float, float, list[brep.FacingPair]]]:
    """``(thickness, weight, pairs)`` per thickness; weight is the face area the pairs cover."""
    clusters: list[tuple[float, float, list[brep.FacingPair]]] = []
    for pair in sorted(pairs, key=lambda p: p.gap_mm):
        weight = pair.face_area_mm2 if pair.kind == "plane" else inv.faces[pair.face_ids[0] - 1].area_mm2
        if clusters and abs(pair.gap_mm - clusters[-1][0]) <= TOLERANCE_MM:
            gap, total, members = clusters[-1]
            clusters[-1] = (gap, total + weight, members + [pair])
        else:
            clusters.append((pair.gap_mm, weight, [pair]))
    return clusters


def check_sheet_metal(solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float]) -> Report:
    findings: list[Finding] = []
    warnings: list[str] = []
    cylinders = brep.cylinder_facts(solid, inv)
    search = brep.facing_pairs(solid, inv, cylinders, max_gap_mm=limits["max_thickness"])
    if not search.complete:
        warnings.append(f"The thickness search stopped after {search.tested} candidate face pairs; the result may be incomplete")
    clusters = _cluster(list(search.pairs), inv)
    checked = [
        "sheet.thickness and sheet.thickness_uniform: parallel planes and coaxial cylinders with material between them",
        "sheet.min_bend_radius: coaxial concave/convex cylinder pairs of the sheet thickness, and sharp bends between sheet faces",
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
    edges = brep.edge_facts(solid)
    neighbours: dict[int, set[int]] = {}
    for edge in edges:
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
    others = [c for c in clusters if abs(c[0] - thickness) > TOLERANCE_MM]
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
                face_ids=off[:50],
                location_mm=inv.faces[off[0] - 1].origin_mm or tuple(inv.faces[off[0] - 1].bbox_min_mm),
                basis=(
                    "heuristic interpretation of exact data: a face is an edge face when it touches two faces of the sheet "
                    "thickness; the rest is reported"
                ),
                extra={"other_thicknesses_mm": sorted({p.gap_mm for p in other_pairs}), "faces_total": len(off)},
            )
        )

    # bends: concentric cylinder pairs of the sheet thickness
    bends = [p for p in dominant if p.kind == "cylinder" and p.span_rad is not None and p.span_rad < FULL_TURN]
    min_radius = limits["min_bend_radius_ratio"] * thickness
    for pair in sorted(bends, key=lambda p: p.face_ids):
        radius = float(pair.inner_radius_mm)
        if _less(radius, min_radius):
            findings.append(
                Finding(
                    rule="sheet.min_bend_radius",
                    severity="error",
                    exact=True,
                    message=(
                        f"Bend inner radius {radius:g} mm is {radius / thickness:.6g} times the thickness {thickness:g} mm, "
                        f"below {limits['min_bend_radius_ratio']:g}"
                    ),
                    measured=radius,
                    limit=min_radius,
                    test="inner radius >= min_bend_radius_ratio * thickness",
                    face_ids=list(pair.face_ids),
                    location_mm=pair.point_mm,
                    basis="exact inner radius of a concave/convex cylinder pair whose radii differ by the sheet thickness",
                    extra={"bend_angle_deg": math.degrees(pair.span_rad), "outer_radius_mm": pair.outer_radius_mm},
                )
            )
    planar_covered = {f for p in dominant if p.kind == "plane" for f in p.face_ids}
    sharp_bends = 0
    for edge in edges:
        a, b = edge.face_ids
        if edge.convexity == "concave" and edge.kind == "line" and a in planar_covered and b in planar_covered:
            sharp_bends += 1
            findings.append(
                Finding(
                    rule="sheet.min_bend_radius",
                    severity="error",
                    exact=False,
                    message=(
                        f"Sharp inside corner (radius 0) between two sheet faces, below {min_radius:g} mm "
                        f"({limits['min_bend_radius_ratio']:g} times the thickness)"
                    ),
                    measured=0.0,
                    limit=min_radius,
                    test="inner radius >= min_bend_radius_ratio * thickness",
                    face_ids=[a, b],
                    edge_ids=[edge.id],
                    location_mm=edge.midpoint_mm,
                    basis="heuristic: a concave straight edge between two sheet-thickness planes is read as a bend with no radius",
                    extra={"material_angle_deg": edge.material_angle_deg},
                )
            )

    # holes against edges and cutouts
    hole_faces = in_holes
    candidates = [f for f in inv.faces if f.id in edge_faces and f.id not in hole_faces]
    normals = [np.array(f.normal) for f in inv.faces if f.id in planar_covered and f.normal is not None]
    min_edge = limits["min_hole_edge_ratio"] * thickness
    holes_checked = 0
    for hole in inv.holes:
        if not any(_parallel(hole.axis, n) for n in normals):
            continue
        holes_checked += 1
        nearest = _nearest_edge_face(solid, inv, hole, candidates, max_distance_mm=min_edge)
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
            "sharp_bends": sharp_bends,
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
        lower_bound = box_gap(face)
        # These bounds are rounded to 1e-9 mm; leave slack before ruling out a possible finding.
        if lower_bound > max_distance_mm + TOLERANCE_MM or (best is not None and lower_bound >= best[0]):
            break
        value = brep.distance(solid, brep.HoleRef(hole.id), brep.FaceRef(face.id), inv=inv)
        if best is None or value < best[0]:
            best = (value, face.id)
    return best


def run(process: str, solid: brep.Solid, inv: brep.Inventory, limits: dict[str, float], tool_axis: str) -> Report:
    if process == "cnc_milling":
        return check_cnc_milling(solid, inv, limits, tool_axis)
    if process == "sheet_metal":
        return check_sheet_metal(solid, inv, limits)
    raise unsupported_process(process)


__all__ = [
    "AXES",
    "LIMIT_DEFAULTS",
    "MAX_FACES",
    "NOT_PERFORMED",
    "PROCESSES",
    "Finding",
    "Report",
    "defaults_used",
    "parse_limits",
    "run",
    "unsupported_process",
]
