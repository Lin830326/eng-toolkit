"""``geometry_view``: a solid 3D CAD file as a coloured GLB whose colours carry engineering meaning.

One glTF mesh with one triangle primitive per colour group (one material each), plus, optionally, one line primitive with
the B-rep edges.  Each primitive's ``extras`` lists the face ids (``F<n>``, numbered as ``geometry_measure`` numbers them)
and the triangle range of each face; the node's ``extras`` holds the legend, the unit and axes conversion, the model's
bounding box and the tool version.  Units and axes follow ``geometry_convert``: metres, +Y up, (x, y, z) mm ->
(x, z, -y) / 1000 m.

The DFM colouring calls the rules of ``geometry_check_dfm`` (:mod:`.dfm`) unchanged; nothing about the rules lives here.
Every number in the answer that describes the GLB (triangle counts, per-face areas, edge points, accessor bounds) is read
back from the bytes of the file handed to the caller.  OpenCASCADE is reached only through :mod:`eng_toolkit.brep`.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from eng_toolkit import __version__, brep
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.geometry import convert as conversion
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle

COLOR_MODES: tuple[str, ...] = ("none", "face_kind", "dfm", "highlight")
# OpenCASCADE's mesher holds about 6.6 kB per triangle while it works (a 603,802-triangle sphere took 4.1 GB), so 600,000
# triangles is about 4 GB.  The cap is checked before meshing (estimate) and after every face (count).
MAX_TRIANGLES = 600_000
MEMORY_PER_TRIANGLE_KB = 6.6
MAX_FACES = 20000  # faces of a model coloured by none, face_kind or highlight; dfm keeps its own limit (dfm.MAX_FACES)
MAX_SECONDS = (1, 600)
MAX_PATH_CHARS = 1024
MAX_TEXT_CHARS = 4096  # highlight (limits: dfm.MAX_LIMITS_CHARS, the same 4096)
MAX_WORD_CHARS = 64  # process, tool_axis, color_by
MAX_LISTED = 200  # faces per group and findings listed in data; view.json holds all of them
JSON_NAME = "view.json"
METALLIC, ROUGHNESS = 0.1, 0.8
FLOAT32, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
TRIANGLES, LINES = 4, 1
GLB_MAGIC = 0x46546C67
GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942
FACE_TOKEN = re.compile(r"[Ff]([0-9]{1,9})")
CONVERSION_TEXT = "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"

NEUTRAL = ("neutral", "#B4B8BE")
EDGE_COLOR = "#1A1A1A"
# group -> (sRGB colour, meaning), per color_by, in legend (and primitive) order
PALETTES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "none": ((NEUTRAL[0], NEUTRAL[1], "every face: no colour coding"),),
    "face_kind": (
        ("plane", "#56B4E9", "planar face"),
        ("cylinder", "#E69F00", "cylindrical face"),
        ("cone", "#009E73", "conical face"),
        ("sphere", "#CC79A7", "spherical face"),
        ("torus", "#F0E442", "toroidal face"),
        ("bspline", "#0072B2", "B-spline or Bezier surface"),
        ("other", "#7F7F7F", "any other surface (revolution, extrusion, offset)"),
    ),
    "dfm": (
        ("dfm_error", "#D62728", "worst DFM finding on the face has severity error (exact or heuristic: see each finding's check)"),
        ("dfm_warn", "#FF7F0E", "worst DFM finding on the face has severity warn (exact or heuristic: see each finding's check)"),
        ("dfm_info", "#1F77B4", "worst DFM finding on the face has severity info (exact or heuristic: see each finding's check)"),
        (NEUTRAL[0], NEUTRAL[1], "no DFM finding names the face"),
    ),
    "highlight": (
        ("highlight", "#E6007E", "a face listed in highlight"),
        (NEUTRAL[0], NEUTRAL[1], "a face not listed in highlight"),
    ),
}
EDGE_MEANING = "B-rep edges (lines), drawn from the tessellation; seams and degenerate edges are not drawn"


def _failure(family: str, param: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure(family, f"{param.lstrip('/')}: {message}", {"param": param, **details})


def srgb_to_linear(hex_colour: str) -> list[float]:
    """glTF's baseColorFactor is linear RGB: the sRGB transfer function is undone, alpha 1."""
    out = []
    for i in (1, 3, 5):
        c = int(hex_colour[i : i + 2], 16) / 255.0
        out.append(round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 6))
    return [*out, 1.0]


def pointer(details: dict[str, Any], family: str, *, default: str = "/model_path") -> str:
    """The JSON pointer a refusal names: the given param (``limits.min_wall`` -> ``/limits/min_wall``), or the input the
    failure is about (``default``: the model file).  Used by every geometry tool's refusals."""
    raw = details.get("param")
    if isinstance(raw, str) and raw.strip().startswith("/") and raw.strip("/ "):
        return raw.strip()  # already a pointer (dfm.parse_limits: /limits/<key>, keys escaped as RFC 6901 says)
    if isinstance(raw, str) and raw.strip("/ "):
        return "/" + raw.strip().lstrip("/").replace(".", "/")
    if family == "geometry.time_budget_exceeded":
        return "/max_seconds"
    return default


# --------------------------------------------------------------------------- parameters (pure)


