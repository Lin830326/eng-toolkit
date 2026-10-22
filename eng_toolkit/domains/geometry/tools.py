"""``geometry`` sub-server: exact measurement of STEP, IGES and BREP models.

The tool parses strings into numbers through the unit gatekeeper, asks :mod:`eng_toolkit.brep` (the one owner of
OpenCASCADE) and shapes the answer into the contract.  The query grammar lives in :mod:`.queries`.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__, brep
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.geometry import convert as conversion
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry import part as partspec
from eng_toolkit.domains.geometry import queries as grammar
from eng_toolkit.domains.geometry import view as viewer
from eng_toolkit.errors import ToolFailure
from eng_toolkit.licenses import install_hint
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, ureg

ENGINE_MEASURE = "eng_toolkit.geometry.measure"
ENGINE_MAKE_PART = "eng_toolkit.geometry.make_part"
ENGINE_CHECK_DFM = "eng_toolkit.geometry.check_dfm"
ENGINE_CONVERT = "eng_toolkit.geometry.convert"
ENGINE_VIEW = "eng_toolkit.geometry.view"
TOOL_NAMES: tuple[str, ...] = ("measure", "make_part", "check_dfm", "convert", "view")
MAX_INVENTORY_LIMIT = 500
PART_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
PART_FORMATS: tuple[str, ...] = ("step", "glb", "stl", "brep")  # the order the files are written in
MESH_DEFLECTION_MM = (0.001, 100.0)
MAX_HOLES_IN_DATA = 50
READBACK_TOLERANCE = 1e-6
AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
COVERAGE: tuple[str, ...] = (
    "counterbores and countersinks are not detected: their fields are null, and a counterbore is listed as a "
    "separate blind hole beside the bore",
    "threads are not detected: a threaded hole is reported as its plain cylinder",
    "a hole is a concave cylinder whose faces cover the full circumference; slots, pockets and non-round holes are not holes",
    "a drill-point cone is not part of depth_mm, which is the cylindrical depth",
    "an IGES file written as plain surfaces (not a solid) can store a cylinder as a surface of revolution: it is "
    "listed with kind 'other', so no hole is detected in it, and with no closed solid there is no volume either; "
    "this was seen with Open CASCADE's own IGES writer in face mode, other exporters vary. Use STEP, or export "
    "the IGES as a solid, when volume or holes matter",
    "tolerances, fits and other design intent are not in the geometry",
)
CONVENTIONS: dict[str, str] = {
    "face_ids": "1-based order of the faces in the file as OpenCASCADE enumerates them",
    "hole_axis": (
        "the drilling direction, into the material; a through hole has no entry of its own, so it is drilled from "
        "the positive side of its dominant axis"
    ),
    "thru": (
        "true when exact rays along the axis direction, started just beyond each end of the bore and a little off "
        "its axis, meet nothing: both ends open to the outside. False for a blind hole with a bottom"
    ),
    "distance": (
        "shortest distance between the actual surfaces of the two entities. A face is the trimmed face, bounded by "
        "its edges, not its infinite plane: a point 30 mm above the middle of a diameter-10 hole in a plate's top "
        "face is 30.414 mm from that face, not 30. A hole is its wall cylinder, so the distance is to the nearest "
        "point of the wall, not to the axis: a diameter-10 hole whose axis is 25 mm from a parallel face is 20 mm "
        "from it. A point is a point"
    ),
    "angle": (
        "two planes: between their outward normals, 0 to 180 degrees. When either is a cylinder face or a hole, "
        "between the axes as lines, 0 to 90 degrees"
    ),
    "thickness": (
        "distance between the first two exact hits of the ray from the point along the axis; start outside the part"
    ),
}

server = FastMCP(
    "geometry",
    instructions=(
        "Exact geometry of 3D CAD files. Every length carries a unit. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)


def _invalid(message: str, **details: Any) -> UnitsError:
    return UnitsError("input.invalid", message, details)


def _list(vector) -> list[float]:
    return [float(v) for v in vector]


def _face_dict(face: brep.FaceInfo) -> dict[str, Any]:
    return {
        "id": face.id,
        "kind": face.kind,
        "area_mm2": face.area_mm2,
        "sense": face.sense,
        "normal": None if face.normal is None else _list(face.normal),
        "axis": None if face.axis is None else _list(face.axis),
        "origin_mm": None if face.origin_mm is None else _list(face.origin_mm),
        "radius_mm": face.radius_mm,
        "bbox_min_mm": _list(face.bbox_min_mm),
        "bbox_max_mm": _list(face.bbox_max_mm),
    }


def _hole_dict(hole: brep.HoleInfo) -> dict[str, Any]:
    return {
        "id": hole.id,
        "face_ids": list(hole.face_ids),
        "diameter_mm": hole.diameter_mm,
        "axis": _list(hole.axis),
        "entry_mm": _list(hole.entry_mm),
        "depth_mm": hole.depth_mm,
        "thru": hole.thru,
        "counterbore": None if hole.counterbore is None else _list(hole.counterbore),
        "countersink": None if hole.countersink is None else _list(hole.countersink),
    }


def _point_mm(raw: grammar.PointText, index: int) -> brep.PointRef:
    coordinates = []
    for name, text in (("x", raw.x), ("y", raw.y), ("z", raw.z)):
        try:
            coordinates.append(float(parse_quantity(text, name=f"queries point {name}", dimension="length").to("millimeter").magnitude))
        except UnitsError as exc:
            raise UnitsError(exc.family, f"q{index + 1}: {exc.message}", {**exc.details, "query_index": index}) from exc
    return brep.PointRef((coordinates[0], coordinates[1], coordinates[2]))


def _entity(ref: grammar.Ref, index: int) -> brep.Entity:
    if isinstance(ref, grammar.FaceId):
        return brep.FaceRef(ref.number)
    if isinstance(ref, grammar.HoleId):
        return brep.HoleRef(f"H{ref.number}")
    return _point_mm(ref, index)


def _ref_data(entity: brep.Entity, inv: brep.Inventory) -> dict[str, Any]:
    if isinstance(entity, brep.PointRef):
        return {"ref": "point", "point_mm": _list(entity.xyz_mm)}
    found = brep.lookup(inv, entity)
    if isinstance(found, brep.FaceInfo):
        return {"ref": f"F{found.id}", "face_id": found.id, "kind": found.kind}
    return {"ref": found.id, "hole_id": found.id, "face_ids": list(found.face_ids), "diameter_mm": found.diameter_mm}


def _unsupported(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("geometry.unsupported_query", message, details)


def _cylinder_radius_mm(entity: brep.Entity, inv: brep.Inventory, verb: str) -> float:
    if isinstance(entity, brep.PointRef):
        raise _unsupported(f"{verb} applies to a cylinder face or a hole, not to a point")
    found = brep.lookup(inv, entity)
    if isinstance(found, brep.HoleInfo):
        return found.diameter_mm / 2.0
    if found.kind != "cylinder" or found.radius_mm is None:
        raise _unsupported(
            f"{verb} applies to a cylinder face or a hole; F{found.id} is a {found.kind} face", kind=found.kind
        )
    return found.radius_mm


def _area_mm2(entity: brep.Entity, inv: brep.Inventory) -> float:
    if isinstance(entity, brep.PointRef):
        raise _unsupported("area applies to a face or a hole, not to a point")
    found = brep.lookup(inv, entity)
    if isinstance(found, brep.HoleInfo):
        return sum(inv.faces[i - 1].area_mm2 for i in found.face_ids)
    return found.area_mm2


@server.tool(
    name="measure",
    description=(
        "Exact measurements of a 3D CAD model file (.step .stp .iges .igs .brep; read in millimetres): "
        "volume, surface area, bounding-box size, optional mass, an inventory of faces by kind and of round "
        "holes (diameter, depth, through or blind), and up to 20 queries. 'queries' is a ';'-separated list of "
        "'distance A B', 'angle A B' (degrees), 'diameter A', 'radius A', 'area A' and "
        "'thickness P(x, y, z) <axis>' (wall thickness along a ray). A and B are F<n> (face number from "
        "data.faces), H<n> (hole from data.holes) or P(10 mm, 0 mm, 5 mm) (a point, units required); axis is one of "
        "+x -x +y -y +z -z. Results q1, q2, ... follow the order of the queries. Face and hole ids belong to one "
        "file: call once without queries to read the inventory, then ask. A file without a closed solid (a "
        "shell, loose faces) has no volume: surface_area becomes the primary value. "
        f"Needs the optional cad engine ({install_hint('cadquery-ocp-novtk')}). Mesh files (.stl) are not supported yet."
    ),
)
def measure(
    model_path: Annotated[str, Field(description="Path to the model: .step .stp .iges .igs or .brep")],
    queries: Annotated[
        str,
        Field(
            description=(
                "Optional ';'-separated queries, at most 20, e.g. 'distance F3 H1; angle F3 F6; diameter H1; "
                "thickness P(10 mm, 10 mm, 30 mm) -z'. A and B are F<n>, H<n> or P(x, y, z) with units. "
                "Empty = only the model properties and inventory"
            )
        ),
    ] = "",
    density: Annotated[
        str, Field(description="Optional material density with unit, e.g. '2700 kg/m^3'; adds 'mass'. Empty = no mass")
    ] = "",
    inventory_limit: Annotated[
        int,
        Field(description="Faces and holes listed in data, 0 to 500. inventory.json always lists all of them"),
    ] = 50,
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'um', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {
        "model_path": model_path,
        "queries": queries,
        "density": density,
        "inventory_limit": inventory_limit,
        "output_unit": output_unit,
    }

    def compute(job: JobHandle) -> Computed:
        unit_obj = parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units
        unit = str(unit_obj)
        per_mm = float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)
        density_kg_m3 = None
        if density.strip():
            rho = require_positive(parse_quantity(density, name="density", dimension="density"), name="density")
            density_kg_m3 = float(rho.to("kilogram / meter ** 3").magnitude)
        if not 0 <= inventory_limit <= MAX_INVENTORY_LIMIT:
            raise _invalid(
                f"inventory_limit must be between 0 and {MAX_INVENTORY_LIMIT}, got {inventory_limit}",
                param="inventory_limit",
            )
        parsed = grammar.parse_queries(queries)
        entities = [[_entity(ref, q.index) for ref in q.refs] for q in parsed]

        if Path(model_path).suffix.lower() == ".stl":
            raise ToolFailure(
                "input.unsupported_format",
                "model_path: '.stl' mesh files are not supported yet; a later version reads them. "
                f"Use one of {list(brep.SOLID_EXTENSIONS)}",
                {"param": "model_path", "path": str(Path(model_path)), "supported": list(brep.SOLID_EXTENSIONS)},
            )
        model_file = job.input_file(model_path, param="model_path", extensions=brep.SOLID_EXTENSIONS)
        solid = brep.read_solid(model_file, param="model_path")
        if solid.faces == 0:
            raise ToolFailure("input.invalid", f"model_path: no faces in {job.input_files[-1].path!r}", {"path": job.input_files[-1].path})
        props = brep.mass_properties(solid)
        inv = brep.inventory(solid)
        file_hash = job.input_files[-1].sha256

        def length(mm: float) -> QuantityOut:
            return QuantityOut(quantity=float(f"{mm * per_mm:.12g}"), unit=unit)

        def area(mm2: float) -> QuantityOut:
            return QuantityOut(quantity=float(f"{mm2 * per_mm**2:.12g}"), unit=str(unit_obj**2))

        size = [hi - lo for lo, hi in zip(props.bbox_min_mm, props.bbox_max_mm)]
        values: dict[str, QuantityOut] = {}
        warnings: list[str] = []
        if props.volume_mm3 is not None:
            values["volume"] = QuantityOut(
                quantity=float(f"{props.volume_mm3 * per_mm**3:.12g}"), unit=str(unit_obj**3)
            )
            primary = "volume"
        else:
            primary = "surface_area"
            warnings.append(
                f"The file holds no closed solid with positive volume (solids found: {solid.solids}), so volume and "
                "mass are not reported and surface_area is the primary value"
                + (
                    "; IGES stores surfaces unless the exporter wrote a solid"
                    if model_file.suffix.lower() in (".iges", ".igs")
                    else ""
                )
            )
            if density_kg_m3 is not None:
                warnings.append("density was given but mass needs a volume, so no mass is reported")
        values["surface_area"] = area(props.area_mm2)
        for name, extent in zip(("size_x", "size_y", "size_z"), size):
            values[name] = length(extent)
        if density_kg_m3 is not None and props.volume_mm3 is not None:
            values["mass"] = QuantityOut(
                quantity=float(f"{density_kg_m3 * props.volume_mm3 * 1e-9:.12g}"), unit="kilogram"
            )
        if solid.solids > 1:
            warnings.append(
                f"The file holds {solid.solids} solids: volume and area are sums over all of them, so bodies "
                "that overlap are counted twice"
            )
        if solid.roots_transferred < solid.roots_in_file:
            warnings.append(
                f"Only {solid.roots_transferred} of {solid.roots_in_file} root entities of the file could be "
                "transferred: the model is incomplete and the numbers describe only what was read"
            )

        results: list[dict[str, Any]] = []
        for query, refs in zip(parsed, entities):
            n = query.index + 1
            entry: dict[str, Any] = {"id": f"q{n}", "text": query.text, "verb": query.verb, "found": True, "value": None}
            try:
                entry["refs"] = [_ref_data(ref, inv) for ref in refs]
                if query.verb == "distance":
                    mm = brep.distance(solid, refs[0], refs[1], inv=inv)
                    values[f"q{n}"], entry["value"], entry["unit"] = length(mm), mm * per_mm, unit
                elif query.verb == "angle":
                    degrees = math.degrees(brep.angle(solid, refs[0], refs[1], inv=inv))
                    values[f"q{n}"] = QuantityOut(quantity=float(f"{degrees:.12g}"), unit="degree")
                    entry["value"], entry["unit"] = degrees, "degree"
                elif query.verb in ("diameter", "radius"):
                    radius = _cylinder_radius_mm(refs[0], inv, query.verb)
                    mm = 2.0 * radius if query.verb == "diameter" else radius
                    values[f"q{n}"], entry["value"], entry["unit"] = length(mm), mm * per_mm, unit
                elif query.verb == "area":
                    mm2 = _area_mm2(refs[0], inv)
                    values[f"q{n}"], entry["value"], entry["unit"] = area(mm2), mm2 * per_mm**2, str(unit_obj**2)
                else:
                    point = refs[0]
                    direction = AXIS_VECTORS[query.axis]
                    entry["axis"], entry["direction"] = query.axis, list(direction)
                    mm = brep.wall_thickness(solid, point.xyz_mm, direction)
                    if mm is None:
                        entry["found"] = False
                        warnings.append(
                            f"q{n}: the ray from {_list(point.xyz_mm)} mm along {query.axis} meets the part in fewer than "
                            "two places, so there is no wall thickness; start the ray outside the part, facing the wall"
                        )
                    else:
                        values[f"q{n}"], entry["value"], entry["unit"] = length(mm), mm * per_mm, unit
            except ToolFailure as exc:
                raise ToolFailure(
                    exc.family,
                    f"q{n}: {exc.message}",
                    {**exc.details, "query_index": query.index, "query": query.text},
                ) from exc
            results.append(entry)

        artifact = job.write_json(
            "inventory.json",
            {
                "unit": "mm",
                "face_ids_valid_for": file_hash,
                "conventions": CONVENTIONS,
                "faces": [_face_dict(f) for f in inv.faces],
                "holes": [_hole_dict(h) for h in inv.holes],
                "edges": inv.edges,
                "vertices": inv.vertices,
            },
        )
        data: dict[str, Any] = {
            "solids": solid.solids,
            "faces_total": len(inv.faces),
            "faces_by_kind": dict(sorted(Counter(f.kind for f in inv.faces).items())),
            "holes_total": len(inv.holes),
            "holes": [_hole_dict(h) for h in inv.holes[:inventory_limit]],
            "faces": [_face_dict(f) for f in inv.faces[:inventory_limit]],
            "edges": inv.edges,
            "vertices": inv.vertices,
            "centroid_mm": _list(props.centroid_mm),
            "bbox_min_mm": _list(props.bbox_min_mm),
            "bbox_max_mm": _list(props.bbox_max_mm),
            "step_roots": {"in_file": solid.roots_in_file, "transferred": solid.roots_transferred},
            "queries": results,
            "face_ids_valid_for": file_hash,
            "conventions": CONVENTIONS,
            "coverage": list(COVERAGE),
        }
        return Computed(values=values, primary=primary, warnings=warnings, data=data, artifacts=[artifact])

    return run_tool(
        tool_name="geometry_measure",
        engine=ENGINE_MEASURE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "Measures are exact B-rep properties of the file as read, not of a tessellation. Units are millimetres "
            "as OpenCASCADE reads them (STEP and IGES carry their unit; BREP numbers are taken as millimetres). "
            "Face and hole ids are specific to this file's bytes and this OpenCASCADE version (see "
            "face_ids_valid_for); do not reuse them for another or re-exported file.",
        ),
    )


def _finding_dict(finding: dfm.Finding, per_mm: float, unit: str) -> dict[str, Any]:
    def number(value: float | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if finding.measured_unit == "mm":
            return {"value": float(f"{value * per_mm:.12g}"), "unit": unit}
        return {"value": float(f"{value:.12g}"), "unit": finding.measured_unit}

    return {
        "rule": finding.rule,
        "severity": finding.severity,
        "check": "exact" if finding.exact else "heuristic",
        "message": finding.message,
        "measured": number(finding.measured),
        "limit": number(finding.limit),
        "passes_when": finding.test or None,
        "face_ids": finding.face_ids,
        "edge_ids": finding.edge_ids,
        "location_mm": None if finding.location_mm is None else _list(finding.location_mm),
        "basis": finding.basis,
        "details": finding.extra,
    }


DFM_CONVENTIONS: dict[str, str] = {
    "face_ids": "same numbering as geometry_measure: valid for this file's bytes and this OpenCASCADE version only",
    "edge_ids": "1-based index of the edge in OpenCASCADE's edge-to-face map: valid for this file's bytes and this OpenCASCADE version only",
    "location_mm": "a point in model coordinates, millimetres: an edge midpoint, a point on the face, or a point in the material between two faces",
    "check": (
        "'exact' = the number is an exact B-rep quantity (a radius, a plane offset, an exact distance) and the rule applies it as "
        "stated; 'heuristic' = it depends on a sampled test or on reading a shape as a feature, and may be wrong in either direction"
    ),
    "limits": (
        "The default limits are this toolkit's own rules of thumb; they are not taken from any standard or handbook, and no finding "
        "says a part conforms to one. Set limits to your shop's values"
    ),
    "severity": "error = an exact rule is violated; warn = a heuristic rule is violated; info = a measured fact, no limit applied",
}

CHECK_DFM_DESCRIPTION = (
    "Design-for-manufacturing checks of a solid 3D CAD file (.step .stp .iges .igs .brep, millimetres) for one process: "
    "'cnc_milling' (internal corner radius against the tool radius, thinnest wall, hole depth/diameter ratio, smallest hole, "
    "planar faces no axis direction reaches) or 'sheet_metal' (uniform thickness, bend radius against thickness, hole-to-edge "
    "distance). Other processes (turning, injection_molding, additive, ...) are not supported and are refused, not guessed. "
    "Each finding has a rule id, severity (error, warn, info), the measured value and the limit, face and edge ids, a point "
    "in model coordinates and whether the check is 'exact' or 'heuristic'; the result also counts findings by severity and "
    "lists the checks that were NOT performed. 'limits' is an optional JSON object that overrides the defaults, e.g. "
    '{"min_wall": "1 mm", "tool_radius": "2 mm"} for cnc_milling (keys: ' + ", ".join(dfm.LIMIT_DEFAULTS["cnc_milling"]) + "), or "
    '{"min_bend_radius_ratio": 1.5} for sheet_metal (keys: ' + ", ".join(dfm.LIMIT_DEFAULTS["sheet_metal"]) + "). "
    "The defaults are the toolkit's own rules of thumb, not a standard. A file without a closed solid is refused. "
    "max_seconds is a wall-clock budget (default 120, 1 to 600 seconds). On expiry, returns "
    "geometry.time_budget_exceeded at the next check. An OpenCASCADE call already running cannot be interrupted. "
    f"Needs the optional cad engine ({install_hint('cadquery-ocp-novtk')})."
)


@server.tool(name="check_dfm", description=CHECK_DFM_DESCRIPTION)
def check_dfm(
    model_path: Annotated[str, Field(description="Path to the model: .step .stp .iges .igs or .brep")],
    process: Annotated[str, Field(description="'cnc_milling' or 'sheet_metal'; anything else is refused")] = "cnc_milling",
    limits: Annotated[
        str,
        Field(
            description=(
                "Optional JSON object of limits overriding the defaults, lengths as strings with a unit: "
                '{"min_wall": "1 mm", "tool_radius": "2 mm", "max_hole_depth_ratio": 8}. Unknown keys are refused. Empty = defaults'
            )
        ),
    ] = "",
    tool_axis: Annotated[
        str, Field(description="cnc_milling: the spindle axis, one of +x -x +y -y +z -z; internal corners are judged along it")
    ] = "+z",
    max_findings: Annotated[
        int, Field(description="Findings listed in data, 1 to 500. The summary counts and dfm_findings.json cover all of them")
    ] = 100,
    output_unit: Annotated[str, Field(description="Length unit of the measured values and limits, e.g. 'mm', 'inch'")] = "mm",
    max_seconds: Annotated[
        int, Field(description="Wall-clock budget in seconds, 1 to 600; default 120. Stops at the next check; a running OpenCASCADE call cannot be interrupted")
    ] = 120,
) -> ToolResult:
    deadline = dfm.Deadline(max_seconds)
    inputs = {
        "model_path": model_path,
        "process": process,
        "limits": limits,
        "tool_axis": tool_axis,
        "max_findings": max_findings,
        "max_seconds": max_seconds,
        "output_unit": output_unit,
    }

    def compute(job: JobHandle) -> Computed:
        if not 1 <= max_seconds <= 600:
            raise ToolFailure(
                "input.out_of_range",
                f"max_seconds: must be between 1 and 600, got {max_seconds}",
                {"param": "max_seconds"},
            )
        name = process.strip().lower()
        if name not in dfm.PROCESSES:
            raise dfm.unsupported_process(process)
        unit_obj = parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units
        unit = str(unit_obj)
        per_mm = float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)
        axis_name = tool_axis.strip().lower()
        if axis_name not in dfm.AXES:
            raise _invalid_param("tool_axis", f"{tool_axis!r} is not one of {list(dfm.AXES)}", allowed=list(dfm.AXES))
        if not 1 <= max_findings <= MAX_INVENTORY_LIMIT:
            raise _invalid_param("max_findings", f"must be between 1 and {MAX_INVENTORY_LIMIT}, got {max_findings}")
        limit_mm = dfm.parse_limits(name, limits)
        if Path(model_path).suffix.lower() == ".stl":
            raise ToolFailure(
                "input.unsupported_format",
                "model_path: '.stl' mesh files are not supported; DFM needs an exact B-rep. "
                f"Use one of {list(brep.SOLID_EXTENSIONS)}",
                {"param": "model_path", "supported": list(brep.SOLID_EXTENSIONS)},
            )
        deadline("read")
        model_file = job.input_file(model_path, param="model_path", extensions=brep.SOLID_EXTENSIONS)
        solid = brep.read_solid(model_file, param="model_path")
        deadline("read")
        file_hash = job.input_files[-1].sha256
        if solid.faces == 0:
            raise ToolFailure("input.invalid", f"model_path: no faces in {job.input_files[-1].path!r}", {"path": job.input_files[-1].path})
        if solid.solids == 0 or brep.mass_properties(solid).volume_mm3 is None:
            raise ToolFailure(
                "geometry.not_a_solid",
                f"model_path holds {solid.faces} faces but no closed solid with positive volume (solids found: {solid.solids}); "
                "manufacturability checks need material between faces, so a shell or loose surfaces are refused. "
                "Export the part as a solid",
                {"solids": solid.solids, "faces": solid.faces},
            )
        if solid.faces > dfm.MAX_FACES:
            raise ToolFailure(
                "input.too_large",
                f"model_path has {solid.faces} faces; the limit is {dfm.MAX_FACES}",
                {"faces": solid.faces, "limit": dfm.MAX_FACES},
            )
        deadline("inventory")
        inv = brep.inventory(solid, deadline=deadline)
        deadline("inventory")
        report = dfm.run(name, solid, inv, limit_mm, axis_name, deadline=deadline)
        deadline("rules")

        warnings = list(report.warnings)
        if solid.solids > 1:
            warnings.append(
                f"The file holds {solid.solids} solids: they are checked together as one body, so a wall between two separate "
                "solids is not recognised as one, and a face id belongs to whichever solid holds it"
            )
        if solid.roots_transferred < solid.roots_in_file:
            warnings.append(
                f"Only {solid.roots_transferred} of {solid.roots_in_file} root entities of the file could be transferred: "
                "the model is incomplete and only what was read was checked"
            )
        used = dfm.defaults_used(name, limits)
        if used:
            warnings.append(
                "Default limits were used for " + ", ".join(used) + ": they are this toolkit's rules of thumb, not values from a "
                "standard; pass 'limits' with your own"
            )

        rank = {severity: i for i, severity in enumerate(dfm.SEVERITIES)}
        ordered = sorted(report.findings, key=lambda f: (rank[f.severity], f.rule))
        counts = Counter(f.severity for f in ordered)
        by_rule: dict[str, dict[str, int]] = {}
        for f in ordered:
            by_rule.setdefault(f.rule, {}).setdefault(f.severity, 0)
            by_rule[f.rule][f.severity] += 1
        all_findings = [_finding_dict(f, per_mm, unit) for f in ordered]
        shown = all_findings[:max_findings]
        if len(all_findings) > len(shown):
            warnings.append(f"{len(all_findings)} findings, {len(shown)} listed in data; dfm_findings.json holds all of them")

        limit_report: dict[str, Any] = {}
        for key, (kind, _default, description) in dfm.LIMIT_DEFAULTS[name].items():
            value = limit_mm[key]
            limit_report[key] = {
                "value": float(f"{value * per_mm:.12g}") if kind == "length" else value,
                "unit": unit if kind == "length" else kind,
                "source": "default" if key in used else "caller",
                "meaning": description,
            }
        summary = {
            "errors": counts["error"],
            "warnings": counts["warn"],
            "info": counts["info"],
            "total": len(ordered),
            "by_rule": by_rule,
            "exact_findings": sum(1 for f in ordered if f.exact),
            "heuristic_findings": sum(1 for f in ordered if not f.exact),
        }
        measurements = dict(report.measurements)
        artifact = job.write_json(
            "dfm_findings.json",
            {
                "process": name,
                "face_ids_valid_for": file_hash,
                "conventions": DFM_CONVENTIONS,
                "limits": limit_report,
                "summary": summary,
                "findings": all_findings,
                "measurements": measurements,
                "checked": report.checked,
                "not_performed": report.not_performed,
            },
        )
        values: dict[str, QuantityOut] = {
            "errors": QuantityOut(quantity=float(counts["error"]), unit="dimensionless"),
            "warnings": QuantityOut(quantity=float(counts["warn"]), unit="dimensionless"),
            "info": QuantityOut(quantity=float(counts["info"]), unit="dimensionless"),
        }
        for key in ("smallest_wall_mm", "thickness_mm"):
            if measurements.get(key) is not None:
                values[key.removesuffix("_mm")] = QuantityOut(quantity=float(f"{measurements[key] * per_mm:.12g}"), unit=unit)
        data: dict[str, Any] = {
            "process": name,
            "supported_processes": list(dfm.PROCESSES),
            "tool_axis": axis_name if name == "cnc_milling" else None,
            "solids": solid.solids,
            "faces_total": len(inv.faces),
            "holes_total": len(inv.holes),
            "limits": limit_report,
            "summary": summary,
            "findings": shown,
            "findings_listed": len(shown),
            "measurements": measurements,
            "checked": report.checked,
            "not_performed": report.not_performed,
            "face_ids_valid_for": file_hash,
            "conventions": DFM_CONVENTIONS,
        }
        deadline("rules")
        return Computed(values=values, primary="errors", warnings=warnings, data=data, artifacts=[artifact])

    return run_tool(
        tool_name="geometry_check_dfm",
        engine=ENGINE_CHECK_DFM,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "Manufacturability findings are rule-of-thumb checks on the geometry alone, with limits chosen by this toolkit or "
            "the caller. Nothing here states conformance to a standard, and what is not listed under data.checked was not "
            "checked (see data.not_performed). A clean result does not mean the part can be made; a qualified engineer must review it.",
        ),
    )


MAKE_PART_DESCRIPTION = (
    "Build a 3D part from a declarative JSON spec (nothing is executed) and write STEP, optionally GLB, STL and BREP, "
    "with exact volume, surface area, bounding-box size and optional mass. Needs the optional cad engine "
    f"({install_hint('cadquery-ocp-novtk')}).\n"
    f"{partspec.grammar_text()}\n"
    'Divide by plain numbers ("$L / 4"), not by lengths. '
    f"Example: {json.dumps(partspec.EXAMPLE_SPEC, separators=(',', ':'))}\n"
    "Errors: input.invalid has details.pointer (e.g. /features/2/diameter) and details.allowed; geometry.empty_selection and "
    "geometry.build_failed name the failing feature_index. Also writes <name>.features.json (declared holes, bound to the STEP by sha256) and spec.json."
)


def _invalid_param(param: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", f"{param}: {message}", {"param": param, **details})


def _part_formats(formats: str) -> list[str]:
    wanted = [item.strip().lower() for item in formats.split(",") if item.strip()]
    unknown = [item for item in wanted if item not in PART_FORMATS]
    if unknown:
        raise _invalid_param("formats", f"{unknown} is not a format; use a comma list of {list(PART_FORMATS)}", allowed=list(PART_FORMATS))
    return [fmt for fmt in PART_FORMATS if fmt == "step" or fmt in wanted]  # STEP is the read-back reference, so it is always written


@server.tool(name="make_part", description=MAKE_PART_DESCRIPTION)
def make_part(
    spec: Annotated[
        str,
        Field(description="Part spec as a JSON string, at most 64 kB: {params, features}. The grammar and an example are in the tool description"),
    ],
    name: Annotated[str, Field(description="Product name in the STEP file and stem of the output files: 1 to 64 letters, digits, '_' or '-'")] = "part",
    formats: Annotated[
        str, Field(description="Comma list of files to write besides the always-written STEP: 'glb', 'stl', 'brep'. Empty = STEP only")
    ] = "step",
    mesh_deflection: Annotated[
        str, Field(description="Chord error of the GLB/STL tessellation, 0.001 mm to 100 mm, e.g. '0.01 mm'")
    ] = "0.01 mm",
    density: Annotated[
        str, Field(description="Optional material density with unit, e.g. '2700 kg/m^3'; adds 'mass'. Empty = no mass")
    ] = "",
    output_unit: Annotated[str, Field(description="Length unit of the results, e.g. 'mm', 'cm', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {
        "spec": spec,
        "name": name,
        "formats": formats,
        "mesh_deflection": mesh_deflection,
        "density": density,
        "output_unit": output_unit,
    }

    def compute(job: JobHandle) -> Computed:
        unit_obj = parse_quantity(f"1 {output_unit}", name="output_unit", dimension="length").units
        unit = str(unit_obj)
        per_mm = float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)
        density_kg_m3 = None
        if density.strip():
            rho = require_positive(parse_quantity(density, name="density", dimension="density"), name="density")
            density_kg_m3 = float(rho.to("kilogram / meter ** 3").magnitude)
        deflection_mm = float(parse_quantity(mesh_deflection, name="mesh_deflection", dimension="length").to("millimeter").magnitude)
        if not MESH_DEFLECTION_MM[0] <= deflection_mm <= MESH_DEFLECTION_MM[1]:
            raise _invalid_param(
                "mesh_deflection",
                f"must be between {MESH_DEFLECTION_MM[0]:g} mm and {MESH_DEFLECTION_MM[1]:g} mm, got {deflection_mm:g} mm",
                limits_mm=list(MESH_DEFLECTION_MM),
            )
        if not PART_NAME.fullmatch(name):
            raise _invalid_param("name", f"{name!r} must be 1 to 64 ASCII letters, digits, '_' or '-'", pattern=PART_NAME.pattern)
        wanted = _part_formats(formats)

        def open_model(raw: str, pointer: str) -> tuple[Path, str]:
            opened = job.input_file(raw, param=pointer, extensions=brep.SOLID_EXTENSIONS)
            return opened, job.input_files[-1].sha256

        parsed = partspec.parse_spec(spec)
        built = partspec.build(parsed, open_file=open_model)
        props = brep.mass_properties(built.solid)

        with tempfile.TemporaryDirectory(prefix="eng-toolkit-") as scratch:
            step_path = Path(scratch) / "part.step"
            brep.write_step(built.solid, step_path, product_name=name)
            step_art = job.write_bytes(f"{name}.step", step_path.read_bytes(), fmt="step")
            written = props.volume_mm3 or 0.0
            read_back = brep.read_solid(Path(step_art.path), param="step")
            read = brep.mass_properties(read_back).volume_mm3 or 0.0
            difference = abs(written - read) / max(abs(written), 1e-300)
            if read_back.solids != 1 or difference > READBACK_TOLERANCE:
                raise ToolFailure(
                    "geometry.readback_mismatch",
                    f"the STEP file read back as {read_back.solids} solids with volume {read:.9g} mm^3 instead of {written:.9g} mm^3",
                    {"volume_written_mm3": written, "volume_read_mm3": read, "solids_read": read_back.solids},
                )
            artifacts = [
                step_art,
                job.write_json(
                    f"{name}.features.json", partspec.features_document(parsed, built, step_art.sha256, part_name=name)
                ),
                job.write_text("spec.json", parsed.canonical, fmt="json"),
            ]
            mesh_info: dict[str, Any] | None = None
            if "glb" in wanted or "stl" in wanted:
                mesh = brep.triangulate(built.solid, deflection_mm)
                mesh_info = {"deflection_mm": deflection_mm, "triangles": int(len(mesh.triangles))}
                for fmt in ("glb", "stl"):
                    if fmt in wanted:
                        target = Path(scratch) / f"part.{fmt}"
                        brep.write_mesh(mesh, target, fmt=fmt, name=name)
                        artifacts.append(job.write_bytes(f"{name}.{fmt}", target.read_bytes(), fmt=fmt))
            if "brep" in wanted:
                target = Path(scratch) / "part.brep"
                brep.write_brep(built.solid, target)
                artifacts.append(job.write_bytes(f"{name}.brep", target.read_bytes(), fmt="brep"))

        def length(mm: float) -> QuantityOut:
            return QuantityOut(quantity=float(f"{mm * per_mm:.12g}"), unit=unit)

        volume_mm3 = props.volume_mm3 or 0.0
        values: dict[str, QuantityOut] = {
            "volume": QuantityOut(quantity=float(f"{volume_mm3 * per_mm**3:.12g}"), unit=str(unit_obj**3)),
            "surface_area": QuantityOut(quantity=float(f"{props.area_mm2 * per_mm**2:.12g}"), unit=str(unit_obj**2)),
        }
        for axis, lo, hi in zip(("size_x", "size_y", "size_z"), props.bbox_min_mm, props.bbox_max_mm):
            values[axis] = length(hi - lo)
        if density_kg_m3 is not None:
            values["mass"] = QuantityOut(quantity=float(f"{density_kg_m3 * volume_mm3 * 1e-9:.12g}"), unit="kilogram")

        warnings = list(built.warnings)
        callouts = {h.feature: h for h in built.holes if h.thread or h.fit}
        if callouts:
            shown = "; ".join(
                f"{feature}: " + ", ".join(f"{label} {value}" for label, value in (("thread", h.thread), ("fit", h.fit)) if value)
                for feature, h in callouts.items()
            )
            warnings.append(
                f"Hole callouts ({shown}) are text in the features file only: the holes are modelled at their nominal "
                "diameter and no thread geometry exists"
            )
        data: dict[str, Any] = {
            "name": name,
            "formats": wanted,
            "features": [record.to_dict() for record in built.records],
            "holes_total": len(built.holes),
            "holes": [hole.to_dict() for hole in built.holes[:MAX_HOLES_IN_DATA]],
            "imports": [item.to_dict() for item in built.imports],
            "params": dict(parsed.params),
            "bbox": {"min_mm": list(props.bbox_min_mm), "max_mm": list(props.bbox_max_mm)},
            "solids": built.solid.solids,
            "faces": built.solid.faces,
            "readback": {
                "volume_written_mm3": written,
                "volume_read_mm3": read,
                "relative_difference": difference,
                "solids_read": read_back.solids,
                "faces_read": read_back.faces,
            },
            "spec_sha256": parsed.sha256,
            "step_sha256": step_art.sha256,
        }
        if mesh_info is not None:
            data["mesh"] = mesh_info
        return Computed(values=values, primary="volume", warnings=warnings, data=data, artifacts=artifacts)

    return run_tool(
        tool_name="geometry_make_part",
        engine=ENGINE_MAKE_PART,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "The part is built exactly as the spec says and checked for validity by OpenCASCADE; it is an unreviewed model, "
            "not an engineering-approved design. Dimensions, fits, threads and material are the caller's responsibility.",
        ),
    )


CONVERT_DESCRIPTION = (
    "Convert a 3D model file to another format and report what the conversion lost. Input: STEP/IGES/BREP (.step .stp .iges .igs "
    ".brep, read in millimetres) or an STL mesh (.stl, binary or ASCII, read as millimetres). Output 'step', 'brep' (exact, from "
    "STEP/IGES/BREP only), 'stl' (binary, millimetres) or 'glb' (glTF binary: metres and +Y up, the conversion is applied). STL as "
    "input converts to 'stl' or 'glb' only: a mesh is not a solid and is never turned into STEP or BREP. The converted file is an "
    "artifact with its sha256; the input is never modified. Mesh outputs tessellate the exact surfaces to triangles within "
    "mesh_deflection. Returns volume_in, volume_out and volume_relative_change (read back from the written file; no volume for a "
    "shell or an open mesh, then surface_area_out is the primary value), triangle_count for mesh outputs, and data.losses: "
    "every loss in plain words (exact surfaces become triangles, units converted, GLB axes, names/colours/assembly dropped, "
    f"float32 precision). Needs the optional cad engine for STEP/IGES/BREP input or output ({install_hint('cadquery-ocp-novtk')}); "
    "STL to STL or GLB needs no engine."
)


@server.tool(name="convert", description=CONVERT_DESCRIPTION)
def convert(
    input_path: Annotated[str, Field(description="Path to the model: .step .stp .iges .igs .brep or .stl, at most 100 MB")],
    output_format: Annotated[str, Field(description="'step', 'stl', 'glb' or 'brep'. An STL input can only go to 'stl' or 'glb'")],
    mesh_deflection: Annotated[
        str, Field(description="Chord error of the tessellation for stl/glb output of a STEP/IGES/BREP, 0.001 mm to 100 mm, e.g. '0.01 mm'")
    ] = "0.01 mm",
    name: Annotated[
        str, Field(description="Stem of the output file and product name: 1 to 64 letters, digits, '_' or '-'. Empty = the input file's stem")
    ] = "",
) -> ToolResult:
    inputs = {"input_path": input_path, "output_format": output_format, "mesh_deflection": mesh_deflection, "name": name}
    return run_tool(
        tool_name="geometry_convert",
        engine=ENGINE_CONVERT,
        version=__version__,
        inputs=inputs,
        compute=lambda job: conversion.convert(
            job, input_path=input_path, output_format=output_format, mesh_deflection=mesh_deflection, name=name
        ),
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "A converted file is an unreviewed copy: read data.losses for what it no longer holds. Check the converted file in the "
            "system that will use it before relying on it; a qualified engineer must review anything built from it.",
        ),
    )


VIEW_DESCRIPTION = (
    "Write a solid 3D CAD file (.step .stp .iges .igs .brep, millimetres) as a coloured GLB for web, AR and CAD viewers: one "
    "material per colour, each primitive's extras list its face ids (F<n>, as in geometry_measure) with their triangle ranges, "
    "the node's extras hold the legend; B-rep edges as lines if show_edges. color_by: 'none' (grey), 'face_kind' (plane, "
    "cylinder, cone, sphere, torus, bspline, other), 'dfm' (worst geometry_check_dfm finding per face: error red, warn orange, "
    "info blue; process, tool_axis, limits as there) or 'highlight' (faces in highlight, e.g. 'F3,F7'). GLB is metres, +Y up. "
    "Also writes view.json (face -> colour -> finding ids). Returns triangle_count, face_count, highlighted_face_count, "
    "finding_count. Shells, loose faces and .stl are refused. "
    f"Needs the optional cad engine ({install_hint('cadquery-ocp-novtk')})."
)


@server.tool(name="view", description=VIEW_DESCRIPTION)
def view(
    model_path: Annotated[str, Field(description="Path to the solid model: .step .stp .iges .igs or .brep")],
    color_by: Annotated[str, Field(description="'none', 'face_kind', 'dfm' or 'highlight'; anything else is refused")] = "face_kind",
    highlight: Annotated[
        str, Field(description="color_by 'highlight' only: face ids as in geometry_measure, e.g. 'F3,F7'; an unknown id is refused")
    ] = "",
    process: Annotated[str, Field(description="color_by 'dfm' only: 'cnc_milling' or 'sheet_metal', as in geometry_check_dfm")] = "cnc_milling",
    tool_axis: Annotated[str, Field(description="color_by 'dfm', cnc_milling: spindle axis, one of +x -x +y -y +z -z")] = "+z",
    limits: Annotated[
        str, Field(description='color_by \'dfm\' only: JSON limits as in geometry_check_dfm, e.g. {"min_wall": "1 mm"}. Empty = defaults')
    ] = "",
    max_seconds: Annotated[
        int, Field(description="Wall-clock budget in seconds, 1 to 600; default 120. A running OpenCASCADE call cannot be interrupted")
    ] = 120,
    mesh_deflection: Annotated[str, Field(description="Chord error of the tessellation, 0.001 mm to 100 mm, e.g. '0.01 mm'")] = "0.01 mm",
    name: Annotated[
        str, Field(description="Stem of the GLB file and node name: 1 to 64 letters, digits, '_' or '-'. Empty = the input file's stem")
    ] = "view",
    show_edges: Annotated[bool, Field(description="Add the B-rep edges as a line primitive (GL_LINES) so CAD edges stay visible")] = True,
) -> ToolResult:
    deadline = dfm.Deadline(max(1, min(max_seconds, 600)))
    inputs = {
        "model_path": model_path,
        "color_by": color_by,
        "highlight": highlight,
        "process": process,
        "tool_axis": tool_axis,
        "limits": limits,
        "max_seconds": max_seconds,
        "mesh_deflection": mesh_deflection,
        "name": name,
        "show_edges": show_edges,
    }

    def compute(job: JobHandle) -> Computed:
        try:
            return viewer.view(
                job,
                model_path=model_path,
                color_by=color_by,
                highlight=highlight,
                process=process,
                tool_axis=tool_axis,
                limits=limits,
                max_seconds=max_seconds,
                mesh_deflection=mesh_deflection,
                name=name,
                show_edges=show_edges,
                deadline=deadline,
            )
        except ToolFailure as exc:  # every refusal names its parameter as a JSON pointer
            details = {**exc.details, "param": viewer.pointer(exc.details, exc.family)}
            raise ToolFailure(exc.family, exc.message, details) from None
        except (ArithmeticError, FloatingPointError) as exc:
            raise ToolFailure("input.out_of_range", f"model_path: a number is outside the supported range ({exc})", {"param": "/model_path"}) from None

    return run_tool(
        tool_name="geometry_view",
        engine=ENGINE_VIEW,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "The colours are this toolkit's coding of the geometry (face kind) or of rule-of-thumb DFM findings, not an engineering "
            "approval; a neutral face is not a checked face. Face ids belong to this file's bytes (face_ids_valid_for).",
        ),
    )


__all__ = ["TOOL_NAMES", "check_dfm", "convert", "make_part", "measure", "server", "view"]
