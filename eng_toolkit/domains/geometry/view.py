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

from eng_toolkit import jsonsafe
import json
import math
import re
import struct
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from eng_toolkit import __version__, brep
from eng_toolkit import preview as pic
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.geometry import convert as conversion
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle

COLOR_MODES: tuple[str, ...] = ("none", "face_kind", "dfm", "highlight")
PREVIEW_MODES: tuple[str, ...] = ("png", "iso", "none")  # png: isometric + front, top, right; iso: the isometric view only
PREVIEW_SECONDS = 60.0  # wall-clock budget of the picture alone; past it the PNG is dropped (with a warning) and the GLB is kept
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
FACE_TOKEN = re.compile(r"[Ff]([0-9]{1,9})(?:-[Ff]?([0-9]{1,9}))?")  # F7 or a range F14-F19 / F14-19
GROUP_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_ -]{0,31}")
RESERVED_GROUPS = ("neutral", "edges")
MAX_HIGHLIGHT_GROUPS = 8
# one colour per named group, in order; the first is the colour of a plain (unnamed) highlight; none is the neutral grey
GROUP_COLOURS = ("#E6007E", "#E69F00", "#0072B2", "#009E73", "#F0E442", "#D55E00", "#56B4E9", "#7B3FA0")
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


def _read_ids(tokens: list[str], param: str = "/highlight") -> set[int]:
    """Face ids from tokens ``F3`` or ``F14-F19`` / ``F14-19`` (a range is expanded; at most MAX_FACES faces in all)."""
    ids: set[int] = set()
    for token in tokens:
        match = FACE_TOKEN.fullmatch(token)
        if not match or int(match.group(1)) < 1:
            shown = token if len(token) <= 32 else token[:32] + "..."
            raise _failure("input.invalid", param, f"{shown!r} is not a face id; use F<n> with n >= 1, e.g. 'F3,F7', or a range 'F14-F19'")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if last < first:
            raise _failure("input.invalid", param, f"{token!r} is a range that ends before it starts")
        if last - first + 1 > MAX_FACES:
            raise _failure("input.too_large", param, f"{token!r} names {last - first + 1} faces; the limit is {MAX_FACES}", limit=MAX_FACES)
        ids.update(range(first, last + 1))
        if len(ids) > MAX_FACES:
            raise _failure("input.too_large", param, f"more than {MAX_FACES} face ids; the limit is {MAX_FACES}", limit=MAX_FACES)
    return ids


def parse_highlight(raw: str) -> list[int]:
    """Face ids from ``'F3, F7'`` (commas, semicolons and/or blanks between ids; ``F14-F19`` is a range), unique and ascending."""
    if len(raw) > MAX_TEXT_CHARS:
        raise _failure("input.invalid", "/highlight", f"{len(raw)} characters; at most {MAX_TEXT_CHARS}", limit=MAX_TEXT_CHARS)
    tokens = [t for t in re.split(r"[\s,;]+", raw.strip()) if t]
    if not tokens:
        raise _failure(
            "input.missing",
            "/highlight",
            "color_by 'highlight' needs face ids such as 'F3,F7' (from geometry_measure), or named groups such as 'bends: F5,F12; holes: F3,F4'",
        )
    return sorted(_read_ids(tokens))