def parse_color_by(raw: str) -> str:
    mode = raw.strip().lower()
    if len(raw) > MAX_WORD_CHARS or mode not in COLOR_MODES:
        shown = raw if len(raw) <= MAX_WORD_CHARS else raw[:MAX_WORD_CHARS] + "..."
        raise _failure("input.invalid", "/color_by", f"{shown!r} is not one of {list(COLOR_MODES)}", allowed=list(COLOR_MODES))
    return mode


def parse_highlight(raw: str) -> list[int]:
    """Face ids from ``'F3, F7'`` (commas and/or blanks between ids), unique and ascending."""
    if len(raw) > MAX_TEXT_CHARS:
        raise _failure("input.invalid", "/highlight", f"{len(raw)} characters; at most {MAX_TEXT_CHARS}", limit=MAX_TEXT_CHARS)
    tokens = [t for t in re.split(r"[\s,;]+", raw.strip()) if t]
    if not tokens:
        raise _failure("input.missing", "/highlight", "color_by 'highlight' needs face ids such as 'F3,F7' (from geometry_measure)")
    ids: set[int] = set()
    for token in tokens:
        match = FACE_TOKEN.fullmatch(token)
        if not match or int(match.group(1)) < 1:
            shown = token if len(token) <= 32 else token[:32] + "..."
            raise _failure("input.invalid", "/highlight", f"{shown!r} is not a face id; use F<n> with n >= 1, e.g. 'F3,F7'")
        ids.add(int(match.group(1)))
    return sorted(ids)


def too_many_triangles(count: int, deflection_mm: float, *, estimated: bool) -> ToolFailure:
    """The refusal for a mesh over :data:`MAX_TRIANGLES`, with a deflection that should fit (triangle counts fall at
    least as 1 / deflection on curved faces, so scaling the deflection by count / cap is on the safe side)."""
    suggested = min(conversion.DEFLECTION_MM[1], float(f"{deflection_mm * 1.2 * count / MAX_TRIANGLES:.2g}"))
    memory_gb = count * MEMORY_PER_TRIANGLE_KB / 1e6
    what = f"about {count:,} triangles are expected (estimated before meshing)" if estimated else f"the mesh reached {count:,} triangles"
    return _failure(
        "input.out_of_range" if estimated else "input.too_large",
        "/mesh_deflection",
        f"{what} at {deflection_mm:g} mm; the limit is {MAX_TRIANGLES:,} triangles (about {MAX_TRIANGLES * MEMORY_PER_TRIANGLE_KB / 1e6:.0f} GB "
        f"of meshing memory; this mesh would need about {memory_gb:.1f} GB). Use a coarser (larger) mesh_deflection, e.g. '{suggested:g} mm'",
        triangles=count,
        estimated=estimated,
        limit=MAX_TRIANGLES,
        suggested_mesh_deflection_mm=suggested,
    )


def parse_word(raw: str, param: str) -> str:
    if len(raw) > MAX_WORD_CHARS:
        raise _failure("input.invalid", param, f"{len(raw)} characters; at most {MAX_WORD_CHARS}", limit=MAX_WORD_CHARS)
    return raw.strip().lower()


# --------------------------------------------------------------------------- the GLB (pure)


@dataclass(frozen=True)
class Primitive:
    group: str
    colour: str
    face_ids: tuple[int, ...]
    positions: np.ndarray  # (n, 3) float32, glTF axes, metres
    normals: np.ndarray  # (n, 3) float32, unit
    indices: np.ndarray  # (m * 3,) uint32
    face_ranges: tuple[tuple[int, int], ...]  # (first triangle, count) per face of face_ids


@dataclass(frozen=True)
class Lines:
    edge_ids: tuple[int, ...]
    positions: np.ndarray  # (p, 3) float32
    indices: np.ndarray  # (2 s,) uint32
    point_ranges: tuple[tuple[int, int], ...]  # (first point, count) per edge


def to_gltf(points_mm: np.ndarray) -> np.ndarray:
    p = np.asarray(points_mm, dtype=np.float64).reshape(-1, 3)
    return np.stack([p[:, 0], p[:, 2], -p[:, 1]], axis=1) / 1000.0


def to_model(gltf_m: np.ndarray) -> np.ndarray:
    g = np.asarray(gltf_m, dtype=np.float64).reshape(-1, 3)
    return np.stack([g[:, 0], -g[:, 2], g[:, 1]], axis=1) * 1000.0


def vertex_normals(vertices_mm: np.ndarray, triangles: np.ndarray, vertex_ranges, triangle_ranges) -> np.ndarray:
    """Area-weighted mean of the triangle normals at each vertex, model axes.  Faces share no vertices, so the shading is
    smooth inside a face and sharp between faces.  A vertex no non-degenerate triangle reaches takes its face's mean normal
    (or +Z when the whole face is degenerate)."""
    v = np.asarray(vertices_mm, dtype=np.float64).reshape(-1, 3)
    t = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    out = np.zeros_like(v)
    if len(t):
        corners = v[t]
        weighted = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        for k in range(3):
            np.add.at(out, t[:, k], weighted)
        lengths = np.linalg.norm(out, axis=1)
        for (v0, vn), (t0, tn) in zip(vertex_ranges, triangle_ranges):
            if vn == 0:
                continue
            block = slice(v0, v0 + vn)
            weak = lengths[block] <= 1e-300
            if weak.any():
                mean = weighted[t0 : t0 + tn].sum(axis=0) if tn else np.zeros(3)
                if not np.linalg.norm(mean) > 1e-300:
                    mean = np.array([0.0, 0.0, 1.0])
                out[block] = np.where(weak[:, None], mean, out[block])
    else:
        out[:] = (0.0, 0.0, 1.0)
    out[np.linalg.norm(out, axis=1) <= 1e-300] = (0.0, 0.0, 1.0)
    return out / np.linalg.norm(out, axis=1)[:, None]


