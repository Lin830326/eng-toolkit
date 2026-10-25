"""``geometry_convert``: STEP, IGES and BREP to STEP, STL, GLB or BREP, and STL to STL or GLB, with a loss report.

The pure parts (the STL reader, the GLB reader used to read our own output back, mesh volume and area, the length unit a
STEP file declares) need neither OpenCASCADE nor a file system and are tested on their own.  OpenCASCADE is reached only
through :mod:`eng_toolkit.brep`; an STL to STL or GLB conversion does not need it at all.

Every number in the answer that describes a mesh output (``volume_out``, ``triangle_count``) is read back from the
bytes of the file that is handed to the caller, not taken from the data structure that was written.
"""

from __future__ import annotations

from eng_toolkit import jsonsafe
import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from eng_toolkit import brep
from eng_toolkit.contract import QuantityOut
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle
from eng_toolkit.units import parse_quantity
from eng_toolkit.domains.geometry._numeric import out_of_range

FORMATS: tuple[str, ...] = ("step", "stl", "glb", "brep")
MESH_FORMATS: tuple[str, ...] = ("stl", "glb")
INPUT_EXTENSIONS: tuple[str, ...] = (*brep.SOLID_EXTENSIONS, ".stl")
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
DEFLECTION_MM = (0.001, 100.0)
MAX_ABS_MM = 1.0e6  # a coordinate beyond 1 km is a unit mistake; also keeps float32 mesh files meaningful
MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_TRIANGLES = 2_000_000  # an STL read as input (pure NumPy, about 100 bytes per triangle)
MAX_TESSELLATION_TRIANGLES = 600_000  # a B-rep tessellated by OpenCASCADE, which holds about 6.6 kB per triangle while meshing
MAX_SIZE_PER_DEFLECTION = 5.0e6  # bounding-box diagonal over deflection: guards the time of the tessellation
READBACK_TOLERANCE = 1e-6
MAX_LOOSE_FACES_LISTED = 200  # loose face ids in data.input.closure
FLOAT32_RELATIVE = 2.0**-24  # half a unit in the last place of a float32 mantissa
MM3 = "millimeter ** 3"
MM2 = "millimeter ** 2"
DIMENSIONLESS = "dimensionless"
STL_RECORD = np.dtype([("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])  # 50 bytes, packed
ASCII_VERTEX = re.compile(rb"\bvertex[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)", re.IGNORECASE)


def _bad(message: str, family: str = "input.invalid", **details: Any) -> ToolFailure:
    return ToolFailure(family, message, details)


# --------------------------------------------------------------------------- mesh arithmetic (pure)


def mesh_volume_area(vertices_mm: np.ndarray, triangles: np.ndarray) -> tuple[float, float]:
    """Signed volume (positive when the triangles wind counter-clockwise seen from outside) and surface area of a triangle mesh.

    Volume is the sum of the signed tetrahedra from a reference point to each triangle, ``dot(a, cross(b, c)) / 6``; the
    reference point is the mean vertex, which keeps the cancellation small for a part far from the origin.
    """
    v = np.asarray(vertices_mm, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if len(t) == 0:
        return 0.0, 0.0
    corners = v[t] - v.mean(axis=0)
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    area = float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0)
    return volume, area


# --------------------------------------------------------------------------- STL (pure)


def parse_stl(data: bytes) -> np.ndarray:
    """Triangles of a binary or ASCII STL as an ``(n, 3, 3)`` float64 array (the facet normals are ignored).

    A file is binary when its size is exactly 84 + 50 * n for the n in its header, whatever its first bytes say (many
    binary files start with ``solid``); otherwise it must be ASCII.  Anything else, a non-numeric or non-finite
    coordinate, or a vertex count that is not a multiple of three, is ``input.invalid``.
    """
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if len(data) == 84 + 50 * count:
            if count == 0:
                raise _bad("the STL file has no triangles")
            if count > MAX_TRIANGLES:
                raise _bad(
                    f"the STL file holds {count} triangles; the limit is {MAX_TRIANGLES}",
                    "input.too_large",
                    triangles=count,
                    limit=MAX_TRIANGLES,
                )
            rows = np.frombuffer(data, dtype=STL_RECORD, count=count, offset=84)
            triangles = rows["corners"].astype(np.float64)
            return _checked_triangles(triangles)
    if data.lstrip()[:5].lower() != b"solid":
        raise _bad("not a readable STL file: it is neither a binary STL of the size its header states nor ASCII text starting with 'solid'")
    found = ASCII_VERTEX.findall(data)
    if not found or len(found) % 3:
        raise _bad(f"ASCII STL with {len(found)} vertex lines: need a positive multiple of 3")
    if len(found) // 3 > MAX_TRIANGLES:
        raise _bad(
            f"the STL file holds {len(found) // 3} triangles; the limit is {MAX_TRIANGLES}",
            "input.too_large",
            triangles=len(found) // 3,
            limit=MAX_TRIANGLES,
        )
    try:
        numbers = np.array(found, dtype="S").astype(np.float64)
    except ValueError as exc:
        raise _bad("ASCII STL has a vertex coordinate that is not a number") from exc
    return _checked_triangles(numbers.reshape(-1, 3, 3))


def _checked_triangles(triangles: np.ndarray) -> np.ndarray:
    if not np.isfinite(triangles).all():
        raise _bad("the STL file has a coordinate that is NaN or infinite")
    if float(np.abs(triangles).max()) > MAX_ABS_MM:
        raise ToolFailure(
            "input.out_of_range",
            f"the STL file has a coordinate beyond {MAX_ABS_MM:g} mm (1 km); STL stores no unit and its numbers are read as "
            "millimetres, so check that the file is not in another unit or in metres-times-a-large-factor",
            {"maximum_abs_mm": MAX_ABS_MM},
        )
    return triangles


def mesh_of_triangles(triangles: np.ndarray) -> brep.Mesh:
    """A ``Mesh`` holding each triangle with its own three vertices (not welded)."""
    count = len(triangles)
    return brep.Mesh(
        vertices=np.asarray(triangles, dtype=np.float64).reshape(-1, 3),
        triangles=np.arange(3 * count, dtype=np.int32).reshape(-1, 3),
        deflection_mm=0.0,
    )


# --------------------------------------------------------------------------- GLB (pure; reads our own output back)

GLB_MAGIC = 0x46546C67
GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942
COMPONENT_DTYPE = {5125: "<u4", 5126: "<f4"}


@dataclass(frozen=True)
class GlbMesh:
    vertices_mm: np.ndarray  # (n, 3) model coordinates, millimetres, +Z up
    triangles: np.ndarray  # (m, 3)
    accessor_min_m: list[float]  # the POSITION accessor's min and max as stored (glTF axes, metres)
    accessor_max_m: list[float]


def parse_glb(data: bytes) -> GlbMesh:
    """Read a GLB with one triangle mesh (as :func:`brep.write_mesh` writes it) back into millimetres and +Z up.

    glTF is metres and +Y up; the model is millimetres and +Z up.  A glTF position (x, y, z) is the model point
    (1000 x, -1000 z, 1000 y): the inverse of the (x, y, z) -> (x, z, -y) / 1000 that the writer applies.
    """
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or length != len(data):
        raise ToolFailure("geometry.write_failed", "the GLB just written does not read back as a GLB 2.0 of its stated length")
    offset = 12
    document: dict[str, Any] | None = None
    blob = b""
    while offset < length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        body = data[offset + 8 : offset + 8 + chunk_length]
        if chunk_type == GLB_JSON:
            document = jsonsafe.loads(body.decode("utf-8"))
        elif chunk_type == GLB_BIN:
            blob = body
        offset += 8 + chunk_length
    if document is None:
        raise ToolFailure("geometry.write_failed", "the GLB just written has no JSON chunk")

    def accessor_array(index: int, components: int) -> np.ndarray:
        accessor = document["accessors"][index]
        view = document["bufferViews"][accessor["bufferView"]]
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        dtype = np.dtype(COMPONENT_DTYPE[accessor["componentType"]])
        return np.frombuffer(blob, dtype=dtype, count=accessor["count"] * components, offset=start).astype(np.float64)

    primitive = document["meshes"][0]["primitives"][0]
    indices = accessor_array(primitive["indices"], 1).astype(np.int64)
    positions = accessor_array(primitive["attributes"]["POSITION"], 3).reshape(-1, 3)
    position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
    return GlbMesh(_to_model(positions), indices.reshape(-1, 3), list(position_accessor.get("min", [])), list(position_accessor.get("max", [])))


def _to_model(gltf_metres: np.ndarray) -> np.ndarray:
    return np.stack([gltf_metres[:, 0], -gltf_metres[:, 2], gltf_metres[:, 1]], axis=1) * 1000.0


# --------------------------------------------------------------------------- STEP length unit (pure)

_SI_PREFIX = {
    "": 1.0,
    "MILLI": 1e-3,
    "CENTI": 1e-2,
    "DECI": 1e-1,
    "KILO": 1e3,
    "MICRO": 1e-6,
}
_STEP_UNIT_NAMES = {"": "metre", "MILLI": "millimetre", "CENTI": "centimetre", "DECI": "decimetre", "KILO": "kilometre", "MICRO": "micrometre"}


def step_length_unit(text: str) -> str | None:
    """The length unit a STEP file declares (``'millimetre'``, ``'inch'`` ...), or None when it cannot be told.

    Looks at the ``LENGTH_UNIT`` entity of the file's geometric context: either an ``SI_UNIT`` with an optional prefix, or a
    ``CONVERSION_BASED_UNIT('INCH', ...)``.  A best-effort read of the file text; OpenCASCADE does the real conversion
    when it reads the file.
    """
    for statement in text.split(";"):
        if "LENGTH_UNIT" not in statement or "SOLID_ANGLE" in statement or "PLANE_ANGLE" in statement:
            continue
        based = re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", statement)
        if based:
            return based.group(1).strip().lower() or None
        si = re.search(r"SI_UNIT\s*\(\s*(?:\.([A-Z]+)\.|\$)\s*,\s*\.METRE\.\s*\)", statement)
        if si:
            return _STEP_UNIT_NAMES.get(si.group(1) or "")
    return None


# --------------------------------------------------------------------------- parameters


def parse_format(raw: str) -> str:
    fmt = raw.strip().lower()
    if fmt not in FORMATS:
        raise _bad(f"output_format: {raw!r} is not one of {list(FORMATS)}", param="output_format", allowed=list(FORMATS))
    return fmt


def parse_deflection(raw: str, *, range_family: str = "input.out_of_range") -> float:
    try:
        quantity = parse_quantity(raw, name="mesh_deflection", dimension="length")
        mm = float(quantity.to("millimeter").magnitude)
    except ArithmeticError:
        raise out_of_range("mesh_deflection") from None
    low, high = DEFLECTION_MM
    if not math.isfinite(mm) or not low <= mm <= high:
        shown = f"{mm:g} mm" if math.isfinite(mm) else f"{raw.strip()!r}, which overflows when converted to millimetres,"
        raise ToolFailure(
            range_family if math.isfinite(mm) else "input.out_of_range",
            f"mesh_deflection: {shown} is outside {low:g} mm to {high:g} mm",
            {"param": "mesh_deflection", "limits_mm": [low, high]},
        )
    return mm


def output_name(raw: str, input_path: str) -> str:
    if raw.strip():
        if not NAME.fullmatch(raw):
            raise _bad(f"name: {raw!r} must be 1 to 64 ASCII letters, digits, '_' or '-'", param="name", pattern=NAME.pattern)
        return raw
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(input_path).stem)[:64]
    return stem or "converted"


# --------------------------------------------------------------------------- the conversion


def _q(value: float, unit: str) -> QuantityOut:
    number = float(f"{value:.12g}")
    if not math.isfinite(number):
        raise ToolFailure("input.out_of_range", "a computed value is not finite; the model is far outside any physical size", {"unit": unit})
    return QuantityOut(quantity=number, unit=unit)


def _finite_all(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _bounds_check(props: brep.MassProperties) -> None:
    box = [*props.bbox_min_mm, *props.bbox_max_mm]
    if not _finite_all(*box, props.area_mm2) or max(abs(c) for c in box) > MAX_ABS_MM + 1e-6:
        raise ToolFailure(
            "input.out_of_range",
            f"the model extends beyond {MAX_ABS_MM:g} mm (1 km) from the origin or its size is not a finite number; check the "
            "length unit of the file",
            {"maximum_abs_mm": MAX_ABS_MM},
        )
    if props.volume_mm3 is not None and not math.isfinite(props.volume_mm3):
        raise ToolFailure("input.out_of_range", "the model volume is not a finite number; check the length unit of the file", {})


def _diagonal(props: brep.MassProperties) -> float:
    return math.dist(props.bbox_min_mm, props.bbox_max_mm)


def convert(
    job: JobHandle, *, input_path: str, output_format: str, mesh_deflection: str, name: str
) -> Computed:
    fmt = parse_format(output_format)
    deflection_mm = parse_deflection(mesh_deflection)
    stem = output_name(name, input_path)
    suffix = Path(input_path).suffix.lower()
    is_mesh_input = suffix == ".stl"
    if is_mesh_input and fmt not in MESH_FORMATS:
        raise ToolFailure(
            "geometry.needs_brep",
            f"an STL file is a triangle mesh, not a solid: it has no exact surfaces to write as {fmt.upper()}. "
            f"STL converts to {[f.upper() for f in MESH_FORMATS]} only; for a STEP or BREP, rebuild the part or export it from the CAD system",
            {"param": "output_format", "input_format": "stl", "allowed": list(MESH_FORMATS)},
        )

    source = job.input_file(input_path, param="input_path", extensions=INPUT_EXTENSIONS, max_bytes=MAX_INPUT_BYTES)
    original = job.input_files[-1]
    warnings: list[str] = []
    losses: list[dict[str, str]] = []
    data: dict[str, Any] = {"input_format": suffix.lstrip("."), "output_format": fmt, "input_sha256": original.sha256}

    solid: brep.Solid | None = None
    if is_mesh_input:
        triangles_in = parse_stl(source.read_bytes())
        mesh = mesh_of_triangles(triangles_in)
        welded = brep.weld(mesh)
        topology = brep.mesh_topology(welded)
        volume_in, area_in = mesh_volume_area(welded.vertices, welded.triangles)
        data["input"] = {
            "kind": "mesh",
            "triangles": int(len(triangles_in)),
            "closed": bool(topology.edge_manifold_closed),
            "watertight": bool(topology.watertight),
        }
        if topology.edge_manifold_closed:
            if volume_in <= 0.0:
                raise _bad(
                    "the STL is closed but its triangles wind clockwise seen from outside (negative volume): it is inside out. "
                    "Fix the orientation in the CAD or mesh tool, then convert",
                    "input.invalid",
                    signed_volume_mm3=volume_in,
                )
            exact_volume: float | None = volume_in
        else:
            exact_volume = None
            warnings.append(
                "The STL is not a closed, consistently oriented surface (some edge is not shared by exactly two triangles, "
                "one in each direction), so it has no volume: surface_area is the primary value and no volume is reported"
            )
        bbox_abs = float(np.abs(welded.vertices).max())
        losses.append(
            {
                "kind": "input_units",
                "detail": "STL stores no length unit; its numbers are read as millimetres, which is the toolkit's convention. If the file is in another unit, the output is wrong by that factor",
            }
        )
        losses.append(
            {
                "kind": "mesh_deflection_ignored",
                "detail": "the input is already triangles, so mesh_deflection has no effect; the triangles are copied, not re-meshed",
            }
        )
        area_exact = area_in
        size_diagonal = float(np.linalg.norm(welded.vertices.max(axis=0) - welded.vertices.min(axis=0)))
    else:
        solid = brep.read_solid(source, param="input_path")
        brep.validate_bounds(solid, param="/input_path")
        if solid.faces == 0:
            raise _bad(
                f"input_path: no faces could be read from {original.path!r} ({solid.roots_in_file} root entities in the file, "
                f"{solid.roots_transferred} transferred; OpenCASCADE drops a face whose geometry it cannot convert, as it does for absurd coordinates)",
                path=original.path,
            )
        closed = brep.closure(solid)
        # a solid with an open shell, or loose faces beside the solids: the whole file is converted, but OpenCASCADE's volume
        # integral of it means nothing (an open box "has" 80000 mm3, a loose face adds its share), so no volume is claimed
        not_closed = bool(closed.solids) and not closed.closed
        props = brep.mass_properties(solid, volume="none" if not_closed else "shape")
        _bounds_check(props)
        exact_volume = props.volume_mm3
        area_in = props.area_mm2
        area_exact = area_in
        size_diagonal = _diagonal(props)
        bbox_abs = max(abs(c) for c in [*props.bbox_min_mm, *props.bbox_max_mm])
        data["input"] = {
            "kind": "b-rep",
            "bbox_kind": "conservative, including topology tolerances and spline control hulls",
            "integration_relative_tolerance": 1e-8,
            "solids": solid.solids,
            "faces": solid.faces,
            "step_roots": {"in_file": solid.roots_in_file, "transferred": solid.roots_transferred},
            "closure": closed.summary(MAX_LOOSE_FACES_LISTED),
        }
        if not_closed:
            what = " and ".join(closed.problems())
            loose = [f"F{i}" for i in closed.loose_face_ids[:20]] + (["..."] if len(closed.loose_face_ids) > 20 else [])
            warnings.append(
                f"The file is not made of closed solids only: {what}"
                + (f" ({', '.join(loose)}, numbered as geometry_measure numbers faces)" if loose else "")
                + "; it is converted as it is, but no volume is claimed: volume_in, volume_out and volume_relative_change are not "
                "reported and surface_area_out is the primary value"
            )
            losses.append(
                {
                    "kind": "no_volume",
                    "detail": f"the input is not closed solids only ({what}); the output holds the same faces, and no volume was "
                    "computed or verified for either file (the output is read back and its area reported, nothing more)",
                }
            )
        elif exact_volume is None:
            warnings.append(
                f"The file holds no closed solid with positive volume (solids found: {solid.solids}); it is converted as "
                "surfaces, so no volume is reported and surface_area is the primary value. "
                + ("A mesh of an open shell is not a closed body." if fmt in MESH_FORMATS else "")
            )
        if solid.solids > 1:
            warnings.append(
                f"The file holds {solid.solids} solids: they are written together as one shape; volume and area are sums over all of them, so bodies that overlap are counted twice"
            )
        if solid.roots_transferred < solid.roots_in_file:
            warnings.append(
                f"Only {solid.roots_transferred} of {solid.roots_in_file} root entities of the file could be transferred: the converted file holds only what was read"
            )
        declared = None
        if suffix in (".step", ".stp"):
            declared = step_length_unit(source.read_bytes().decode("latin-1"))
            data["input"]["declared_length_unit"] = declared
            if declared is None:
                losses.append({"kind": "units", "detail": "the length unit the STEP file declares could not be determined from its text; OpenCASCADE read it and converted to millimetres"})
            elif declared != "millimetre":
                losses.append(
                    {
                        "kind": "units",
                        "detail": f"the STEP file declares its length unit as {declared}; OpenCASCADE converted every length to millimetres when reading, "
                        f"so the numbers in the output are in mm (the output {'file declares millimetres' if fmt == 'step' else 'is millimetres'}), not in {declared}",
                    }
                )
        elif suffix == ".brep":
            losses.append({"kind": "units", "detail": "a BREP file carries no unit; its numbers were read as millimetres"})
        else:
            losses.append({"kind": "units", "detail": "an IGES file's unit is converted to millimetres by OpenCASCADE when reading; the output is millimetres"})
        losses.append(
            {
                "kind": "names_colours_structure",
                "detail": "product and part names, colours, layers, PMI (tolerances, notes) and the assembly tree are not read, so they are not in the output: "
                "the result is one flat shape (STEP: one product named after 'name')",
            }
        )
        losses.append({"kind": "face_ids", "detail": "face, edge and hole numbers of geometry_measure belong to the input file's bytes; measure the output for its own"})

    values: dict[str, QuantityOut] = {}
    if exact_volume is not None:
        values["volume_in"] = _q(exact_volume, MM3)
    values["surface_area_in"] = _q(area_in, MM2)

    if fmt in MESH_FORMATS:
        out_mesh = _mesh_output(
            job,
            fmt=fmt,
            stem=stem,
            solid=solid,
            mesh=mesh if is_mesh_input else None,
            deflection_mm=deflection_mm,
            size_diagonal=size_diagonal,
            exact_volume=exact_volume,
            area_exact=area_exact,
            bbox_abs=bbox_abs,
            losses=losses,
            warnings=warnings,
            data=data,
        )
        artifact, volume_out, area_out, triangle_count = out_mesh
        values["triangle_count"] = _q(float(triangle_count), DIMENSIONLESS)
        out_has_volume = data["output"]["closed"]
    else:
        assert solid is not None
        artifact, volume_out, area_out = _brep_output(job, solid, fmt=fmt, stem=stem, exact_volume=exact_volume, data=data, warnings=warnings)
        out_has_volume = exact_volume is not None

    values["surface_area_out"] = _q(area_out, MM2)
    if out_has_volume and exact_volume is not None:
        change = (volume_out - exact_volume) / exact_volume
        values["volume_out"] = _q(volume_out, MM3)
        values["volume_relative_change"] = _q(change, DIMENSIONLESS)
        primary = "volume_out"
        data["output"]["volume_relative_change"] = float(f"{change:.12g}")
    else:
        primary = "surface_area_out"
    data["losses"] = losses
    data["output"]["file"] = Path(artifact.path).name
    data["output"]["sha256"] = artifact.sha256
    return Computed(values=values, primary=primary, warnings=warnings, data=data, artifacts=[artifact])


def _brep_output(
    job: JobHandle, solid: brep.Solid, *, fmt: str, stem: str, exact_volume: float | None, data: dict[str, Any], warnings: list[str]
) -> tuple[Any, float, float]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="eng-toolkit-") as scratch:
        target = Path(scratch) / f"out.{fmt}"
        if fmt == "step":
            brep.write_step(solid, target, product_name=stem)
        else:
            brep.write_brep(solid, target)
        payload = target.read_bytes()
    artifact = job.write_bytes(f"{stem}.{fmt}", payload, fmt=fmt)
    read_back = brep.read_solid(Path(artifact.path), param="output", caller_argument=False)  # the file itself, as the next reader will see it
    read_props = brep.mass_properties(read_back)
    volume_out = read_props.volume_mm3 if read_props.volume_mm3 is not None else 0.0
    if exact_volume is not None:
        difference = abs(volume_out - exact_volume) / exact_volume
        if read_back.solids != solid.solids or difference > READBACK_TOLERANCE:
            raise ToolFailure(
                "geometry.readback_mismatch",
                f"the {fmt.upper()} file read back as {read_back.solids} solids with volume {volume_out:.9g} mm^3 instead of "
                f"{solid.solids} solids with {exact_volume:.9g} mm^3",
                {"volume_in_mm3": exact_volume, "volume_read_mm3": volume_out, "solids_read": read_back.solids},
            )
    elif read_back.solids != solid.solids or read_back.faces != solid.faces:
        warnings.append(
            f"The {fmt.upper()} file reads back as {read_back.solids} solid(s) and {read_back.faces} faces (the input: {solid.solids} and "
            f"{solid.faces})"
            + (": OpenCASCADE writes a solid whose shell is open as a shell in STEP" if fmt == "step" and read_back.solids < solid.solids else "")
            + "; no volume was compared, since the input has none"
        )
    data["output"] = {
        "kind": "b-rep",
        "solids": read_back.solids,
        "faces": read_back.faces,
        "closed": exact_volume is not None,
        "schema": "AP214 (geometry only)" if fmt == "step" else "OpenCASCADE BREP text",
    }
    return artifact, volume_out, read_props.area_mm2


def _mesh_output(
    job: JobHandle,
    *,
    fmt: str,
    stem: str,
    solid: brep.Solid | None,
    mesh: brep.Mesh | None,
    deflection_mm: float,
    size_diagonal: float,
    exact_volume: float | None,
    area_exact: float,
    bbox_abs: float,
    losses: list[dict[str, str]],
    warnings: list[str],
    data: dict[str, Any],
) -> tuple[Any, float, float, int]:
    import tempfile

    from_solid = mesh is None
    if from_solid:
        assert solid is not None
        if size_diagonal / deflection_mm > MAX_SIZE_PER_DEFLECTION:
            raise ToolFailure(
                "input.out_of_range",
                f"mesh_deflection {deflection_mm:g} mm is too fine for a model {size_diagonal:g} mm across (ratio over {MAX_SIZE_PER_DEFLECTION:g}): "
                "the mesh would take too long and be too large. Use a coarser deflection",
                {"param": "mesh_deflection", "size_mm": size_diagonal, "maximum_ratio": MAX_SIZE_PER_DEFLECTION},
            )
        expected = brep.estimate_triangles(solid, deflection_mm).total
        if expected > MAX_TESSELLATION_TRIANGLES:  # refused before OpenCASCADE allocates the mesh
            raise ToolFailure(
                "input.out_of_range",
                f"mesh_deflection: about {expected:,} triangles are expected at {deflection_mm:g} mm (estimated before meshing); the limit "
                f"for tessellating a B-rep is {MAX_TESSELLATION_TRIANGLES:,} (about 4 GB of meshing memory). Use a coarser mesh_deflection",
                {"param": "mesh_deflection", "triangles": expected, "estimated": True, "limit": MAX_TESSELLATION_TRIANGLES},
            )
        mesh = brep.triangulate(solid, deflection_mm)
        if len(mesh.triangles) > MAX_TESSELLATION_TRIANGLES:
            raise ToolFailure(
                "input.too_large",
                f"the mesh has {len(mesh.triangles)} triangles; the limit for tessellating a B-rep is {MAX_TESSELLATION_TRIANGLES}. Use a coarser mesh_deflection",
                {"param": "mesh_deflection", "triangles": int(len(mesh.triangles)), "limit": MAX_TESSELLATION_TRIANGLES},
            )
        if len(mesh.triangles) == 0:
            raise ToolFailure("geometry.empty_mesh", "the model produced no triangles (no face could be tessellated)", {})
    assert mesh is not None
    if len(mesh.triangles) > MAX_TRIANGLES:
        raise ToolFailure(
            "input.too_large",
            f"the mesh has {len(mesh.triangles)} triangles; the limit is {MAX_TRIANGLES}. Use a coarser mesh_deflection",
            {"triangles": int(len(mesh.triangles)), "limit": MAX_TRIANGLES},
        )
    triangles_before = int(len(mesh.triangles))
    with tempfile.TemporaryDirectory(prefix="eng-toolkit-") as scratch:
        target = Path(scratch) / f"out.{fmt}"
        brep.write_mesh(mesh, target, fmt=fmt, name=stem)  # type: ignore[arg-type]
        payload = target.read_bytes()
    artifact = job.write_bytes(f"{stem}.{fmt}", payload, fmt=fmt)

    glb_info: dict[str, Any] | None = None
    if fmt == "stl":
        written = parse_stl(payload)
        vertices_out = written.reshape(-1, 3)
        triangles_out = np.arange(len(vertices_out)).reshape(-1, 3)
    else:
        glb = parse_glb(payload)
        vertices_out, triangles_out = glb.vertices_mm, glb.triangles
        glb_info = {
            "unit": "metre",
            "up_axis": "+Y",
            "conversion": "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m",
            "accessor_min_m": glb.accessor_min_m,
            "accessor_max_m": glb.accessor_max_m,
        }
    triangle_count = int(len(triangles_out))
    volume_out, area_out = mesh_volume_area(vertices_out, triangles_out)
    welded_out = brep.weld(brep.Mesh(vertices_out, np.asarray(triangles_out, dtype=np.int32), 0.0))
    topology = brep.mesh_topology(welded_out)
    closed = bool(topology.edge_manifold_closed) and volume_out > 0.0
    float32_bound = bbox_abs * FLOAT32_RELATIVE
    if not _finite_all(volume_out, area_out, float32_bound):
        raise ToolFailure("input.out_of_range", "the mesh volume or area is not a finite number; check the length unit of the file", {})

    mesh_data: dict[str, Any] = {
        "source": "tessellation of the exact surfaces" if from_solid else "the input triangles, copied",
        "triangles_before_weld": triangles_before,
        "triangle_count": triangle_count,
        "watertight": bool(topology.watertight),
        "float32_coordinate_error_bound_mm": float(f"{float32_bound:.6g}"),
    }
    if from_solid:
        mesh_data["deflection_mm"] = deflection_mm
        mesh_data["angular_deflection_rad"] = 0.5
        mesh_data["volume_change_bound_mm3"] = float(f"{area_exact * deflection_mm:.12g}")
    if glb_info:
        mesh_data["glb"] = glb_info
    data["output"] = {"kind": "mesh", "closed": closed, **mesh_data}

    if from_solid:
        losses.append(
            {
                "kind": "exact_surfaces_to_triangles",
                "detail": f"planes, cylinders, cones, spheres and free-form surfaces become flat triangles; the requested chord error (largest distance between a "
                f"triangle and the surface it replaces) is {deflection_mm:g} mm, with an angle limit of 0.5 rad between neighbouring facets. Edges, "
                "radii and exact positions can no longer be recovered; convex curved faces lose volume, concave ones (holes) gain it",
            }
        )
        if exact_volume is not None:
            bound = area_exact * deflection_mm
            change = abs(volume_out - exact_volume)
            if change > bound * (1.0 + 1e-6) + 1e-6 * exact_volume:
                warnings.append(
                    f"The mesh volume differs from the exact volume by {change:.6g} mm^3, more than the {bound:.6g} mm^3 that a chord error "
                    f"of {deflection_mm:g} mm over the surface area allows: the tessellation did not reach the requested deflection. Use a finer mesh_deflection"
                )
        losses.append({"kind": "face_structure", "detail": "the faces are merged into one triangle soup (STL) or one welded mesh (GLB): face and edge identity, and hole features, are gone"})
    losses.append(
        {
            "kind": "coordinate_precision",
            "detail": f"mesh coordinates are stored as 32-bit floats: each is rounded by up to {float32_bound:.3g} mm at this model's size (relative 6e-8)",
        }
    )
    if fmt == "stl":
        losses.append(
            {
                "kind": "stl_format",
                "detail": "binary STL, millimetres by convention (the file carries no unit), +Z as in the model. Facet normals are recomputed from the vertex order; "
                "there are no names, colours or materials, and the per-triangle attribute bytes are zero",
            }
        )
    else:
        losses.append(
            {
                "kind": "glb_axes_and_unit",
                "detail": "GLB is metres and +Y up: every vertex is converted from millimetres with +Z up by (x, y, z) -> (x, z, -y) / 1000. A point 0.1 m along +Y "
                "in the file is 100 mm along +Z in the model; a 20 mm edge along Z reads 0.02 in the file. The conversion is applied, "
                "so a viewer shows the part upright and in true size",
            }
        )
        losses.append(
            {
                "kind": "glb_content",
                "detail": "one mesh in one node named after 'name', vertices welded (shared), indexed triangles, positions only: no normals, colours, materials, textures or hierarchy",
            }
        )
    if not from_solid and triangle_count != triangles_before:
        dropped = triangles_before - triangle_count
        losses.append({"kind": "degenerate_triangles", "detail": f"{dropped} triangle(s) with repeated vertices after welding were dropped"})
    elif from_solid and fmt == "glb" and triangle_count != triangles_before:
        losses.append({"kind": "degenerate_triangles", "detail": f"{triangles_before - triangle_count} triangle(s) with repeated vertices after welding were dropped"})
    if not closed:
        warnings.append(
            "The mesh written is not a closed, consistently oriented surface, so it encloses no volume; volume_out and volume_relative_change are not reported"
        )
    return artifact, volume_out, area_out, triangle_count