def parse_highlight_groups(raw: str) -> list[tuple[str, list[int]]]:
    """The groups of a ``highlight`` text, in the order written, each with its ascending face ids.

    Without a colon the text is one group named ``highlight`` (``'F3,F7'``).  With a colon every group is ``name: ids``, groups
    are separated by ``;``, ``|`` or a line break, a name is 1 to 32 letters, digits, ``_``, ``-`` or blanks, and each group
    gets its own colour (:data:`GROUP_COLOURS`, at most :data:`MAX_HIGHLIGHT_GROUPS` groups).  A face may be in one group only.
    ``'bends: F5,F12; holes: F3-F4; ribs: F14-F19'`` is three groups."""
    if len(raw) > MAX_TEXT_CHARS:
        raise _failure("input.invalid", "/highlight", f"{len(raw)} characters; at most {MAX_TEXT_CHARS}", limit=MAX_TEXT_CHARS)
    if ":" not in raw:
        return [("highlight", parse_highlight(raw))]
    groups: list[tuple[str, list[int]]] = []
    owner: dict[int, str] = {}
    names: set[str] = set()
    for part in (p for p in re.split(r"[;|\n]+", raw) if p.strip()):
        name, colon, body = part.partition(":")
        name = name.strip()
        shown = name if len(name) <= 32 else name[:32] + "..."
        if not colon or not GROUP_NAME.fullmatch(name):
            raise _failure(
                "input.invalid",
                "/highlight",
                f"{shown!r} is not a group name: in a grouped highlight every group is 'name: F1,F2' (name of 1 to 32 letters, digits, '_', '-' "
                "or blanks), groups separated by ';'",
            )
        if name.lower() in RESERVED_GROUPS or name.lower() in names:
            why = "is reserved (the legend uses it)" if name.lower() in RESERVED_GROUPS else "is used twice"
            raise _failure("input.invalid", "/highlight", f"the group name {shown!r} {why}")
        names.add(name.lower())
        tokens = [t for t in re.split(r"[\s,]+", body.strip()) if t]
        if not tokens:
            raise _failure("input.missing", "/highlight", f"the group {shown!r} lists no face ids")
        ids = sorted(_read_ids(tokens))
        for face_id in ids:
            if face_id in owner:
                raise _failure("input.invalid", "/highlight", f"F{face_id} is in both {owner[face_id]!r} and {shown!r}; a face has one colour")
            owner[face_id] = name
        groups.append((name, ids))
    if not groups:
        raise _failure("input.missing", "/highlight", "no group in the text; write 'bends: F5,F12; holes: F3,F4'")
    if len(groups) > MAX_HIGHLIGHT_GROUPS:
        raise _failure("input.too_large", "/highlight", f"{len(groups)} groups; at most {MAX_HIGHLIGHT_GROUPS} (one colour each)", limit=MAX_HIGHLIGHT_GROUPS)
    return groups


def parse_preview(raw: str) -> str:
    mode = raw.strip().lower() if isinstance(raw, str) else ""
    if not isinstance(raw, str) or len(raw) > MAX_WORD_CHARS or mode not in PREVIEW_MODES:
        shown = raw if isinstance(raw, str) and len(raw) <= MAX_WORD_CHARS else "(too long or not text)"
        raise _failure("input.invalid", "/preview", f"{shown!r} is not one of {list(PREVIEW_MODES)}", allowed=list(PREVIEW_MODES))
    return mode


FINDING_TOKEN = re.compile(r"[Dd]([0-9]{1,6})")
FOCUS_PANEL_MAX_LABELS = 12  # faces whose id is written in the close-up
FOCUS_LABEL_CANDIDATES = 40  # points of a face tried (nearest the middle of the frame first) until one is seen
MAX_MARKERS = 30  # rings drawn at finding points; the first MARKER_LABELS also carry the finding id
MARKER_LABELS = 12
MARKER_LABELS_PER_RULE = 3
MARKER_RGB = (20, 20, 24)


@dataclass(frozen=True)
class FocusRequest:
    faces: tuple[int, ...]  # face ids named directly or through a highlight group
    groups: tuple[str, ...]
    findings: tuple[int, ...]  # D<n> numbers