def build_primitive(group: str, colour: str, face_ids: list[int], fm: brep.FaceMesh, normals_mm: np.ndarray) -> Primitive:
    positions, normals, indices, ranges = [], [], [], []
    vertex_offset = triangle_offset = 0
    for face_id in face_ids:
        v0, vn = fm.vertex_ranges[face_id - 1]
        t0, tn = fm.triangle_ranges[face_id - 1]
        positions.append(fm.vertices[v0 : v0 + vn])
        normals.append(normals_mm[v0 : v0 + vn])
        indices.append(fm.triangles[t0 : t0 + tn] - v0 + vertex_offset)
        ranges.append((triangle_offset, tn))
        vertex_offset += vn
        triangle_offset += tn
    model_normals = np.concatenate(normals) if normals else np.empty((0, 3))
    gl_normals = np.stack([model_normals[:, 0], model_normals[:, 2], -model_normals[:, 1]], axis=1) if len(model_normals) else model_normals
    return Primitive(
        group=group,
        colour=colour,
        face_ids=tuple(face_ids),
        positions=np.ascontiguousarray(to_gltf(np.concatenate(positions) if positions else np.empty((0, 3))), dtype="<f4"),
        normals=np.ascontiguousarray(gl_normals, dtype="<f4").reshape(-1, 3),
        indices=np.ascontiguousarray(np.concatenate(indices).reshape(-1) if indices else np.empty(0), dtype="<u4"),
        face_ranges=tuple(ranges),
    )


def build_lines(edges: tuple[brep.EdgePolyline, ...]) -> Lines:
    positions, indices, ranges = [], [], []
    offset = 0
    for edge in edges:
        n = len(edge.points_mm)
        positions.append(edge.points_mm)
        k = np.arange(n - 1, dtype=np.int64) + offset
        indices.append(np.stack([k, k + 1], axis=1).reshape(-1))
        ranges.append((offset, n))
        offset += n
    return Lines(
        edge_ids=tuple(e.id for e in edges),
        positions=np.ascontiguousarray(to_gltf(np.concatenate(positions) if positions else np.empty((0, 3))), dtype="<f4"),
        indices=np.ascontiguousarray(np.concatenate(indices) if indices else np.empty(0), dtype="<u4"),
        point_ranges=tuple(ranges),
    )


def _bounds(array: np.ndarray, components: int) -> tuple[list[float], list[float]]:
    a = np.asarray(array).reshape(-1, components)
    if len(a) == 0:
        return [0.0] * components, [0.0] * components
    return [float(x) for x in a.min(axis=0)], [float(x) for x in a.max(axis=0)]


