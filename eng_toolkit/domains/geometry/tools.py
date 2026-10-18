"""``geometry`` sub-server: exact measurement of STEP, IGES and BREP models.

The tool parses strings into numbers through the unit gatekeeper, asks :mod:`eng_toolkit.brep` (the one owner of
OpenCASCADE) and shapes the answer into the contract.  The query grammar lives in :mod:`.queries`.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__, brep
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.geometry import queries as grammar
from eng_toolkit.errors import ToolFailure
from eng_toolkit.licenses import install_hint
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, ureg

ENGINE_MEASURE = "eng_toolkit.geometry.measure"
TOOL_NAMES: tuple[str, ...] = ("measure",)
MAX_INVENTORY_LIMIT = 500
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
            raise ToolFailure("input.invalid", f"model_path: no faces in {str(model_file)!r}", {"path": str(model_file)})
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


__all__ = ["TOOL_NAMES", "measure", "server"]