def parse_focus(raw: str, groups: list[tuple[str, list[int]]], mode: str) -> FocusRequest | None:
    """``focus``: face ids ``F3,F7`` (ranges ``F14-F19``), names of highlight groups, and, with color_by 'dfm', finding ids
    ``D2``.  Empty text is no focus.  A name that is none of these is refused at ``/focus`` with the allowed ones."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    if len(raw) > MAX_TEXT_CHARS:
        raise _failure("input.invalid", "/focus", f"{len(raw)} characters; at most {MAX_TEXT_CHARS}", limit=MAX_TEXT_CHARS)
    named = {name.lower(): (name, ids) for name, ids in groups if not (len(groups) == 1 and name == "highlight")}
    faces: set[int] = set()
    group_names: list[str] = []
    findings: list[int] = []
    tokens: list[str] = []
    for part in (p.strip() for p in re.split(r"[,;]+", raw)):
        if not part:
            continue
        if part.lower() in named:  # a group name may hold blanks, so it is matched before the text is cut at blanks
            name, ids = named[part.lower()]
            group_names.append(name)
            faces.update(ids)
            continue
        tokens.extend(t for t in part.split() if t)
    face_tokens: list[str] = []
    for token in tokens:
        if token.lower() in named:
            name, ids = named[token.lower()]
            group_names.append(name)
            faces.update(ids)
        elif FACE_TOKEN.fullmatch(token):
            face_tokens.append(token)
        elif FINDING_TOKEN.fullmatch(token):
            if mode != "dfm":
                raise _failure("input.invalid", "/focus", f"{token!r} is a finding id; finding ids need color_by 'dfm'")
            findings.append(int(token[1:]))
        else:
            shown = token if len(token) <= 32 else token[:32] + "..."
            raise _failure(
                "input.invalid",
                "/focus",
                f"{shown!r} is not a face id (F3, F14-F19), a highlight group name or, with color_by 'dfm', a finding id (D2)",
                groups=[name for name, _ in named.values()],
            )
    if face_tokens:
        faces.update(_read_ids(face_tokens, "/focus"))
    if not (faces or findings):
        raise _failure("input.missing", "/focus", "no face id, group name or finding id in the text; write e.g. 'F3,F7'")
    return FocusRequest(tuple(sorted(faces)), tuple(dict.fromkeys(group_names)), tuple(dict.fromkeys(findings)))


def palette_for(mode: str, groups: list[tuple[str, list[int]]]) -> tuple[tuple[str, str, str], ...]:
    """The legend of a colouring: ``PALETTES[mode]``, except that named highlight groups get one colour each (then the neutral grey)."""
    if mode != "highlight" or (len(groups) == 1 and groups[0][0] == "highlight"):
        return PALETTES[mode]
    named = tuple((name, GROUP_COLOURS[i], f"faces listed in the highlight group {name!r}") for i, (name, _) in enumerate(groups))
    return named + ((NEUTRAL[0], NEUTRAL[1], "a face in no highlight group"),)


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
    document = jsonsafe.loads(data[20 : 20 + json_length].decode("utf-8"))
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


def _groups(
    mode: str, inv: brep.Inventory, highlight: list[tuple[str, list[int]]], findings: list[dfm.Finding]
) -> tuple[list[str], dict[int, list[str]]]:
    """The colour group of every face (index = id - 1) and the finding ids (D<n>) that name each face."""
    count = len(inv.faces)
    named: dict[int, list[str]] = {}
    if mode == "none":
        return [NEUTRAL[0]] * count, named
    if mode == "face_kind":
        known = {g for g, _, _ in PALETTES["face_kind"]}
        return [f.kind if f.kind in known else "other" for f in inv.faces], named
    if mode == "highlight":
        wanted = {face_id: name for name, ids in highlight for face_id in ids}
        return [wanted.get(i + 1, NEUTRAL[0]) for i in range(count)], named
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
    preview: str = "png",
    focus: str = "",
) -> Computed:
    if not MAX_SECONDS[0] <= max_seconds <= MAX_SECONDS[1]:
        raise _failure("input.out_of_range", "/max_seconds", f"must be between {MAX_SECONDS[0]} and {MAX_SECONDS[1]}, got {max_seconds}")
    if len(model_path) > MAX_PATH_CHARS:
        raise _failure("input.invalid", "/model_path", f"{len(model_path)} characters; at most {MAX_PATH_CHARS}", limit=MAX_PATH_CHARS)
    mode = parse_color_by(color_by)
    process_name = parse_word(process, "/process")
    if process_name not in dfm.PROCESSES:
        raise dfm.unsupported_process(process)
    axis_defaulted = not tool_axis.strip()
    axis_name = parse_word(tool_axis, "/tool_axis") or dfm.DEFAULT_TOOL_AXIS
    if axis_name not in dfm.AXES:
        raise _failure("input.invalid", "/tool_axis", f"{tool_axis!r} is not one of {list(dfm.AXES)}", allowed=list(dfm.AXES))
    limit_mm = dfm.parse_limits(process_name, limits)  # the validator of geometry_check_dfm: /limits or /limits/<key>
    parse_word(mesh_deflection, "/mesh_deflection")
    deflection_mm = conversion.parse_deflection(mesh_deflection)
    parse_word(name, "/name")
    stem = conversion.output_name(name, model_path)
    preview_mode = parse_preview(preview)
    highlight_groups: list[tuple[str, list[int]]] = []
    if mode == "highlight":
        highlight_groups = parse_highlight_groups(highlight)
    elif highlight.strip():
        raise _failure("input.invalid", "/highlight", "face ids are used only with color_by 'highlight'; leave highlight empty for this color_by")
    highlight_ids = sorted({face_id for _, ids in highlight_groups for face_id in ids})
    focus_request = parse_focus(focus, highlight_groups, mode)
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
        if axis_defaulted and process_name == "cnc_milling":
            warnings.append(dfm.TOOL_AXIS_DEFAULTED_WARNING)
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

    groups, named = _groups(mode, inv, highlight_groups, findings)
    normals = vertex_normals(fm.vertices, fm.triangles, fm.vertex_ranges, fm.triangle_ranges)
    palette = palette_for(mode, highlight_groups)
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

    focus_data: dict[str, Any] | None = None
    focus_spec: pic.Focus | None = None
    if focus_request is not None:
        focus_spec, focus_data = _focus(focus_request, fm, inv, findings, groups, highlight_groups)
        if preview_mode == "none":
            warnings.append("focus draws a close-up in the PNG; preview is 'none', so no picture was made")
    markers = _finding_markers(findings) if mode == "dfm" else []
    flagged = _flagged_area(findings, inv, groups, named) if mode == "dfm" else None
    if flagged is not None:
        for row in finding_rows:
            row.update(flagged["by_finding"].get(row["id"], {}))

    picture = None
    if preview_mode != "none":
        picture = _preview(
            job,
            mode=mode,
            views=("iso", "front", "top", "right") if preview_mode == "png" else ("iso",),
            stem=stem,
            source_name=original.path,
            fm=fm,
            normals=normals,
            groups=groups,
            palette=palette,
            face_counts=face_counts,
            show_edges=show_edges,
            findings=findings,
            deflection_mm=deflection_mm,
            deadline=deadline,
            markers=markers,
            focus=focus_spec,
        )
        warnings.extend(picture.warnings)
        if picture.data.get("focus") is not None and focus_data is not None:
            focus_data.update(picture.data["focus"])
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
            "preview": None if picture is None or picture.artifact is None else Path(picture.artifact.path).name,
            "legend": legend,
            "focus": focus_data,
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
        "highlight_groups": [
            {
                "name": name,
                "color": colour_of[name],
                "faces": len(ids),
                "face_ids": [f"F{i}" for i in ids[:MAX_LISTED]],
            }
            for name, ids in highlight_groups
        ],
        "supported_preview": list(PREVIEW_MODES),
        "preview": None if picture is None else picture.data,
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
    data["focus"] = focus_data
    if mode == "dfm":
        data["process"] = process_name
        data["tool_axis"] = axis_name if process_name == "cnc_milling" else None
        data["tool_axis_defaulted"] = bool(axis_defaulted and process_name == "cnc_milling")
        data["flagged_area_share"] = flagged["by_group"] if flagged else []
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
    artifacts = [glb_artifact, json_artifact]
    if picture is not None and picture.artifact is not None:
        artifacts.insert(1, picture.artifact)
    return Computed(values=values, primary="triangle_count", warnings=warnings, data=data, artifacts=artifacts)


@dataclass(frozen=True)
class Picture:
    artifact: Any  # the PNG artifact, or None when it could not be drawn
    data: dict[str, Any]
    warnings: list[str]


COLOUR_TEXT = {
    "none": "no colour coding",
    "face_kind": "colour = face kind",
    "dfm": "colour = DFM finding (rules of thumb)",
    "highlight": "colour = highlighted faces",
}
PREVIEW_LABELS = {
    "plane": "plane (flat face)",
    "cylinder": "cylinder",
    "cone": "cone",
    "sphere": "sphere",
    "torus": "torus",
    "bspline": "B-spline surface",
    "other": "other surface",
    "dfm_error": "DFM error flagged",
    "dfm_warn": "DFM warning flagged",
    "dfm_info": "DFM info flagged",
}


def _legend_label(mode: str, group: str) -> str:
    if group == NEUTRAL[0]:
        return {"none": "model (no colour coding)", "dfm": "no DFM finding on the face", "highlight": "not highlighted"}.get(mode, "neutral")
    if mode == "highlight" and group == "highlight":
        return "highlighted faces"
    return PREVIEW_LABELS.get(group, group)


def _hex_rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def _length_text(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 1000 else f"{value:.4g}" if abs(value) >= 1 else f"{value:.3f}"


def _preview(
    job: JobHandle,
    *,
    mode: str,
    views: tuple[str, ...],
    stem: str,
    source_name: str,
    fm: brep.FaceMesh,
    normals: np.ndarray,
    groups: list[str],
    palette: tuple[tuple[str, str, str], ...],
    face_counts: Counter,
    show_edges: bool,
    findings: list[dfm.Finding],
    deflection_mm: float,
    deadline,
    markers: list[FindingMarker] | None = None,
    focus: pic.Focus | None = None,
) -> Picture:
    """The PNG next to the GLB: the same triangles, colours and edges (see :mod:`eng_toolkit.preview`).  A failure to draw it
    is a warning, never a failure of the tool: the GLB is complete without it."""
    index = {group: k for k, (group, _, _) in enumerate(palette)}
    face_group = np.array([index[g] for g in groups], dtype=np.int64)
    triangles_per_face = [n for _, n in fm.triangle_ranges]
    tri_face = np.repeat(np.arange(len(groups)), triangles_per_face)
    table = np.array([_hex_rgb(colour) for _, colour, _ in palette], dtype=np.float64) / 255.0
    lines: tuple[pic.LineSet, ...] = ()
    drawn = [e for e in fm.edges if len(e.points_mm) > 1]
    if show_edges and drawn:
        segments = np.concatenate([np.stack([e.points_mm[:-1], e.points_mm[1:]], axis=1) for e in drawn])
        lines = (pic.LineSet(segments, tuple(c / 255.0 for c in _hex_rgb(EDGE_COLOR)), 1.0),)
    mesh = pic.Mesh(
        vertices=fm.vertices,
        triangles=fm.triangles,
        normals=normals,
        face_rgb=table[face_group][tri_face],
        tri_group=face_group[tri_face],
        lines=lines,
        markers=tuple(
            pic.Marker(m.point, m.label, MARKER_RGB, draw_hidden=True)
            for m in markers or []
        ),
    )
    low, high = fm.vertices.min(axis=0), fm.vertices.max(axis=0)
    size = [float(v) for v in high - low]
    legend = [
        pic.LegendItem(_legend_label(mode, group), _hex_rgb(colour), f"{face_counts[group]} face{'s' if face_counts[group] != 1 else ''}")
        for group, colour, _ in palette
        if face_counts.get(group, 0) > 0
    ]
    if lines:
        legend.append(pic.LegendItem("B-rep edges", _hex_rgb(EDGE_COLOR), "", kind="line"))
    info = [
        f"Size X {_length_text(size[0])} x Y {_length_text(size[1])} x Z {_length_text(size[2])} mm",
        f"{len(fm.triangles):,} triangles, chord error {deflection_mm:g} mm; {len(groups)} faces",
    ]
    if mode == "dfm":
        count = Counter(f.severity for f in findings)
        info.append(f"DFM findings: {count.get('error', 0)} error, {count.get('warn', 0)} warn, {count.get('info', 0)} info")
    notes = ["Axes: X red, Y green, Z blue; Z is up."]
    if markers:
        shown = sum(1 for m in markers if m.label)
        notes.append(
            f"Rings mark the point of each error or warning finding (D<n>: {shown} labelled, {len(markers)} drawn): a face can be "
            "coloured for one small local feature there. A thin ring is a point inside the material."
        )
    if focus is not None:
        notes.append(f"{focus.caption}: the faces named in focus, framed with a {int(pic.CROP_MARGIN * 100)} % margin, same colours, scale bar in mm.")
    if mode == "dfm":
        notes.append("A coloured face was flagged by a rule of thumb of this toolkit, not a verdict; a grey face is not a checked face.")
    elif mode == "face_kind":
        notes.append("Colour shows the kind of surface (how OpenCASCADE classifies it), not a finding.")
    notes.append("Same colours as the GLB; shading only shows the form. Unreviewed.")
    spec = pic.SheetSpec(
        title=stem,
        subtitle=(f"{Path(source_name).name}   -   {COLOUR_TEXT[mode]}",),
        legend=tuple(legend),
        legend_heading="Colours",
        info=tuple(info),
        notes=tuple(notes),
        views=views,
        back_view_for_markers=bool(markers),
        focus=focus,
    )
    started = time.monotonic()

    def check(phase: str) -> None:
        deadline(phase)
        if time.monotonic() - started > PREVIEW_SECONDS:
            raise TimeoutError(f"the picture took more than {PREVIEW_SECONDS:g} s")

    try:
        result = pic.render_sheet(mesh, spec, check=check)
    except ToolFailure as exc:
        if exc.family != "geometry.time_budget_exceeded":
            raise
        return Picture(None, {"file": None, "reason": "time budget"}, [f"The PNG preview was not drawn: {exc.message}. The GLB is complete"])
    except (TimeoutError, MemoryError, ArithmeticError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:200]
        return Picture(None, {"file": None, "reason": reason}, [f"The PNG preview was not drawn ({reason}). The GLB is complete"])
    artifact = job.write_bytes(f"{stem}.png", result.png, fmt="png")
    warnings: list[str] = []
    visible = max(result.visible_pixels, 1)
    group_rows = []
    for k, (group, colour, _) in enumerate(palette):
        faces = face_counts.get(group, 0)
        if faces == 0:
            continue
        seen = result.group_pixels.get(k, 0)
        group_rows.append({"group": group, "color": colour, "faces": faces, "share_of_model_in_isometric_view": float(f"{seen / visible:.4g}")})
        if result.group_pixels_all.get(k, 0) == 0:
            warnings.append(f"Colour group {group!r} ({faces} faces) is not visible in {'any view' if len(result.views) > 1 else 'the view'} of the PNG (it faces away or lies inside): see the GLB")
    return Picture(
        artifact,
        {
            "file": Path(artifact.path).name,
            "sha256": artifact.sha256,
            "format": "PNG, 8-bit RGB",
            "width": result.width,
            "height": result.height,
            "views": list(result.views),
            "camera": "orthographic; isometric from (+x, -y, +z) with Z up; front from -y, top from +z, right from +x",
            "supersampling": result.supersampling,
            "colours": "the GLB's sRGB colours times a Lambert factor of a fixed light (0.55 to 1.0); legend swatches are the exact colours",
            "edges_drawn": bool(lines),
            "markers": [
                {"finding": m.id, "rule": m.rule, "labelled": bool(m.label), "view": None if px is None else px[0],
                 "x": None if px is None else round(px[1], 1), "y": None if px is None else round(px[2], 1),
                 "inside_material": None if px is None else px[3], "point_mm": [float(f"{v:.6g}") for v in m.point]}
                for m, px in zip(markers or [], result.markers_px)
            ],
            "focus": result.focus,
            "model_size_mm": {"x": float(f"{size[0]:.6g}"), "y": float(f"{size[1]:.6g}"), "z": float(f"{size[2]:.6g}")},
            "groups": group_rows,
            "rule": "drawn from the same tessellation as the GLB; a face colour means 'flagged' or 'of this kind', not a verdict",
        },
        warnings,
    )


@dataclass(frozen=True)
class FindingMarker:
    id: str  # D<n>
    rule: str
    point: np.ndarray
    label: str  # written next to the ring, or "" when the rule already has MARKER_LABELS_PER_RULE labelled rings


def _finding_markers(findings: list[dfm.Finding]) -> list[FindingMarker]:
    """A ring for each error and warning finding that has a point (errors first, at most MAX_MARKERS).  The first
    MARKER_LABELS_PER_RULE rings of a rule, and at most MARKER_LABELS in all, carry the finding id: 22 corner radii must not
    hide the 3 ligaments."""
    rows: list[FindingMarker] = []
    per_rule: Counter = Counter()
    labelled = 0
    for n, f in enumerate(findings, start=1):
        if f.severity not in ("error", "warn") or f.location_mm is None or len(rows) >= MAX_MARKERS:
            continue
        label = ""
        if per_rule[f.rule] < MARKER_LABELS_PER_RULE and labelled < MARKER_LABELS:
            label, labelled = f"D{n}", labelled + 1
        per_rule[f.rule] += 1
        rows.append(FindingMarker(f"D{n}", f.rule, np.array(f.location_mm, dtype=np.float64), label))
    return rows


def _face_triangles(fm: brep.FaceMesh, face_id: int) -> tuple[int, int]:
    return fm.triangle_ranges[face_id - 1]


def _focus(
    request: FocusRequest,
    fm: brep.FaceMesh,
    inv: brep.Inventory,
    findings: list[dfm.Finding],
    groups: list[str],
    highlight_groups: list[tuple[str, list[int]]],
) -> tuple[pic.Focus, dict[str, Any]]:
    """The close-up of the faces ``request`` names: the box of their triangles, their triangle indices, and a label (F<n>)
    at a point of each of the largest ones, the point nearest the middle of the box.  A finding id (D<n>) frames the
    finding's smaller faces and its point: the larger one (the floor above a ligament) would frame the whole model."""
    count = len(inv.faces)
    faces: set[int] = set(request.faces)
    anchors: list[np.ndarray] = []
    for number in request.findings:
        if not 1 <= number <= len(findings):
            raise _failure(
                "input.invalid", "/focus", f"D{number} is not a finding: this check has {len(findings)} (D1 to D{len(findings)})",
                findings=len(findings),
            )
        finding = findings[number - 1]
        named = [i for i in dfm.all_face_ids(finding) if 1 <= i <= count]
        faces.update(named)
        if finding.location_mm is not None:
            anchors.append(np.array(finding.location_mm, dtype=np.float64))
    unknown = [f"F{i}" for i in sorted(faces) if i > count]
    if unknown:
        raise ToolFailure(
            "geometry.unknown_entity",
            f"focus: {', '.join(unknown[:20])}{' ...' if len(unknown) > 20 else ''}: the model has no such face (it has {count} faces, "
            f"F1 to F{count}; ids as in geometry_measure)",
            {"param": "/focus", "unknown": unknown[:100], "faces": count},
        )
    framed = set(faces)
    if request.findings and not request.faces and not request.groups:  # only findings: their smaller faces
        areas = {i: inv.faces[i - 1].area_mm2 for i in faces}
        biggest = max(areas.values(), default=0.0)
        small = {i for i, a in areas.items() if a <= 0.25 * biggest}
        framed = small or set(faces)
    pieces = []
    for i in sorted(framed):
        v0, vn = fm.vertex_ranges[i - 1]
        if vn:
            pieces.append(fm.vertices[v0 : v0 + vn])
    if anchors:
        pieces.append(np.array(anchors))
    if not pieces:
        raise ToolFailure(
            "geometry.empty_mesh", "focus: none of the named faces produced triangles, so there is nothing to frame", {"param": "/focus"}
        )
    cloud = np.concatenate(pieces)
    box = np.stack([cloud.min(axis=0), cloud.max(axis=0)])
    triangles = np.concatenate([np.arange(t0, t0 + tn) for t0, tn in (fm.triangle_ranges[i - 1] for i in sorted(faces)) if tn] or [np.empty(0, dtype=np.int64)])
    middle = box.mean(axis=0)
    ranked = sorted(faces, key=lambda i: (-inv.faces[i - 1].area_mm2, i))
    labels: list[pic.FocusLabel] = []
    for i in ranked:
        if len(labels) >= FOCUS_PANEL_MAX_LABELS:
            break
        t0, tn = fm.triangle_ranges[i - 1]
        if tn == 0:
            continue
        corners = fm.vertices[fm.triangles[t0 : t0 + tn]]  # (tn, 3, 3)
        centroids = corners.mean(axis=1)
        nearest = np.argsort(np.linalg.norm(centroids - middle, axis=1), kind="stable")[:FOCUS_LABEL_CANDIDATES]
        labels.append(pic.FocusLabel(centroids[nearest], f"F{i}"))
    caption = "CLOSE-UP " + ("of " + ", ".join(f"F{i}" for i in sorted(faces)[:4]) + (" ..." if len(faces) > 4 else ""))
    spec = pic.Focus(box_mm=box, triangles=triangles, caption=caption, labels=tuple(labels))
    data = {
        "faces": [f"F{i}" for i in sorted(faces)[:MAX_LISTED]],
        "faces_total": len(faces),
        "groups": list(request.groups),
        "findings": [f"D{n}" for n in request.findings],
        "colour_groups": sorted({groups[i - 1] for i in faces}),
        "margin": pic.CROP_MARGIN,
        "labels_requested": [lab.text for lab in labels],
    }
    return spec, data