def glb_bytes(primitives: list[Primitive], lines: Lines | None, *, name: str, node_extras: dict[str, Any]) -> bytes:
    """GLB 2.0: one scene, one node (``name``, ``node_extras``), one mesh; every buffer view starts on a 4-byte boundary."""
    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []

    def add(array: np.ndarray, *, component: int, kind: str, target: int, components: int, integer: bool = False) -> int:
        data = array.tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        blob.extend(data)
        blob.extend(b"\x00" * (-len(blob) % 4))
        lo, hi = _bounds(array, components)
        if integer:
            lo, hi = [int(x) for x in lo], [int(x) for x in hi]
        accessors.append(
            {"bufferView": len(views) - 1, "componentType": component, "count": int(np.asarray(array).size // components), "type": kind, "min": lo, "max": hi}
        )
        return len(accessors) - 1

    materials: list[dict[str, Any]] = []
    gl_primitives: list[dict[str, Any]] = []
    for p in primitives:
        materials.append(
            {
                "name": p.group,
                "doubleSided": False,
                "pbrMetallicRoughness": {"baseColorFactor": srgb_to_linear(p.colour), "metallicFactor": METALLIC, "roughnessFactor": ROUGHNESS},
            }
        )
        indices = add(p.indices, component=UINT32, kind="SCALAR", target=ELEMENT_ARRAY_BUFFER, components=1, integer=True)
        position = add(p.positions, component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3)
        normal = add(p.normals, component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3)
        gl_primitives.append(
            {
                "attributes": {"POSITION": position, "NORMAL": normal},
                "indices": indices,
                "material": len(materials) - 1,
                "mode": TRIANGLES,
                "extras": {
                    "group": p.group,
                    "color": p.colour,
                    "faces": [{"id": f"F{f}", "first_triangle": r[0], "triangle_count": r[1]} for f, r in zip(p.face_ids, p.face_ranges)],
                },
            }
        )
    if lines is not None and len(lines.indices):
        materials.append(
            {
                "name": "edges",
                "doubleSided": False,
                "pbrMetallicRoughness": {"baseColorFactor": srgb_to_linear(EDGE_COLOR), "metallicFactor": METALLIC, "roughnessFactor": ROUGHNESS},
            }
        )
        indices = add(lines.indices, component=UINT32, kind="SCALAR", target=ELEMENT_ARRAY_BUFFER, components=1, integer=True)
        position = add(lines.positions, component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3)
        gl_primitives.append(
            {
                "attributes": {"POSITION": position},
                "indices": indices,
                "material": len(materials) - 1,
                "mode": LINES,
                "extras": {
                    "group": "edges",
                    "color": EDGE_COLOR,
                    "edges": [{"id": f"E{e}", "first_point": r[0], "point_count": r[1]} for e, r in zip(lines.edge_ids, lines.point_ranges)],
                },
            }
        )
    document = {
        "asset": {"generator": "eng-toolkit geometry_view", "version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name, "extras": node_extras}],
        "meshes": [{"name": name, "primitives": gl_primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    text = json.dumps(document, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = bytes(blob)
    total = 12 + 8 + len(text) + 8 + len(body)
    return struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(text), GLB_JSON) + text + struct.pack("<II", len(body), GLB_BIN) + body


@dataclass(frozen=True)
class ReadBack:
    document: dict[str, Any]
    triangles_by_group: dict[str, int]
    face_area_mm2: dict[int, float]  # from the triangles of each face, read from the file
    face_triangles: dict[int, int]
    edge_points: int
    edge_count: int
    accessor_min_m: list[float]  # over every POSITION accessor of the triangle primitives
    accessor_max_m: list[float]


def read_back(data: bytes) -> ReadBack:
    """Parse the GLB just written (as :func:`glb_bytes` writes it) and measure it from its bytes."""
    bad = lambda why: ToolFailure("geometry.write_failed", f"the GLB just written does not read back: {why}", {"param": "/model_path"})  # noqa: E731
    if len(data) < 28:
        raise bad("too short")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or length != len(data):
        raise bad("not a GLB 2.0 of its stated length")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    document = json.loads(data[20 : 20 + json_length].decode("utf-8"))
    start = 20 + json_length
    bin_length, bin_type = struct.unpack_from("<II", data, start)
    if json_type != GLB_JSON or bin_type != GLB_BIN or start + 8 + bin_length != len(data):
        raise bad("chunk layout")
    blob = data[start + 8 :]

    def array(index: int, components: int) -> np.ndarray:
        accessor = document["accessors"][index]
        view = document["bufferViews"][accessor["bufferView"]]
        dtype = "<u4" if accessor["componentType"] == UINT32 else "<f4"
        return np.frombuffer(blob, dtype=dtype, count=accessor["count"] * components, offset=view["byteOffset"]).reshape(-1, components)

    by_group: dict[str, int] = {}
    areas: dict[int, float] = {}
    face_triangles: dict[int, int] = {}
    lows, highs = [], []
    edge_points = edge_count = 0
    for primitive in document["meshes"][0]["primitives"]:
        extras = primitive["extras"]
        positions = array(primitive["attributes"]["POSITION"], 3)
        indices = array(primitive["indices"], 1).reshape(-1).astype(np.int64)
        if len(indices) and int(indices.max()) >= len(positions):
            raise bad("an index is out of range")
        if primitive["mode"] == LINES:
            edge_points += len(positions)
            edge_count += len(extras["edges"])
            continue
        triangles = indices.reshape(-1, 3)
        by_group[extras["group"]] = len(triangles)
        if len(positions):
            lows.append(positions.min(axis=0))
            highs.append(positions.max(axis=0))
        model = to_model(positions.astype(np.float64))
        corners = model[triangles]
        tri_area = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1) / 2.0
        for face in extras["faces"]:
            first, count = face["first_triangle"], face["triangle_count"]
            face_id = int(face["id"][1:])
            areas[face_id] = float(math.fsum(tri_area[first : first + count]))
            face_triangles[face_id] = count
    lo = [float(x) for x in np.min(lows, axis=0)] if lows else [0.0, 0.0, 0.0]
    hi = [float(x) for x in np.max(highs, axis=0)] if highs else [0.0, 0.0, 0.0]
    return ReadBack(document, by_group, areas, face_triangles, edge_points, edge_count, lo, hi)


# --------------------------------------------------------------------------- colouring


def _finding_entry(n: int, f: dfm.Finding, face_count: int) -> dict[str, Any]:
    def number(value: float | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {"value": float(f"{value:.12g}"), "unit": "mm" if f.measured_unit == "mm" else f.measured_unit}

    named = dfm.all_face_ids(f)
    colourable = sum(1 for i in named if 1 <= i <= face_count)
    total = faces_total(f)
    return {
        "id": f"D{n}",
        "rule": f.rule,
        "severity": f.severity,
        "check": "exact" if f.exact else "heuristic",
        "message": f.message,
        "measured": number(f.measured),
        "limit": number(f.limit),
        "face_ids": named,
        "edge_ids": list(f.edge_ids),
        "location_mm": None if f.location_mm is None else [float(v) for v in f.location_mm],
        "coloured": colourable > 0,
        "faces_named": len(named),
        "faces_total": total,
        "faces_not_coloured": total - colourable,
        "faces_listed_by_geometry_check_dfm": len(f.face_ids),
    }


def faces_total(f: dfm.Finding) -> int:
    """How many faces a finding concerns: its complete face list (``dfm.all_face_ids``, which keeps the faces
    ``geometry_check_dfm`` leaves out of its list of 50), or the count in ``extra['faces_total']`` if that is larger."""
    named = len(dfm.all_face_ids(f))
    total = f.extra.get("faces_total") if isinstance(f.extra, dict) else None
    if isinstance(total, int) and not isinstance(total, bool) and total >= named:
        return total
    return named


def _groups(mode: str, inv: brep.Inventory, highlight: list[int], findings: list[dfm.Finding]) -> tuple[list[str], dict[int, list[str]]]:
    """The colour group of every face (index = id - 1) and the finding ids (D<n>) that name each face."""
    count = len(inv.faces)
    named: dict[int, list[str]] = {}
    if mode == "none":
        return [NEUTRAL[0]] * count, named
    if mode == "face_kind":
        known = {g for g, _, _ in PALETTES["face_kind"]}
        return [f.kind if f.kind in known else "other" for f in inv.faces], named
    if mode == "highlight":
        wanted = set(highlight)
        return ["highlight" if i + 1 in wanted else NEUTRAL[0] for i in range(count)], named
    rank = {severity: i for i, severity in enumerate(dfm.SEVERITIES)}  # error first
    worst: dict[int, int] = {}
    for n, f in enumerate(findings, start=1):
        for face_id in dfm.all_face_ids(f):  # every face of the finding, also those geometry_check_dfm does not list
            if 1 <= face_id <= count:
                worst[face_id] = min(worst.get(face_id, len(rank)), rank[f.severity])
                named.setdefault(face_id, []).append(f"D{n}")
    return [f"dfm_{dfm.SEVERITIES[worst[i + 1]]}" if i + 1 in worst else NEUTRAL[0] for i in range(count)], named


def _count(value: int) -> QuantityOut:
    return QuantityOut(quantity=float(value), unit="dimensionless")


def view(
    job: JobHandle,
    *,
    model_path: str,
    color_by: str,
    highlight: str,
    process: str,
    tool_axis: str,
    limits: str,
    max_seconds: int,
    mesh_deflection: str,
    name: str,
    show_edges: bool,
    deadline,
) -> Computed:
    if not MAX_SECONDS[0] <= max_seconds <= MAX_SECONDS[1]:
        raise _failure("input.out_of_range", "/max_seconds", f"must be between {MAX_SECONDS[0]} and {MAX_SECONDS[1]}, got {max_seconds}")
    if len(model_path) > MAX_PATH_CHARS:
        raise _failure("input.invalid", "/model_path", f"{len(model_path)} characters; at most {MAX_PATH_CHARS}", limit=MAX_PATH_CHARS)
    mode = parse_color_by(color_by)
    process_name = parse_word(process, "/process")
    if process_name not in dfm.PROCESSES:
        raise dfm.unsupported_process(process)
    axis_name = parse_word(tool_axis, "/tool_axis")
    if axis_name not in dfm.AXES:
        raise _failure("input.invalid", "/tool_axis", f"{tool_axis!r} is not one of {list(dfm.AXES)}", allowed=list(dfm.AXES))
    limit_mm = dfm.parse_limits(process_name, limits)  # the validator of geometry_check_dfm: /limits or /limits/<key>
    parse_word(mesh_deflection, "/mesh_deflection")
    deflection_mm = conversion.parse_deflection(mesh_deflection)
    parse_word(name, "/name")
    stem = conversion.output_name(name, model_path)
    highlight_ids: list[int] = []
    if mode == "highlight":
        highlight_ids = parse_highlight(highlight)
    elif highlight.strip():
        raise _failure("input.invalid", "/highlight", "face ids are used only with color_by 'highlight'; leave highlight empty for this color_by")
    if Path(model_path).suffix.lower() == ".stl":
        raise _failure(
            "input.unsupported_format",
            "/model_path",
            f"'.stl' mesh files have no faces to colour; use one of {list(brep.SOLID_EXTENSIONS)}",
            supported=list(brep.SOLID_EXTENSIONS),
        )

    deadline("read")
    source = job.input_file(model_path, param="/model_path", extensions=brep.SOLID_EXTENSIONS, max_bytes=conversion.MAX_INPUT_BYTES)
    original = job.input_files[-1]
    solid = brep.read_solid(source, param="model_path")
    brep.validate_bounds(solid)
    deadline("read")
    if solid.faces == 0:
        raise _failure("input.invalid", "/model_path", f"no faces in {original.path!r}", path=original.path)
    props = brep.mass_properties(solid)
    dfm.require_closed_solids(solid, props.volume_mm3, purpose="a coloured view is made of closed solids only")
    conversion._bounds_check(props)
    face_limit = dfm.MAX_FACES if mode == "dfm" else MAX_FACES
    if solid.faces > face_limit:
        raise _failure("input.too_large", "/model_path", f"{solid.faces} faces; the limit for color_by {mode!r} is {face_limit}", faces=solid.faces, limit=face_limit)
    diagonal = math.dist(props.bbox_min_mm, props.bbox_max_mm)
    if diagonal / deflection_mm > conversion.MAX_SIZE_PER_DEFLECTION:
        raise _failure(
            "input.out_of_range",
            "/mesh_deflection",
            f"{deflection_mm:g} mm is too fine for a model {diagonal:g} mm across (ratio over {conversion.MAX_SIZE_PER_DEFLECTION:g}); "
            "use a coarser mesh_deflection",
            size_mm=diagonal,
            maximum_ratio=conversion.MAX_SIZE_PER_DEFLECTION,
        )
    deadline("estimate")
    estimate = brep.estimate_triangles(solid, deflection_mm, deadline=deadline)
    if estimate.total > MAX_TRIANGLES:
        raise too_many_triangles(estimate.total, deflection_mm, estimated=True)

    deadline("inventory")
    inv = brep.inventory(solid, deadline=deadline)
    deadline("inventory")
    unknown = [f"F{i}" for i in highlight_ids if i > len(inv.faces)]
    if unknown:
        raise ToolFailure(
            "geometry.unknown_entity",
            f"highlight: {', '.join(unknown[:20])}{' ...' if len(unknown) > 20 else ''}: the model has no such face (it has "
            f"{len(inv.faces)} faces, F1 to F{len(inv.faces)}; ids as in geometry_measure)",
            {"param": "/highlight", "unknown": unknown[:100], "faces": len(inv.faces)},
        )

    warnings: list[str] = []
    findings: list[dfm.Finding] = []
    report: dfm.Report | None = None
    if mode == "dfm":
        report = dfm.run(process_name, solid, inv, limit_mm, axis_name, deadline=deadline)  # before meshing: the same facts as geometry_check_dfm
        deadline("rules")
        rank = {severity: i for i, severity in enumerate(dfm.SEVERITIES)}
        findings = sorted(report.findings, key=lambda f: (rank[f.severity], f.rule))  # geometry_check_dfm's order: D<n> is its n-th finding
        warnings.extend(report.warnings)
        used = dfm.defaults_used(process_name, limits)
        if used:
            warnings.append(
                "Default DFM limits were used for " + ", ".join(used) + ": they are this toolkit's rules of thumb, not values from a "
                "standard; pass 'limits' with your own"
            )
    elif limits.strip() or process_name != "cnc_milling" or axis_name != "+z":
        warnings.append("process, tool_axis and limits apply to color_by 'dfm' only; they were checked and then not used")

    def size_budget(triangles_so_far: int) -> None:  # second line of defence, checked after every face
        if triangles_so_far > MAX_TRIANGLES:
            raise too_many_triangles(triangles_so_far, deflection_mm, estimated=False)

    fm = brep.tessellate_faces(solid, deflection_mm, with_edges=show_edges, deadline=deadline, after_face=size_budget)
    deadline("mesh")
    triangles_total = int(len(fm.triangles))
    size_budget(triangles_total)
    if triangles_total == 0:
        raise ToolFailure("geometry.empty_mesh", "the model produced no triangles (no face could be tessellated)", {"param": "/model_path"})
    if not np.isfinite(fm.vertices).all():
        raise _failure("input.out_of_range", "/model_path", "the tessellation has a coordinate that is not a finite number")
    untessellated = [f"F{i + 1}" for i, (_, n) in enumerate(fm.triangle_ranges) if n == 0]
    if untessellated:
        warnings.append(
            f"{len(untessellated)} face(s) produced no triangles and are not visible in the GLB: {', '.join(untessellated[:20])}"
            + (" ..." if len(untessellated) > 20 else "")
        )

    groups, named = _groups(mode, inv, highlight_ids, findings)
    normals = vertex_normals(fm.vertices, fm.triangles, fm.vertex_ranges, fm.triangle_ranges)
    palette = PALETTES[mode]
    primitives = [
        build_primitive(group, colour, [i + 1 for i, g in enumerate(groups) if g == group], fm, normals)
        for group, colour, _ in palette
        if group in groups
    ]
    empty = [p.group for p in primitives if len(p.indices) == 0]
    if empty:  # a group whose faces made no triangles gets no primitive: glTF has no use for an empty accessor
        primitives = [p for p in primitives if len(p.indices)]
        warnings.append(f"Colour group(s) {empty} have no triangles (their faces were not tessellated), so the GLB has no primitive for them")
    lines = build_lines(fm.edges) if show_edges else None
    face_counts = Counter(groups)
    legend = [
        {"group": group, "color": colour, "base_color_factor": srgb_to_linear(colour), "meaning": meaning, "faces": face_counts.get(group, 0)}
        for group, colour, meaning in palette
    ]
    if lines is not None:
        legend.append({"group": "edges", "color": EDGE_COLOR, "base_color_factor": srgb_to_linear(EDGE_COLOR), "meaning": EDGE_MEANING, "faces": 0})
    sha = original.sha256
    node_extras = {
        "tool": "geometry_view",
        "tool_version": __version__,
        "color_by": mode,
        "legend": legend,
        "units": {"model_unit": "millimetre", "unit": "metre", "up_axis": "+Y", "conversion": CONVERSION_TEXT},
        "model_bbox_mm": {"min": [float(v) for v in props.bbox_min_mm], "max": [float(v) for v in props.bbox_max_mm]},
        "model_bbox_kind": "conservative, including topology tolerances and spline control hulls",
        "integration_relative_tolerance": 1e-8,
        "face_ids": "F<n> as in geometry_measure for the same file bytes (face_ids_valid_for) and OpenCASCADE version",
        "face_ids_valid_for": sha,
        "mesh_deflection_mm": deflection_mm,
    }
    payload = glb_bytes(primitives, lines, name=stem, node_extras=node_extras)
    deadline("write")
    glb_artifact = job.write_bytes(f"{stem}.glb", payload, fmt="glb")
    back = read_back(Path(glb_artifact.path).read_bytes())

    triangle_count = int(sum(back.triangles_by_group.values()))
    expected = {p.group: len(p.indices) // 3 for p in primitives}
    if back.triangles_by_group != expected or triangle_count != triangles_total:
        raise ToolFailure(
            "geometry.readback_mismatch",
            f"the GLB read back with {triangle_count} triangles in groups {back.triangles_by_group} instead of {triangles_total} in {expected}",
            {"param": "/model_path"},
        )
    edge_points = back.edge_points
    if lines is not None and edge_points != len(lines.positions):
        raise ToolFailure("geometry.readback_mismatch", f"the GLB read back with {edge_points} edge points instead of {len(lines.positions)}", {"param": "/model_path"})
    for face_id, count in back.face_triangles.items():
        if count != fm.triangle_ranges[face_id - 1][1]:
            raise ToolFailure("geometry.readback_mismatch", f"F{face_id} read back with {count} triangles", {"param": "/model_path"})

    coloured_ids = set(named)
    finding_rows = [_finding_entry(n, f, len(inv.faces)) for n, f in enumerate(findings, start=1)]
    coloured_rows = [row for row in finding_rows if row["coloured"]]
    uncoloured_rows = [row for row in finding_rows if not row["coloured"]]
    for row in finding_rows:
        if row["faces_not_coloured"]:
            warnings.append(
                f"{row['id']} ({row['rule']}) concerns {row['faces_total']} faces; {row['faces_not_coloured']} of them could not be "
                "coloured (no such face in the model, or not named by the rule) and look neutral"
            )
    singled = sorted(coloured_ids) if mode == "dfm" else (highlight_ids if mode == "highlight" else [])
    colour_of = {group: colour for group, colour, _ in palette}
    mesh_area = float(math.fsum(back.face_area_mm2.values()))

    face_rows = [
        {
            "id": f"F{face.id}",
            "kind": face.kind,
            "group": groups[face.id - 1],
            "color": colour_of[groups[face.id - 1]],
            "finding_ids": named.get(face.id, []),
            "triangle_count": back.face_triangles.get(face.id, 0),
            "area_mm2": float(f"{face.area_mm2:.12g}"),
            "area_mesh_mm2": float(f"{back.face_area_mm2.get(face.id, 0.0):.12g}"),
        }
        for face in inv.faces
    ]
    json_artifact = job.write_json(
        JSON_NAME,
        {
            "tool": "geometry_view",
            "tool_version": __version__,
            "glb": Path(glb_artifact.path).name,
            "glb_sha256": glb_artifact.sha256,
            "face_ids_valid_for": sha,
            "color_by": mode,
            "legend": legend,
            "faces": face_rows,
            "findings": finding_rows,
            "units": node_extras["units"],
            "model_bbox_mm": node_extras["model_bbox_mm"],
            "model_bbox_kind": node_extras["model_bbox_kind"],
            "integration_relative_tolerance": 1e-8,
        },
    )

    float32_bound = max(abs(c) for c in [*props.bbox_min_mm, *props.bbox_max_mm]) * conversion.FLOAT32_RELATIVE
    losses = _losses(source, deflection_mm, float32_bound, fm, show_edges)
    if solid.solids > 1:
        warnings.append(f"The file holds {solid.solids} solids: they are written as one mesh, and a face id belongs to whichever solid holds it")
    if solid.roots_transferred < solid.roots_in_file:
        warnings.append(
            f"Only {solid.roots_transferred} of {solid.roots_in_file} root entities of the file could be transferred: the view shows only what was read"
        )
    if mode == "dfm" and uncoloured_rows:
        warnings.append(f"{len(uncoloured_rows)} DFM finding(s) name no face, so no face shows them; see data.findings_not_coloured")
    if mode == "dfm" and report is not None and not findings:
        warnings.append("geometry_check_dfm reports no finding for this part with these limits: every face is neutral")

    groups_data = [
        {
            "group": p.group,
            "color": p.colour,
            "faces": len(p.face_ids),
            "triangles": back.triangles_by_group[p.group],
            "face_ids": [f"F{f}" for f in p.face_ids[:MAX_LISTED]],
            "face_ids_listed": min(len(p.face_ids), MAX_LISTED),
        }
        for p in primitives
    ]
    if any(len(p.face_ids) > MAX_LISTED for p in primitives):
        warnings.append(f"Groups with more than {MAX_LISTED} faces list the first {MAX_LISTED} in data; {JSON_NAME} lists every face")
    data: dict[str, Any] = {
        "color_by": mode,
        "supported_color_by": list(COLOR_MODES),
        "legend": legend,
        "groups": groups_data,
        "face_count": len(inv.faces),
        "highlighted_faces": [f"F{i}" for i in singled[:MAX_LISTED]],
        "findings_total": len(findings),
        "findings_coloured": coloured_rows[:MAX_LISTED],
        "findings_not_coloured": uncoloured_rows[:MAX_LISTED],
        "findings_partly_coloured": [
            {key: row[key] for key in ("id", "rule", "severity", "faces_named", "faces_total", "faces_not_coloured")}
            for row in finding_rows
            if row["faces_not_coloured"]
        ][:MAX_LISTED],
        "edges": None
        if lines is None
        else {
            "drawn": back.edge_count,
            "points": edge_points,
            "seams_not_drawn": fm.seam_edges,
            "degenerate_not_drawn": fm.degenerate_edges,
            "without_polygon": fm.edges_without_polygon,
        },
        "input": {
            "format": source.suffix.lower().lstrip("."),
            "solids": solid.solids,
            "faces": solid.faces,
            "step_roots": {"in_file": solid.roots_in_file, "transferred": solid.roots_transferred},
        },
        "output": {
            "file": Path(glb_artifact.path).name,
            "sha256": glb_artifact.sha256,
            "json_file": JSON_NAME,
            "triangle_count": triangle_count,
            "deflection_mm": deflection_mm,
            "angular_deflection_rad": 0.5,
            "area_exact_mm2": float(f"{props.area_mm2:.12g}"),
            "area_method": "adaptive B-rep integration, successive tolerances agree within 1e-8 relative",
            "area_mesh_mm2": float(f"{mesh_area:.12g}"),
            "float32_coordinate_error_bound_mm": float(f"{float32_bound:.6g}"),
            "glb": {
                "unit": "metre",
                "up_axis": "+Y",
                "conversion": CONVERSION_TEXT,
                "accessor_min_m": back.accessor_min_m,
                "accessor_max_m": back.accessor_max_m,
                "materials": {"metallicFactor": METALLIC, "roughnessFactor": ROUGHNESS, "doubleSided": False},
            },
        },
        "losses": losses,
        "face_ids_valid_for": sha,
    }
    if mode == "dfm":
        data["process"] = process_name
        data["tool_axis"] = axis_name if process_name == "cnc_milling" else None
        data["limits_mm_or_ratio"] = {k: float(f"{v:.12g}") for k, v in limit_mm.items()}
        data["checked"] = report.checked if report else []
        data["not_performed"] = report.not_performed if report else []
    if len(findings) > MAX_LISTED:
        warnings.append(f"{len(findings)} findings; data lists at most {MAX_LISTED} of each kind, {JSON_NAME} lists all of them")
    values = {
        "triangle_count": _count(triangle_count),
        "face_count": _count(len(inv.faces)),
        "highlighted_face_count": _count(len(singled)),
        "finding_count": _count(len(findings)),
    }
    deadline("write")
    return Computed(values=values, primary="triangle_count", warnings=warnings, data=data, artifacts=[glb_artifact, json_artifact])


def _losses(source: Path, deflection_mm: float, float32_bound: float, fm: brep.FaceMesh, show_edges: bool) -> list[dict[str, str]]:
    suffix = source.suffix.lower()
    losses: list[dict[str, str]] = []
    if suffix in (".step", ".stp"):
        declared = conversion.step_length_unit(source.read_bytes().decode("latin-1"))
        if declared is None:
            losses.append({"kind": "units", "detail": "the length unit the STEP file declares could not be determined from its text; OpenCASCADE read it and converted to millimetres"})
        elif declared != "millimetre":
            losses.append(
                {"kind": "units", "detail": f"the STEP file declares its length unit as {declared}; OpenCASCADE converted every length to millimetres when reading, and the GLB is metres"}
            )
    elif suffix == ".brep":
        losses.append({"kind": "units", "detail": "a BREP file carries no unit; its numbers were read as millimetres"})
    else:
        losses.append({"kind": "units", "detail": "an IGES file's unit is converted to millimetres by OpenCASCADE when reading"})
    losses += [
        {
            "kind": "names_colours_structure",
            "detail": "product and part names, the CAD system's colours and materials, layers, PMI and the assembly tree are not read: the GLB holds one "
            "flat mesh, and its colours are this tool's coding (see the legend), not the file's",
        },
        {
            "kind": "exact_surfaces_to_triangles",
            "detail": f"every face becomes flat triangles with a chord error up to {deflection_mm:g} mm and an angle limit of 0.5 rad; radii and exact "
            "positions can no longer be recovered from the GLB. Face identity is kept: each face's triangles are listed in its primitive's extras",
        },
        {
            "kind": "normals",
            "detail": "vertex normals are the area-weighted mean of the triangle normals of one face: smooth inside a face, sharp between faces; "
            "they are not the exact surface normals",
        },
        {"kind": "coordinate_precision", "detail": f"coordinates and normals are 32-bit floats: each coordinate is rounded by up to {float32_bound:.3g} mm"},
        {
            "kind": "glb_axes_and_unit",
            "detail": "GLB is metres and +Y up: every vertex is converted from millimetres with +Z up by (x, y, z) -> (x, z, -y) / 1000, so a viewer "
            "shows the part upright and in true size; the node's extras hold the model's bounding box in millimetres",
        },
        {
            "kind": "glb_content",
            "detail": "one node, one mesh, one triangle primitive per colour group (one material each, metallic 0.1, roughness 0.8, single-sided)"
            + (", one line primitive with the edges" if show_edges else "")
            + ": no textures, animation, hierarchy, assembly or FEM fields",
        },
        {"kind": "face_ids", "detail": "face (F<n>) and edge (E<n>) ids belong to the input file's bytes and this OpenCASCADE version"},
    ]
    if show_edges:
        losses.append(
            {
                "kind": "edges",
                "detail": f"edges are the tessellation's nodes along each B-rep edge, as line segments; {fm.seam_edges} seam edge(s) (where a periodic "
                f"face closes on itself) and {fm.degenerate_edges} degenerate edge(s) (zero length, a sphere's pole) are not drawn"
                + (f"; {fm.edges_without_polygon} edge(s) had no tessellation polygon and are not drawn" if fm.edges_without_polygon else ""),
            }
        )
    return losses


__all__ = ["COLOR_MODES", "PALETTES", "glb_bytes", "pointer", "read_back", "srgb_to_linear", "view"]