# Rules whose finding concerns its faces as a whole (a hole, a bend, a face no tool reaches); the others concern a place on
# them: a pair of faces the finding measures between (the smaller face's area is an upper bound of the overlap), an edge,
# or a point.
WHOLE_FACE_RULES = (
    "cnc.hole_depth_ratio", "cnc.min_hole_diameter", "cnc.tool_access", "sheet.thickness", "sheet.thickness_uniform",
    "sheet.bend_unrecognised", "sheet.bend_tolerance",
)


def _footprint(f: dfm.Finding, face_areas: dict[int, float]) -> tuple[str, dict[int, float] | None]:
    """How much of each face of the finding the finding concerns: ``(kind, {face id: area in mm2})`` or ``(kind, None)``
    when it concerns a place with no area: an edge or a point.  Kinds: ``whole_faces``, ``area_bound`` (the smaller face of
    a pair of faces with material between them: an upper bound of the overlap), ``edge``, ``point``."""
    named = [i for i in dfm.all_face_ids(f) if i in face_areas]
    bound = f.extra.get("face_area_mm2") if isinstance(f.extra, dict) else None
    if isinstance(bound, (int, float)) and not isinstance(bound, bool) and bound > 0:
        return "area_bound", {i: min(float(bound), face_areas[i]) for i in named}
    if f.edge_ids:
        return "edge", None
    if f.rule in WHOLE_FACE_RULES or f.rule == "sheet.min_bend_radius":
        return "whole_faces", {i: face_areas[i] for i in named}
    return "point", None


def _flagged_area(
    findings: list[dfm.Finding], inv: brep.Inventory, groups: list[str], named: dict[int, list[str]]
) -> dict[str, Any]:
    """Which share of a coloured face the findings that colour it concern, so that a floor coloured for the 0.94 mm
    ligament under three holes is not read as a floor that is wrong everywhere.  The share is an upper bound: finding
    footprints of one face add up (at most the face), and a finding at an edge or a point has no area."""
    face_areas = {face.id: face.area_mm2 for face in inv.faces}
    by_finding: dict[str, dict[str, Any]] = {}
    flagged: dict[int, float] = {}
    local: dict[int, list[tuple[str, str]]] = {}  # face -> (D<n>, "rule measured") of the findings with no area
    ids_of: dict[int, list[str]] = {}
    for n, f in enumerate(findings, start=1):
        kind, footprint = _footprint(f, face_areas)
        row: dict[str, Any] = {"footprint": kind, "flagged_area_mm2": None, "flagged_area_share": {}}
        if footprint is not None:
            row["flagged_area_mm2"] = float(f"{sum(footprint.values()):.6g}")
            row["flagged_area_share"] = {
                f"F{i}": float(f"{(a / face_areas[i]) if face_areas[i] > 0 else 0.0:.4g}") for i, a in sorted(footprint.items())
            }
            for i, a in footprint.items():
                flagged[i] = min(face_areas[i], flagged.get(i, 0.0) + a)
        else:
            what = f.rule + (f" {f.measured:.4g} {f.measured_unit if f.measured_unit != 'ratio' else ''}".rstrip() if f.measured is not None else "")
            for i in dfm.all_face_ids(f):
                if i in face_areas:
                    local.setdefault(i, []).append((f"D{n}", what))
        for i in dfm.all_face_ids(f):
            if i in face_areas:
                ids_of.setdefault(i, []).append(f"D{n}")
        by_finding[f"D{n}"] = row
    by_group: list[dict[str, Any]] = []
    for group in ("dfm_error", "dfm_warn", "dfm_info"):
        members = [i for i, g in enumerate(groups, start=1) if g == group]
        if not members:
            continue
        total = math.fsum(face_areas[i] for i in members)
        upper = math.fsum(min(flagged.get(i, 0.0), face_areas[i]) for i in members)
        explanations = []
        for i in members:
            share = flagged.get(i, 0.0) / face_areas[i] if face_areas[i] > 0 else 0.0
            which = ", ".join(named.get(i, ids_of.get(i, [])))
            if i in flagged and share < 0.5:
                explanations.append(
                    f"F{i} is coloured because of {which}: at most {flagged[i]:.3g} mm2 of its {face_areas[i]:.4g} mm2 "
                    f"({100.0 * share:.2g} %) is concerned, at the marked points"
                )
            elif i not in flagged and i in local:
                kinds = sorted({what for _, what in local[i]})
                explanations.append(
                    f"F{i} is coloured because of {', '.join(d for d, _ in local[i])} ({'; '.join(kinds)}), found at "
                    f"{len(local[i])} point{'s' if len(local[i]) != 1 else ''} or edge{'s' if len(local[i]) != 1 else ''} on it, "
                    "not over the whole face (area not measured): see the rings"
                )
        by_group.append(
            {
                "group": group,
                "faces": len(members),
                "area_mm2": float(f"{total:.6g}"),
                "flagged_area_mm2_upper_bound": float(f"{upper:.6g}"),
                "flagged_area_share": float(f"{(upper / total) if total > 0 else 0.0:.4g}"),
                "faces_local_only": [f"F{i}" for i in members if i not in flagged and i in local][:MAX_LISTED],
                "explanations": explanations[:MAX_LISTED],
                "basis": (
                    "upper bound: pairs of faces count the smaller face (the overlap cannot be larger), whole-face rules the whole "
                    "face, edges and points no area"
                ),
            }
        )
    return {"by_finding": by_finding, "by_group": by_group}


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
