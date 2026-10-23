"""``fem_view``: the result of a ``fem_static`` job as a colour-mapped GLB, a legend SVG and ``view.json``.

What is drawn is the boundary of the second-order tetrahedral mesh (:func:`.mesh.boundary_facets`, outward), each six-node
facet as four flat sub-triangles through its midside nodes, coloured by the nodal values ``fem_static`` wrote to
``results.csv`` (CalculiX's nodal stresses: extrapolated from the integration points and averaged between elements).  Nothing
is averaged or smoothed here: a vertex carries exactly the value of its node.

GLB conventions follow ``geometry_view``: metres, +Y up, (x, y, z) mm -> (x, z, -y) / 1000 m, every buffer view on a 4-byte
boundary, every number the answer gives about the file read back from its bytes.  ``COLOR_0`` is float32 linear RGB (glTF
defines vertex colours as linear); ``_FIELD`` (float32, the value in the legend's unit) and ``_NODE`` (float32, the 1-based
node number of ``results.csv``) are application-specific attributes so that a reader can check the colours against the
numbers.  The colour maps are tables of 65 sRGB points computed here from published formulas (cubehelix, Green 2011;
Moreland's diverging map, 2009); see ``docs/fem/design.md``.
"""

from __future__ import annotations

import json
import math
import re
import struct
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import numpy as np

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.fem import mesh as meshing
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, JobHandle
from eng_toolkit.units import UnitsError, parse_quantity, ureg

# --------------------------------------------------------------------------- limits and constants

MAX_TRIANGLES = 1_000_000  # surface sub-triangles (4 per boundary facet); checked from the facet count before anything is built
MAX_SECONDS = (1, 600)
MAX_PATH_CHARS = 1024
MAX_WORD_CHARS = 64  # field, colormap, output_unit, deformation_scale
MAX_RANGE_CHARS = 128
MAX_NAME_CHARS = 64
MAX_MANIFEST_BYTES = 1_000_000
MAX_RESULT_JSON_BYTES = 16_000_000
MAX_MESH_BYTES = 16_000_000  # real fem_static meshes take 165 to 200 bytes per tetrahedron: 60000 of them are at most about 12 MB
MAX_FILE_ELEMENTS = 2 * meshing.MAX_ELEMENTS  # elements of all kinds in the file (tetrahedra plus Gmsh's surface, line and point elements)
PARSE_CHUNK = 20_000  # rows parsed between two deadline checks
DEGENERATE_RELATIVE = 1e-12  # 6 V below this times the longest edge cubed: a tetrahedron with coplanar or collinear corners
CSV_RELATIVE_ROUNDING = 5e-9  # results.csv prints coordinates with %.9g: rounding to 9 significant digits moves a value by at most 5e-9 of it
CSV_SAFETY = 4.0
MAX_CSV_BYTES = 64_000_000
MAX_DEFORMATION_SCALE = 1e4
AUTO_DEFORMATION_FRACTION = 0.05  # 'auto': the largest displacement is drawn as 5 % of the bounding-box diagonal
MAX_STRESS_MPA = 1e9  # |value| above this (a range end, or a number in results.csv) is refused as absurd
MAX_LENGTH_MM = 1e9
MAX_COORDINATE_MM = 1e7
MAX_LABEL_MAGNITUDE = 1e30  # a value converted to the output unit must stay below this (float32 holds 3.4e38)
MIN_SPAN_RELATIVE = 1e-3  # an automatic range narrower than this fraction of its largest magnitude is widened (CalculiX prints 6 digits)
FEATURE_ANGLE_DEG = 40.0  # facets meeting at a sharper angle do not share vertex normals (the edges of a box stay sharp)
TABLE_POINTS = 65
LEGEND_SAMPLES = 11
GLB_NAME, LEGEND_NAME, JSON_NAME = "fem_view.glb", "legend.svg", "view.json"
STATIC_FILES = ("mesh.msh", "model.inp", "result.frd", "result.dat", "results.csv")
FLOAT32, UINT32 = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
TRIANGLES, LINES = 4, 1
GLB_MAGIC = 0x46546C67
GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942
CONVERSION_TEXT = "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"
METALLIC, ROUGHNESS = 0.0, 0.8
MESH_EDGE_COLOUR = "#202020"
OUTLINE_COLOUR = "#7A7A7A"
SHA256 = re.compile(r"[0-9a-f]{64}")

CSV_COLUMNS = ("node", "x_mm", "y_mm", "z_mm", "ux_mm", "uy_mm", "uz_mm", "u_mag_mm", "sxx_mpa", "syy_mpa", "szz_mpa", "sxy_mpa", "syz_mpa", "szx_mpa", "von_mises_mpa")
STRESS_COLUMNS = ("sxx_mpa", "syy_mpa", "szz_mpa", "sxy_mpa", "syz_mpa", "szx_mpa")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "stress" or "length"
    columns: tuple[str, ...]
    signed: bool
    label: str


FIELDS: dict[str, Field] = {
    f.name: f
    for f in (
        Field("von_mises", "stress", ("von_mises_mpa",), False, "von Mises stress"),
        Field("displacement", "length", ("u_mag_mm",), False, "displacement magnitude |u|"),
        Field("ux", "length", ("ux_mm",), True, "displacement ux"),
        Field("uy", "length", ("uy_mm",), True, "displacement uy"),
        Field("uz", "length", ("uz_mm",), True, "displacement uz"),
        Field("s_xx", "stress", ("sxx_mpa",), True, "normal stress s_xx"),
        Field("s_yy", "stress", ("syy_mpa",), True, "normal stress s_yy"),
        Field("s_zz", "stress", ("szz_mpa",), True, "normal stress s_zz"),
        Field("s_xy", "stress", ("sxy_mpa",), True, "shear stress s_xy"),
        Field("s_yz", "stress", ("syz_mpa",), True, "shear stress s_yz"),
        Field("s_zx", "stress", ("szx_mpa",), True, "shear stress s_zx"),
        Field("principal_max", "stress", STRESS_COLUMNS, True, "maximum principal stress"),
        Field("principal_min", "stress", STRESS_COLUMNS, True, "minimum principal stress"),
    )
}
COLORMAPS = ("auto", "sequential", "diverging")
BASE_UNIT = {"stress": "megapascal", "length": "millimeter"}
DIMENSION = {"stress": "pressure", "length": "length"}
MAGNITUDE_CAP = {"stress": MAX_STRESS_MPA, "length": MAX_LENGTH_MM}


def _failure(family: str, param: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure(family, f"{param.lstrip('/')}: {message}", {**details, "param": param})


def pointer(details: dict[str, Any], family: str) -> str:
    """The JSON pointer a refusal names: the given param, or ``/result_dir`` (the input most refusals are about)."""
    raw = details.get("param")
    if isinstance(raw, str) and raw.strip("/ "):
        return "/" + raw.strip().lstrip("/").replace(".", "/")
    if family == "fem.time_budget_exceeded":
        return "/max_seconds"
    return "/result_dir"


class Deadline:
    """Cooperative wall-clock budget, checked between steps."""

    def __init__(self, max_seconds: int):
        self.max_seconds = max_seconds
        self.started = time.monotonic()

    def __call__(self, phase: str) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed >= self.max_seconds:
            raise _failure(
                "fem.time_budget_exceeded",
                "/max_seconds",
                f"time budget exceeded in {phase}: used {elapsed:.3f} s, limit {self.max_seconds} s",
                phase=phase,
                elapsed_s=round(elapsed, 3),
                max_seconds=self.max_seconds,
            )


# --------------------------------------------------------------------------- colour maps (pure)


def srgb_to_linear(c: np.ndarray | float) -> np.ndarray:
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: np.ndarray | float) -> np.ndarray:
    c = np.clip(np.asarray(c, dtype=np.float64), 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1.0 / 2.4) - 0.055)


# Cubehelix (D. A. Green, "A colour scheme for the display of astronomical intensity images", Bull. Astr. Soc. India 39, 289, 2011):
# intensity rises linearly with lambda while the hue turns, so the map reads correctly in grey scale too.
CUBEHELIX = {"start": 3.0, "rotations": -0.65, "hue": 1.3, "gamma": 1.0, "lambda_min": 0.10, "lambda_max": 0.88}


def cubehelix(lam: float, start: float, rotations: float, hue: float, gamma: float) -> tuple[float, float, float]:
    phi = 2.0 * math.pi * (start / 3.0 + rotations * lam)
    lg = lam**gamma
    amp = hue * lg * (1.0 - lg) / 2.0
    c, s = math.cos(phi), math.sin(phi)
    rgb = (lg + amp * (-0.14861 * c + 1.78277 * s), lg + amp * (-0.29227 * c - 0.90649 * s), lg + amp * (1.97294 * c))
    return tuple(min(1.0, max(0.0, v)) for v in rgb)  # type: ignore[return-value]


def _sequential_table() -> np.ndarray:
    p = CUBEHELIX
    rows = []
    for k in range(TABLE_POINTS):
        lam = p["lambda_min"] + (p["lambda_max"] - p["lambda_min"]) * k / (TABLE_POINTS - 1)
        rows.append(cubehelix(lam, p["start"], p["rotations"], p["hue"], p["gamma"]))
    return np.round(np.array(rows), 6)


# Moreland's diverging map (K. Moreland, "Diverging Color Maps for Scientific Visualization", ISVC 2009): interpolation in the
# polar form (M, s, h) of CIELAB between the endpoints of his 'cool to warm' map, through a neutral point of M = 88.
DIVERGING_ENDS = ((59, 76, 192), (180, 4, 38))
_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
_WHITE = np.array([0.95047, 1.0, 1.08883])  # D65


def _lab(rgb: tuple[float, float, float]) -> np.ndarray:
    xyz = _XYZ @ srgb_to_linear(np.array(rgb)) / _WHITE
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def _rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[0] + 16) / 116
    f = np.array([fy + lab[1] / 500, fy, fy - lab[2] / 200])
    xyz = np.where(f > 6 / 29, f**3, 3 * (6 / 29) ** 2 * (f - 4 / 29)) * _WHITE
    return linear_to_srgb(np.linalg.solve(_XYZ, xyz))


def _msh(lab: np.ndarray) -> tuple[float, float, float]:
    m = float(np.linalg.norm(lab))
    return m, math.acos(lab[0] / m), math.atan2(lab[2], lab[1])


def _adjust_hue(m: float, s: float, h: float, m_unsat: float) -> float:
    if m >= m_unsat:
        return h
    spin = s * math.sqrt(m_unsat**2 - m**2) / (m * math.sin(s))
    return h + spin if h > -math.pi / 3 else h - spin


def moreland(t: float) -> np.ndarray:
    (m1, s1, h1), (m2, s2, h2) = (_msh(_lab(tuple(c / 255 for c in end))) for end in DIVERGING_ENDS)
    if s1 > 0.05 and s2 > 0.05 and abs(h1 - h2) > math.pi / 3:
        mid = max(m1, m2, 88.0)
        if t < 0.5:
            m2, s2, h2, t = mid, 0.0, 0.0, 2 * t
        else:
            m1, s1, h1, t = mid, 0.0, 0.0, 2 * t - 1
    if s1 < 0.05 and s2 > 0.05:
        h1 = _adjust_hue(m2, s2, h2, m1)
    elif s2 < 0.05 and s1 > 0.05:
        h2 = _adjust_hue(m1, s1, h1, m2)
    m, s, h = ((1 - t) * a + t * b for a, b in ((m1, m2), (s1, s2), (h1, h2)))
    return _rgb(np.array([m * math.cos(s), m * math.sin(s) * math.cos(h), m * math.sin(s) * math.sin(h)]))


def _diverging_table() -> np.ndarray:
    return np.round(np.clip(np.array([moreland(k / (TABLE_POINTS - 1)) for k in range(TABLE_POINTS)]), 0.0, 1.0), 6)


TABLES: dict[str, np.ndarray] = {"sequential": _sequential_table(), "diverging": _diverging_table()}
COLORMAP_SOURCES = {
    "sequential": "cubehelix (D. A. Green 2011, Bull. Astr. Soc. India 39, 289), start 3.0, rotations -0.65, hue 1.3, gamma 1, "
    "lambda 0.10 to 0.88, sampled at 65 points: dark blue -> teal -> green -> pale yellow, intensity rising linearly",
    "diverging": "Moreland's diverging map (K. Moreland 2009, 'Diverging Color Maps for Scientific Visualization'), endpoints sRGB "
    "(59, 76, 192) and (180, 4, 38) interpolated in Msh (polar CIELAB, D65) through a neutral grey of M = 88, sampled at 65 points",
}


def colours_srgb(t: np.ndarray, table: np.ndarray) -> np.ndarray:
    """sRGB colour of the normalised positions ``t`` in [0, 1]: linear interpolation between the table's points."""
    x = np.clip(np.asarray(t, dtype=np.float64), 0.0, 1.0) * (len(table) - 1)
    i = np.minimum(np.floor(x).astype(np.int64), len(table) - 2)
    frac = (x - i)[:, None]
    return table[i] * (1.0 - frac) + table[i + 1] * frac


def hex_colour(rgb) -> str:
    return "#" + "".join(f"{int(round(float(c) * 255)):02X}" for c in rgb)


# --------------------------------------------------------------------------- parameters (pure)


def _text(raw: Any, param: str, limit: int) -> str:
    if not isinstance(raw, str):
        raise _failure("input.invalid", param, f"must be text, got {type(raw).__name__}")
    if len(raw) > limit:
        raise _failure("input.invalid", param, f"{len(raw)} characters; at most {limit}", limit=limit)
    return raw


def _shown(raw: str, limit: int = 32) -> str:
    return repr(raw if len(raw) <= limit else raw[:limit] + "...")


def parse_field(raw: str) -> Field:
    word = _text(raw, "/field", MAX_WORD_CHARS).strip().lower()
    if word not in FIELDS:
        raise _failure("input.invalid", "/field", f"{_shown(raw)} is not one of {list(FIELDS)}", allowed=list(FIELDS))
    return FIELDS[word]


def parse_colormap(raw: str, field: Field) -> str:
    word = _text(raw, "/colormap", MAX_WORD_CHARS).strip().lower()
    if word not in COLORMAPS:
        raise _failure("input.invalid", "/colormap", f"{_shown(raw)} is not one of {list(COLORMAPS)}", allowed=list(COLORMAPS))
    if word == "auto":
        return "diverging" if field.signed else "sequential"
    return word


def parse_output_unit(raw: str, field: Field) -> tuple[str, float]:
    """The unit of the labels and of ``_FIELD``, and the factor from MPa or mm to it."""
    text = _text(raw, "/output_unit", MAX_WORD_CHARS).strip()
    if text.lower() in ("", "auto"):
        return BASE_UNIT[field.kind], 1.0
    if not re.fullmatch(r"[A-Za-z_ */^0-9.()]+", text):
        raise _failure("input.invalid", "/output_unit", f"{_shown(text)} is not a unit name such as 'MPa', 'psi', 'mm' or 'um'")
    try:
        qty = parse_quantity(f"1 {text}", name="/output_unit", dimension=DIMENSION[field.kind])
        if float(qty.magnitude) != 1.0:
            raise _failure("input.invalid", "/output_unit", f"{_shown(text)} is not a plain unit name (it carries a number)")
        factor = float(ureg.Quantity(1.0, BASE_UNIT[field.kind]).to(qty.units).magnitude)
    except UnitsError as exc:
        raise ToolFailure(exc.family, f"output_unit: {_shown(text)} is not a {DIMENSION[field.kind]} unit (field {field.name!r})", {"param": "/output_unit"}) from None
    except ToolFailure:
        raise
    except (ArithmeticError, ValueError):
        raise _failure("input.out_of_range", "/output_unit", f"{_shown(text)} is outside the supported range of units") from None
    if not (math.isfinite(factor) and 0.0 < factor and factor * MAGNITUDE_CAP[field.kind] <= MAX_LABEL_MAGNITUDE and factor >= 1e-12):
        raise _failure("input.out_of_range", "/output_unit", f"{_shown(text)} is outside the supported range of units")
    return str(qty.units), factor


def parse_range(raw: str, field: Field) -> tuple[float, float] | None:
    """``None`` for 'auto', else (lo, hi) in MPa (stress fields) or mm (displacement fields)."""
    text = _text(raw, "/range", MAX_RANGE_CHARS).strip()
    if text.lower() in ("", "auto"):
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2 or not all(parts):
        raise _failure("input.invalid", "/range", f"{_shown(text)} is not 'auto' or 'lo, hi' with units, e.g. '0 MPa, 250 MPa'")
    ends = []
    for part in parts:
        try:
            qty = parse_quantity(part, name="/range", dimension=DIMENSION[field.kind])
            value = float(qty.to(BASE_UNIT[field.kind]).magnitude)
        except UnitsError as exc:
            what = "a stress (e.g. '250 MPa')" if field.kind == "stress" else "a length (e.g. '0.05 mm')"
            raise ToolFailure(exc.family, f"range: {_shown(part)} is not {what} for field {field.name!r}", {"param": "/range"}) from None
        except (ArithmeticError, ValueError):
            raise _failure("input.out_of_range", "/range", f"{_shown(part)} is outside the supported range") from None
        if not math.isfinite(value) or abs(value) > MAGNITUDE_CAP[field.kind]:
            raise _failure(
                "input.out_of_range",
                "/range",
                f"{_shown(part)} is outside +-{MAGNITUDE_CAP[field.kind]:g} {'MPa' if field.kind == 'stress' else 'mm'}",
                limit=MAGNITUDE_CAP[field.kind],
            )
        ends.append(value)
    lo, hi = ends
    if not lo < hi:
        raise _failure("input.invalid", "/range", f"the low end must be below the high end, got {_shown(text, 64)}")
    return lo, hi


def parse_deformation(raw: str) -> float | str:
    """``'auto'`` or a factor 0 .. 1e4."""
    text = _text(raw, "/deformation_scale", MAX_WORD_CHARS).strip().lower()
    if text == "auto":
        return "auto"
    if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)(e[+-]?\d+)?", text):
        raise _failure("input.invalid", "/deformation_scale", f"{_shown(raw)} is not '0', 'auto' or a plain factor such as '50'")
    factor = float(text)
    if not math.isfinite(factor) or not 0.0 <= factor <= MAX_DEFORMATION_SCALE:
        raise _failure(
            "input.out_of_range",
            "/deformation_scale",
            f"{_shown(raw)}: the factor must be between 0 and {MAX_DEFORMATION_SCALE:g}",
            limit=MAX_DEFORMATION_SCALE,
        )
    return factor


def parse_name(raw: str) -> str:
    text = _text(raw, "/name", MAX_NAME_CHARS).strip()
    if not text:
        raise _failure("input.invalid", "/name", "must not be empty")
    bad = [ch for ch in text if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn")]
    if bad:
        raise _failure("input.invalid", "/name", f"holds {len(bad)} control, format or unassigned character(s), first U+{ord(bad[0]):04X}")
    return text


# --------------------------------------------------------------------------- reading the inputs


@dataclass(frozen=True)
class Source:
    mode: str  # "result_dir" or "files"
    mesh: meshing.TetMesh
    table: dict[str, np.ndarray]  # results.csv column -> (n,) values, rows in node order
    files: dict[str, str]  # name -> sha256 of the bytes read
    job_id: str | None
    static_values: dict[str, Any]  # fem_static's own values (result_dir only)
    mesh_param: str
    results_param: str


def _section_count(lines: list[str], name: str, param: str) -> tuple[int, int]:
    """Index of the ``$Nodes`` / ``$Elements`` line and the count on the line after it."""
    try:
        at = lines.index(name)
        count = int(lines[at + 1].strip())
    except (ValueError, IndexError):
        raise _failure("input.invalid", param, f"not a Gmsh msh2 file: no {name} section with a count") from None
    if count < 0:
        raise _failure("input.invalid", param, f"the {name} count is negative")
    return at, count


def read_mesh_file(path: Path, param: str, deadline: Deadline) -> meshing.TetMesh:
    """The 10-node tetrahedra of an ASCII msh2 file, as :func:`.mesh.read_msh` reads them (same node order, same midside swap),
    but with the counts of the file checked against the caps before any row is parsed, and the deadline checked every
    ``PARSE_CHUNK`` rows.  ``mesh.read_msh`` is shared with fem_static and stays as it is."""
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (UnicodeDecodeError, ValueError):
        raise _failure("input.invalid", param, "the mesh file is not ASCII text (a Gmsh msh2 file is)") from None
    at_nodes, n_nodes = _section_count(lines, "$Nodes", param)
    at_elements, n_elements = _section_count(lines, "$Elements", param)
    if n_nodes > meshing.MAX_NODES or n_elements > MAX_FILE_ELEMENTS:
        raise _failure(
            "input.too_large",
            param,
            f"the file declares {n_nodes} nodes and {n_elements} elements; at most {meshing.MAX_NODES} nodes and {MAX_FILE_ELEMENTS} elements of all "
            f"kinds (fem_static's cap is {meshing.MAX_ELEMENTS} tetrahedra) are read",
            nodes=n_nodes,
            elements=n_elements,
        )
    node_lines = lines[at_nodes + 2 : at_nodes + 2 + n_nodes]
    element_lines = lines[at_elements + 2 : at_elements + 2 + n_elements]
    if len(node_lines) != n_nodes or len(element_lines) != n_elements:
        raise _failure("input.invalid", param, "the mesh file is truncated (fewer rows than its counts)")
    blocks = []
    for start in range(0, n_nodes, PARSE_CHUNK):
        deadline("read mesh")
        try:
            blocks.append(np.array([row.split() for row in node_lines[start : start + PARSE_CHUNK]], dtype=np.float64).reshape(-1, 4))
        except (ValueError, OverflowError):
            raise _failure("input.invalid", param, "a node row is not an id and three coordinates") from None
    node_rows = np.concatenate(blocks) if blocks else np.empty((0, 4))
    tets: list[list[str]] = []
    first_order = 0
    for start in range(0, n_elements, PARSE_CHUNK):
        deadline("read mesh")
        for row in element_lines[start : start + PARSE_CHUNK]:
            parts = row.split()
            if len(parts) < 3:
                raise _failure("input.invalid", param, "an element row is too short")
            if parts[1] == "11":
                try:
                    tags = int(parts[2])
                except ValueError:
                    raise _failure("input.invalid", param, "an element row has no tag count") from None
                connectivity = parts[3 + tags :] if 0 <= tags <= 64 else []
                if len(connectivity) != 10:
                    raise _failure("input.invalid", param, "a type-11 element does not have 10 nodes")
                tets.append(connectivity)
            elif parts[1] == "4":
                first_order += 1
    if not tets:
        raise _failure(
            "input.invalid", param, "the file holds no 10-node tetrahedra" + (f" ({first_order} first-order ones; fem_static writes second-order meshes)" if first_order else "")
        )
    deadline("read mesh")
    try:
        connectivity = np.array(tets, dtype=np.int64)
    except (ValueError, OverflowError):
        raise _failure("input.invalid", param, "an element refers to a node by something that is not an integer") from None
    if not np.all(np.isfinite(node_rows)):
        raise _failure("input.invalid", param, "a node row holds a value that is not a finite number")
    ids = node_rows[:, 0]
    if not np.all(ids == np.round(ids)) or len(np.unique(ids)) != len(ids):
        raise _failure("input.invalid", param, "the node ids are not distinct integers")
    ids_int = ids.astype(np.int64)
    order = np.argsort(ids_int, kind="stable")
    position = np.searchsorted(ids_int[order], connectivity)
    if np.any(position >= len(ids_int)) or not np.array_equal(ids_int[order][np.minimum(position, len(ids_int) - 1)], connectivity):
        raise _failure("input.invalid", param, "an element refers to a node that the file does not list")
    index = order[position]
    used = np.unique(index)
    remap = np.full(len(ids_int), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    index = remap[index]
    index[:, [8, 9]] = index[:, [9, 8]]  # Gmsh edges 34, 24 -> CalculiX edges 24, 34 (as mesh.read_msh)
    return meshing.TetMesh(nodes=node_rows[used, 1:4], tets=index, size_mm=0.0)


TET_FACES = ((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2))  # facet k is opposite corner k (as in mesh.py)


def check_elements(tetra: meshing.TetMesh, param: str) -> None:
    """Refuse what the surface code cannot draw, before any normal is computed: a tetrahedron whose corners are coplanar
    or collinear (zero volume: its facets have no direction), a face shared by more than two tetrahedra, and a mesh with no
    boundary facet at all (every face shared, e.g. one element listed twice)."""
    corners = tetra.nodes[tetra.tets[:, :4]]
    e1, e2, e3 = (corners[:, k] - corners[:, 0] for k in (1, 2, 3))
    volume6 = np.abs(np.einsum("mk,mk->m", e1, np.cross(e2, e3)))
    longest = np.max([np.linalg.norm(corners[:, b] - corners[:, a], axis=1) for a in range(4) for b in range(a + 1, 4)], axis=0)
    flat = volume6 <= DEGENERATE_RELATIVE * longest**3
    if np.any(flat):
        first = int(np.argmax(flat))
        raise _failure(
            "input.invalid",
            param,
            f"{int(flat.sum())} tetrahedra have coplanar or collinear corners (zero volume; first: element {first + 1} of the file's tetrahedra); "
            "their faces have no direction, so the surface cannot be drawn",
            degenerate_elements=int(flat.sum()),
            first_element=first + 1,
        )
    tri = np.sort(np.stack([tetra.tets[:, list(face)] for face in TET_FACES], axis=1).reshape(-1, 3), axis=1)
    n = np.int64(len(tetra.nodes))
    _, counts = np.unique((tri[:, 0] * n + tri[:, 1]) * n + tri[:, 2], return_counts=True)
    if np.any(counts > 2):
        raise _failure("input.invalid", param, f"{int(np.sum(counts > 2))} faces are shared by more than two tetrahedra: not a valid solid mesh")
    if not np.any(counts == 1):
        raise _failure("input.invalid", param, "no boundary facets: the elements do not form a surface (every face is shared, e.g. an element listed twice)")


def _guarded(param: str, what: str, fn, *args):
    """Run a reading or surface step; anything but a ToolFailure becomes ``input.invalid`` at the file's pointer, with the
    exception's type only (no traceback, no path)."""
    try:
        return fn(*args)
    except ToolFailure:
        raise
    except MemoryError:
        raise _failure("input.too_large", param, f"{what}: not enough memory") from None
    except Exception as exc:  # noqa: BLE001 - a malformed file must not surface as an engine failure with a traceback
        raise _failure("input.invalid", param, f"{what} could not be processed ({type(exc).__name__})") from None


def _check_mesh_caps(tetra: meshing.TetMesh, param: str) -> meshing.TetMesh:
    if len(tetra.tets) > meshing.MAX_ELEMENTS or len(tetra.nodes) > meshing.MAX_NODES:
        raise _failure(
            "input.too_large",
            param,
            f"{len(tetra.tets)} elements and {len(tetra.nodes)} nodes; fem_static's limits are {meshing.MAX_ELEMENTS} and {meshing.MAX_NODES}",
            elements=len(tetra.tets),
            nodes=len(tetra.nodes),
        )
    if np.abs(tetra.nodes).max() > MAX_COORDINATE_MM:
        raise _failure("input.out_of_range", param, f"a node coordinate is beyond {MAX_COORDINATE_MM:g} mm")
    return tetra


def _read_csv(path: Path, param: str, n_nodes: int) -> dict[str, np.ndarray]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (UnicodeDecodeError, ValueError):
        raise _failure("input.invalid", param, "results.csv is not ASCII text") from None
    if not lines:
        raise _failure("input.invalid", param, "results.csv is empty")
    header = [h.strip() for h in lines[0].split(",")]
    unknown = [h for h in header if h not in CSV_COLUMNS]
    if unknown or len(set(header)) != len(header) or not {"node", "x_mm", "y_mm", "z_mm"} <= set(header):
        raise _failure(
            "input.invalid",
            param,
            "the header is not one fem_static writes (columns from: " + ", ".join(CSV_COLUMNS) + "; node and x_mm, y_mm, z_mm required)",
            unknown_columns=len(unknown),
        )
    rows = [line for line in lines[1:] if line.strip()]
    if len(rows) != n_nodes:
        raise ToolFailure(
            "fem.integrity_mismatch",
            f"{param.lstrip('/')}: results.csv has {len(rows)} rows but the mesh has {n_nodes} nodes; they are not from the same fem_static run",
            {"param": param, "rows": len(rows), "nodes": n_nodes},
        )
    try:
        cells = [row.split(",") for row in rows]
        if any(len(c) != len(header) for c in cells):
            raise ValueError("row length")
        array = np.array(cells, dtype=np.float64).reshape(len(rows), len(header))
    except (ValueError, OverflowError):
        raise _failure("input.invalid", param, "results.csv holds a row that is not as many numbers as the header names") from None
    if not np.all(np.isfinite(array)):
        raise _failure("input.invalid", param, "results.csv holds a value that is not a finite number (NaN or infinity)")
    table = {name: array[:, k] for k, name in enumerate(header)}
    if not np.array_equal(table["node"], np.arange(1, n_nodes + 1)):
        raise _failure("input.invalid", param, "the node column of results.csv is not 1, 2, 3, ... in order")
    for name, values in table.items():
        cap = MAX_STRESS_MPA if name.endswith("_mpa") else (MAX_LENGTH_MM if name.startswith("u") else MAX_COORDINATE_MM)
        if name != "node" and np.abs(values).max(initial=0.0) > cap:
            raise _failure("input.out_of_range", param, f"results.csv column {name} holds a value beyond {cap:g}", column=name, limit=cap)
    return table


def coordinate_tolerance(nodes_mm: np.ndarray) -> float:
    """How far a coordinate in results.csv may lie from the mesh's: ``1e-6`` of the diagonal (the consistency guard) plus the
    ``%.9g`` rounding bound of fem_static's CSV, ``5e-9 |x|``, with a safety factor of 4 (``2e-8 max |x|``).  A part 20 m from
    the origin is printed with errors up to 1e-4 mm while its diagonal is 25 mm: without the second term it would be refused."""
    diagonal = float(np.linalg.norm(nodes_mm.max(axis=0) - nodes_mm.min(axis=0))) if len(nodes_mm) else 0.0
    return 1e-6 * max(diagonal, 1e-3) + CSV_SAFETY * CSV_RELATIVE_ROUNDING * float(np.abs(nodes_mm).max(initial=0.0))


def _file_sha(job: JobHandle) -> str:
    return job.input_files[-1].sha256


def read_source(job: JobHandle, result_dir: str, mesh_file: str, results_file: str, deadline: Deadline) -> Source:
    given_dir = bool(result_dir.strip())
    given_files = bool(mesh_file.strip() or results_file.strip())
    if given_dir and given_files:
        raise _failure("input.invalid", "/result_dir", "give either result_dir or mesh_file with results_file, not both")
    if not given_dir and not given_files:
        raise _failure("input.missing", "/result_dir", "give result_dir (the job directory of a fem_static run) or mesh_file and results_file")
    static_values: dict[str, Any] = {}
    job_id = None
    files: dict[str, str] = {}
    if given_dir:
        folder = Path(result_dir.strip()).expanduser()
        param = "/result_dir"
        manifest_path = job.input_file(str(folder / "manifest.json"), param=param, extensions=(".json",), max_bytes=MAX_MANIFEST_BYTES)
        not_static = lambda why: ToolFailure(  # noqa: E731
            "fem.not_a_static_result", f"result_dir: the directory holds no valid manifest of a fem_static job ({why})", {"param": param}
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise not_static("manifest.json is not JSON") from None
        listed = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(manifest, dict) or manifest.get("tool") != "fem_static" or not isinstance(listed, dict):
            raise not_static("its tool is not fem_static" if isinstance(manifest, dict) else "not an object")
        missing = [name for name in (*STATIC_FILES, "result.json") if not (isinstance(listed.get(name), str) and SHA256.fullmatch(listed[name]))]
        if missing:
            raise not_static(f"it lists no sha256 for {', '.join(missing)}")
        job_id = manifest.get("job_id") if isinstance(manifest.get("job_id"), str) and len(manifest["job_id"]) <= 128 else None
        paths: dict[str, Path] = {}
        for name, ext, cap in (("result.json", ".json", MAX_RESULT_JSON_BYTES), ("mesh.msh", ".msh", MAX_MESH_BYTES), ("results.csv", ".csv", MAX_CSV_BYTES)):
            deadline("read")
            paths[name] = job.input_file(str(folder / name), param=param, extensions=(ext,), max_bytes=cap)
            files[name] = _file_sha(job)
            if files[name] != listed[name]:
                raise ToolFailure(
                    "fem.integrity_mismatch",
                    f"result_dir: {name} does not match the sha256 that manifest.json lists for it; the file was changed after fem_static wrote it",
                    {"param": param, "file": name},
                )
        try:
            result = json.loads(paths["result.json"].read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise not_static("result.json is not JSON") from None
        if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(result.get("values"), dict):
            raise not_static("result.json is not a successful fem_static result")
        for key in ("max_von_mises", "max_displacement"):
            entry = result["values"].get(key)
            if isinstance(entry, dict) and isinstance(entry.get("quantity"), (int, float)) and isinstance(entry.get("unit"), str) and len(entry["unit"]) < 64:
                static_values[key] = {"quantity": float(entry["quantity"]), "unit": entry["unit"]}
        mesh_path, csv_path, mesh_param, results_param = paths["mesh.msh"], paths["results.csv"], param, param
    else:
        if not (mesh_file.strip() and results_file.strip()):
            missing_param = "/mesh_file" if not mesh_file.strip() else "/results_file"
            raise _failure("input.missing", missing_param, "mesh_file and results_file go together (mesh.msh and results.csv of one fem_static run)")
        mesh_param, results_param = "/mesh_file", "/results_file"
        mesh_path = job.input_file(mesh_file.strip(), param=mesh_param, extensions=(".msh",), max_bytes=MAX_MESH_BYTES)
        files["mesh.msh"] = _file_sha(job)
        deadline("read")
        csv_path = job.input_file(results_file.strip(), param=results_param, extensions=(".csv",), max_bytes=MAX_CSV_BYTES)
        files["results.csv"] = _file_sha(job)
    deadline("read")
    tetra = _guarded(mesh_param, "the mesh file", read_mesh_file, mesh_path, mesh_param, deadline)
    tetra = _check_mesh_caps(tetra, mesh_param)
    deadline("read")
    table = _guarded(results_param, "results.csv", _read_csv, csv_path, results_param, len(tetra.nodes))
    xyz = np.column_stack([table["x_mm"], table["y_mm"], table["z_mm"]])
    gap = float(np.abs(xyz - tetra.nodes).max())
    tolerance = coordinate_tolerance(tetra.nodes)
    if gap > tolerance:
        raise ToolFailure(
            "fem.integrity_mismatch",
            f"{results_param.lstrip('/')}: the node coordinates of results.csv differ from the mesh by up to {gap:.3g} mm (tolerance {tolerance:.3g} mm); "
            "the results do not belong to this mesh",
            {"param": results_param, "gap_mm": gap, "tolerance_mm": tolerance},
        )
    return Source("result_dir" if given_dir else "files", tetra, table, files, job_id, static_values, mesh_param, results_param)


def field_values(field: Field, table: dict[str, np.ndarray], param: str) -> np.ndarray:
    """Nodal values of the field in MPa or mm, as fem_static wrote them (principal stresses from the six components)."""
    absent = [c for c in field.columns if c not in table]
    if absent:
        available = [name for name, f in FIELDS.items() if all(c in table for c in f.columns)]
        raise ToolFailure(
            "fem.field_not_available",
            f"field: {field.name!r} needs the column(s) {', '.join(absent)}, which the results file does not hold; available: {available}",
            {"param": "/field", "available": available},
        )
    if field.name.startswith("principal"):
        s = np.column_stack([table[c] for c in STRESS_COLUMNS])
        tensor = np.empty((len(s), 3, 3))
        tensor[:, 0, 0], tensor[:, 1, 1], tensor[:, 2, 2] = s[:, 0], s[:, 1], s[:, 2]
        tensor[:, 0, 1] = tensor[:, 1, 0] = s[:, 3]
        tensor[:, 1, 2] = tensor[:, 2, 1] = s[:, 4]
        tensor[:, 2, 0] = tensor[:, 0, 2] = s[:, 5]
        eig = np.linalg.eigvalsh(tensor)  # ascending
        return eig[:, -1] if field.name == "principal_max" else eig[:, 0]
    return np.array(table[field.columns[0]], dtype=np.float64)


# --------------------------------------------------------------------------- the surface (pure)

# the four sub-triangles of a six-node facet, local nodes a b c ab bc ca = 0..5, all with the facet's (outward) orientation
SUB_TRIANGLES = np.array([[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]])
_NODE_POINTS = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0.5, 0.5, 0], [0, 0.5, 0.5], [0.5, 0, 0.5]], dtype=float)


@dataclass(frozen=True)
class Surface:
    facets: meshing.Facets
    nodes6: np.ndarray  # (f, 6) node indices: corners a b c, mids ab bc ca
    vertex_node: np.ndarray  # (v,) node of each vertex
    corner_vertex: np.ndarray  # (f, 6) vertex of each local node
    triangles: np.ndarray  # (4 f, 3) vertex indices
    edges: np.ndarray  # (e, 3) unique facet edges: corner, mid, corner (node indices)
    feature: np.ndarray  # (e,) bool: the two facets of the edge meet at more than the feature angle (or it is not shared by exactly two)


_LOCAL_ENDS = np.array([[0, 1], [1, 2], [2, 0]])  # corner positions of the facet edges ab, bc, ca
_LOCAL_MIDS = np.array([3, 4, 5])


def components(n: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Connected components of ``n`` items joined by the pairs ``(u, v)``: the smallest member's index of each item's component.
    Minimum-label propagation with pointer jumping; the fans joined here are a few facets wide, so it settles in a few rounds."""
    label = np.arange(n, dtype=np.int64)
    if len(u) == 0:
        return label
    while True:
        low = np.minimum(label[u], label[v])
        new = label.copy()
        np.minimum.at(new, u, low)
        np.minimum.at(new, v, low)
        new = new[new]
        if np.array_equal(new, label):
            return label
        label = new


def build_surface(tetra: meshing.TetMesh, facets: meshing.Facets) -> Surface:
    """Feature edges per facet pair, and vertices per smooth sector of each node's facet fan.

    An edge is a feature edge when its two facets' normals differ by more than ``FEATURE_ANGLE_DEG`` (or it does not have
    exactly two facets).  The six (facet, local node) incidences of every facet are joined across each non-feature edge, for
    the edge's three nodes; each connected group of incidences of one node is one vertex.  So a node gets as many vertices
    (normals) as its fan of facets has sectors separated by feature edges, whatever happens elsewhere on the part.
    """
    f = len(facets.corners)
    nodes6 = np.concatenate([facets.corners, facets.mids], axis=1)
    ends = np.stack([facets.corners, np.roll(facets.corners, -1, axis=1)], axis=2).reshape(-1, 2)  # (3 f, 2): ab, bc, ca
    mids = facets.mids.reshape(-1)
    owner = np.repeat(np.arange(f), 3)
    local_ends = np.tile(_LOCAL_ENDS, (f, 1))
    local_mid = np.tile(_LOCAL_MIDS, f)
    lo, hi = ends.min(axis=1), ends.max(axis=1)
    n = np.int64(len(tetra.nodes))
    order = np.argsort(lo * n + hi, kind="stable")
    unique_keys, first, counts = np.unique((lo * n + hi)[order], return_index=True, return_counts=True)
    shared = counts == 2
    ia, ib = order[first[shared]], order[first[shared] + 1]  # the two incidences of every shared edge
    fa, fb = owner[ia], owner[ib]
    smooth = np.einsum("ek,ek->e", facets.normals[fa], facets.normals[fb]) >= math.cos(math.radians(FEATURE_ANGLE_DEG))
    feature = np.ones(len(unique_keys), dtype=bool)
    feature[np.nonzero(shared)[0]] = ~smooth
    ia, ib, fa, fb = ia[smooth], ib[smooth], fa[smooth], fb[smooth]
    same = ends[ib, 0] == ends[ia, 0]  # is the first corner of the edge in facet a also the first in facet b?
    u = np.concatenate([fa * 6 + local_ends[ia, 0], fa * 6 + local_ends[ia, 1], fa * 6 + local_mid[ia]])
    v = np.concatenate(
        [fb * 6 + np.where(same, local_ends[ib, 0], local_ends[ib, 1]), fb * 6 + np.where(same, local_ends[ib, 1], local_ends[ib, 0]), fb * 6 + local_mid[ib]]
    )
    group = components(6 * f, u, v)
    unique_vertices, inverse = np.unique(group, return_inverse=True)
    corner_vertex = inverse.reshape(f, 6)
    triangles = corner_vertex[:, SUB_TRIANGLES].reshape(-1, 3)
    first_edge = order[first]
    edges = np.stack([ends[first_edge, 0], mids[first_edge], ends[first_edge, 1]], axis=1)
    return Surface(facets, nodes6, nodes6.reshape(-1)[unique_vertices], corner_vertex, triangles, edges, feature)


def vertex_normals(surface: Surface, nodes_mm: np.ndarray) -> np.ndarray:
    """Outward unit normals of the curved six-node facets at their nodes, area-weighted over the facets of one smooth sector
    of the node's fan (one vertex); computed on the drawn (possibly deformed) positions."""
    _, d = meshing.tri6_shape(_NODE_POINTS)  # (6 points, 6 nodes, 2)
    x = nodes_mm[surface.nodes6]  # (f, 6, 3)
    tangent = np.einsum("fnk,pnj->fpkj", x, d)
    normal = np.cross(tangent[..., 0], tangent[..., 1])  # (f, 6, 3) outward: d/dL2 x d/dL3 = (b - a) x (c - a) on a flat facet
    length = np.linalg.norm(normal, axis=2, keepdims=True)
    fallback = np.broadcast_to(surface.facets.normals[:, None, :], normal.shape)
    unit = np.where(length > 1e-300, normal / np.where(length > 1e-300, length, 1.0), fallback)
    weighted = unit * surface.facets.areas[:, None, None]
    out = np.zeros((len(surface.vertex_node), 3))
    np.add.at(out, surface.corner_vertex.reshape(-1), weighted.reshape(-1, 3))
    size = np.linalg.norm(out, axis=1)
    weak = size <= 1e-300
    if weak.any():  # cannot happen on a valid mesh; kept so that no NaN reaches the file
        out[weak] = (0.0, 0.0, 1.0)
        size[weak] = 1.0
    return out / size[:, None]


def enclosed_volume(points: np.ndarray, triangles: np.ndarray) -> float:
    """Signed volume inside a closed, outward-oriented triangle surface (divergence theorem)."""
    a, b, c = (points[triangles[:, k]] for k in range(3))
    return float(np.einsum("tk,tk->t", a, np.cross(b, c)).sum() / 6.0)


# --------------------------------------------------------------------------- the GLB (pure)


def to_gltf(points_mm: np.ndarray) -> np.ndarray:
    p = np.asarray(points_mm, dtype=np.float64).reshape(-1, 3)
    return np.stack([p[:, 0], p[:, 2], -p[:, 1]], axis=1) / 1000.0


def to_model(gltf_m: np.ndarray) -> np.ndarray:
    g = np.asarray(gltf_m, dtype=np.float64).reshape(-1, 3)
    return np.stack([g[:, 0], -g[:, 2], g[:, 1]], axis=1) * 1000.0


def _bounds(array: np.ndarray, components: int) -> tuple[list[float], list[float]]:
    a = np.asarray(array).reshape(-1, components)
    return [float(x) for x in a.min(axis=0)], [float(x) for x in a.max(axis=0)]


@dataclass(frozen=True)
class LinePrimitive:
    kind: str  # "mesh_edges" or "undeformed_outline"
    colour: str
    positions: np.ndarray  # (p, 3) float32, glTF axes
    indices: np.ndarray  # (2 s,) uint32
    meaning: str


def glb_bytes(
    *,
    name: str,
    positions: np.ndarray,
    normals: np.ndarray,
    colours: np.ndarray,
    field: np.ndarray,
    node_ids: np.ndarray,
    indices: np.ndarray,
    lines: list[LinePrimitive],
    surface_extras: dict[str, Any],
    node_extras: dict[str, Any],
) -> bytes:
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
        accessors.append({"bufferView": len(views) - 1, "componentType": component, "count": int(array.size // components), "type": kind, "min": lo, "max": hi})
        return len(accessors) - 1

    f32 = lambda a: np.ascontiguousarray(a, dtype="<f4")  # noqa: E731
    materials: list[dict[str, Any]] = [
        {
            "name": "field",
            "doubleSided": False,
            "pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0], "metallicFactor": METALLIC, "roughnessFactor": ROUGHNESS},
        }
    ]
    surface = {
        "attributes": {
            "POSITION": add(f32(positions), component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3),
            "NORMAL": add(f32(normals), component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3),
            "COLOR_0": add(f32(colours), component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3),
            "_FIELD": add(f32(field), component=FLOAT32, kind="SCALAR", target=ARRAY_BUFFER, components=1),
            "_NODE": add(f32(node_ids), component=FLOAT32, kind="SCALAR", target=ARRAY_BUFFER, components=1),
        },
        "indices": add(np.ascontiguousarray(indices, dtype="<u4"), component=UINT32, kind="SCALAR", target=ELEMENT_ARRAY_BUFFER, components=1, integer=True),
        "material": 0,
        "mode": TRIANGLES,
        "extras": surface_extras,
    }
    primitives = [surface]
    for line in lines:
        if not len(line.indices):
            continue
        materials.append(
            {
                "name": line.kind,
                "doubleSided": False,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*[round(float(c), 6) for c in srgb_to_linear(np.array([int(line.colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]))], 1.0],
                    "metallicFactor": METALLIC,
                    "roughnessFactor": ROUGHNESS,
                },
            }
        )
        primitives.append(
            {
                "attributes": {"POSITION": add(f32(line.positions), component=FLOAT32, kind="VEC3", target=ARRAY_BUFFER, components=3)},
                "indices": add(np.ascontiguousarray(line.indices, dtype="<u4"), component=UINT32, kind="SCALAR", target=ELEMENT_ARRAY_BUFFER, components=1, integer=True),
                "material": len(materials) - 1,
                "mode": LINES,
                "extras": {"kind": line.kind, "color": line.colour, "segments": int(len(line.indices) // 2), "meaning": line.meaning},
            }
        )
    document = {
        "asset": {"generator": "eng-toolkit fem_view", "version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name, "extras": node_extras}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    text = json.dumps(document, separators=(",", ":"), sort_keys=True, allow_nan=False, ensure_ascii=True).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = bytes(blob)
    total = 12 + 8 + len(text) + 8 + len(body)
    return struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(text), GLB_JSON) + text + struct.pack("<II", len(body), GLB_BIN) + body


@dataclass(frozen=True)
class ReadBack:
    document: dict[str, Any]
    positions: np.ndarray
    colours: np.ndarray
    field: np.ndarray
    node_ids: np.ndarray
    triangles: np.ndarray
    line_segments: dict[str, int]


def read_back(data: bytes) -> ReadBack:
    """Parse the GLB just written and decode the surface primitive and the line counts from its bytes."""
    bad = lambda why: ToolFailure("fem.readback_mismatch", f"the GLB just written does not read back: {why}", {"param": "/result_dir"})  # noqa: E731
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

    primitives = document["meshes"][0]["primitives"]
    surface = primitives[0]
    attributes = surface["attributes"]
    positions = array(attributes["POSITION"], 3)
    triangles = array(surface["indices"], 1).reshape(-1, 3).astype(np.int64)
    if len(triangles) and int(triangles.max()) >= len(positions):
        raise bad("an index is out of range")
    segments: dict[str, int] = {}
    for p in primitives[1:]:
        count = len(array(p["indices"], 1)) // 2
        if count and int(array(p["indices"], 1).max()) >= len(array(p["attributes"]["POSITION"], 3)):
            raise bad("a line index is out of range")
        segments[p["extras"]["kind"]] = count
    return ReadBack(
        document,
        positions.astype(np.float64),
        array(attributes["COLOR_0"], 3).astype(np.float64),
        array(attributes["_FIELD"], 1).reshape(-1).astype(np.float64),
        array(attributes["_NODE"], 1).reshape(-1).astype(np.float64),
        triangles,
        segments,
    )


# --------------------------------------------------------------------------- the legend (pure)

LEGEND_WIDTH, LEGEND_HEIGHT = 340, 470
BAR_X, BAR_Y, BAR_W, BAR_H = 24, 70, 28, 280


def _fmt(value: float, digits: int = 4) -> str:
    text = f"{value:.{digits}g}"
    return "0" if text in ("-0", "0") else text


def tick_digits(lo: float, hi: float) -> int:
    """Significant digits that tell the 11 ticks apart: at least 4, enough to resolve one tick step at the largest magnitude."""
    step = (hi - lo) / (LEGEND_SAMPLES - 1)
    top = max(abs(lo), abs(hi))
    if step <= 0.0 or top <= 0.0:
        return 4
    return int(min(12, max(4, math.ceil(math.log10(top / step)) + 2)))


def legend_svg(*, name: str, label: str, unit: str, table: np.ndarray, lo: float, hi: float, notes: list[str]) -> str:
    """A self-contained colour bar: plain SVG elements, text escaped, no script, no external reference."""
    attr = lambda s: escape(s, {'"': "&quot;", "'": "&apos;"})  # noqa: E731
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{LEGEND_WIDTH}" height="{LEGEND_HEIGHT}" viewBox="0 0 {LEGEND_WIDTH} {LEGEND_HEIGHT}">',
        f"<title>{attr(name)}: {attr(label)} [{attr(unit)}]</title>",
        f'<rect x="0" y="0" width="{LEGEND_WIDTH}" height="{LEGEND_HEIGHT}" fill="#FFFFFF"/>',
        f'<text x="16" y="26" font-family="sans-serif" font-size="15" font-weight="bold" fill="#000000">{attr(name)}</text>',
        f'<text x="16" y="48" font-family="sans-serif" font-size="12" fill="#000000">{attr(label)} [{attr(unit)}]</text>',
        "<defs>",
        '<linearGradient id="ramp" x1="0" y1="1" x2="0" y2="0">',
    ]
    for k, rgb in enumerate(table):
        out.append(f'<stop offset="{k / (len(table) - 1):.6f}" stop-color="{hex_colour(rgb)}"/>')
    out += [
        "</linearGradient>",
        "</defs>",
        f'<rect x="{BAR_X}" y="{BAR_Y}" width="{BAR_W}" height="{BAR_H}" fill="url(#ramp)" stroke="#000000" stroke-width="1"/>',
    ]
    digits = tick_digits(lo, hi)
    for k in range(LEGEND_SAMPLES):
        value = lo + (hi - lo) * k / (LEGEND_SAMPLES - 1)
        y = BAR_Y + BAR_H - BAR_H * k / (LEGEND_SAMPLES - 1)
        out.append(f'<line x1="{BAR_X + BAR_W}" y1="{y:.2f}" x2="{BAR_X + BAR_W + 6}" y2="{y:.2f}" stroke="#000000" stroke-width="1"/>')
        out.append(f'<text x="{BAR_X + BAR_W + 10}" y="{y + 4:.2f}" font-family="sans-serif" font-size="11" fill="#000000">{attr(_fmt(value, digits))}</text>')
    y = BAR_Y + BAR_H + 26
    for note in notes:
        out.append(f'<text x="16" y="{y}" font-family="sans-serif" font-size="10" fill="#000000">{attr(note)}</text>')
        y += 15
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- the tool


def beyond_float32(larger: float, smaller: float) -> bool:
    """``larger`` exceeds ``smaller`` by more than one float32 step at their magnitude (the resolution of ``_FIELD``)."""
    step = float(np.spacing(np.float32(max(abs(larger), abs(smaller)))))
    return float(larger) - float(smaller) > step


def _q(value: float, unit: str) -> QuantityOut:
    return QuantityOut(quantity=float(f"{value:.7g}"), unit=unit)


def _count(value: int) -> QuantityOut:
    return QuantityOut(quantity=float(value), unit="dimensionless")


def _g(value: float, digits: int = 9) -> float:
    return float(f"{value:.{digits}g}")


def view(
    job: JobHandle,
    *,
    result_dir: str,
    mesh_file: str,
    results_file: str,
    field: str,
    colormap: str,
    value_range: str,
    deformation_scale: str,
    show_undeformed_edges: bool,
    mesh_edges: bool,
    name: str,
    output_unit: str,
    max_seconds: int,
    deadline: Deadline,
) -> Computed:
    # ---- every parameter is checked before any file is opened; long strings first, never echoed
    for raw, param in ((result_dir, "/result_dir"), (mesh_file, "/mesh_file"), (results_file, "/results_file")):
        _text(raw, param, MAX_PATH_CHARS)
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or not MAX_SECONDS[0] <= max_seconds <= MAX_SECONDS[1]:
        raise _failure("input.out_of_range", "/max_seconds", f"must be a whole number from {MAX_SECONDS[0]} to {MAX_SECONDS[1]}, got {max_seconds!r}")
    for flag, param in ((show_undeformed_edges, "/show_undeformed_edges"), (mesh_edges, "/mesh_edges")):
        if not isinstance(flag, bool):
            raise _failure("input.invalid", param, "must be true or false")
    spec = parse_field(field)
    cmap = parse_colormap(colormap, spec)
    unit, per_base = parse_output_unit(output_unit, spec)
    symbol = f"{ureg.Unit(unit):~P}"
    explicit = parse_range(value_range, spec)
    scale = parse_deformation(deformation_scale)
    title = parse_name(name)

    # ---- the files
    deadline("read")
    source = read_source(job, result_dir, mesh_file, results_file, deadline)
    values_all = field_values(spec, source.table, source.results_param)
    has_u = all(c in source.table for c in ("ux_mm", "uy_mm", "uz_mm"))
    if scale != 0.0 and not has_u:
        raise _failure("fem.field_not_available", "/deformation_scale", "the results file holds no displacement columns (ux_mm, uy_mm, uz_mm) to deform the shape with")
    u = np.column_stack([source.table[c] for c in ("ux_mm", "uy_mm", "uz_mm")]) if has_u else np.zeros_like(source.mesh.nodes)

    # ---- the surface
    deadline("surface")
    tetra = source.mesh
    _guarded(source.mesh_param, "the mesh", check_elements, tetra, source.mesh_param)
    try:
        facets = _guarded(source.mesh_param, "the mesh's boundary", meshing.boundary_facets, tetra)
    except ToolFailure as exc:
        raise _failure("input.invalid", source.mesh_param, exc.message.removeprefix(source.mesh_param.lstrip("/") + ": ")) from None
    if not (np.all(np.isfinite(facets.normals)) and np.all(facets.areas > 0.0)):
        raise _failure("input.invalid", source.mesh_param, "a boundary facet has no area or no direction (degenerate element)")
    estimate = 4 * len(facets.corners)
    if estimate > MAX_TRIANGLES:
        raise _failure(
            "input.too_large",
            source.mesh_param,
            f"the surface would have {estimate:,} triangles ({len(facets.corners):,} boundary facets x 4); the limit is {MAX_TRIANGLES:,}. "
            "Re-run fem_static with a larger element_size",
            triangles=estimate,
            limit=MAX_TRIANGLES,
        )
    surface = _guarded(source.mesh_param, "the surface", build_surface, tetra, facets)
    deadline("surface")
    surface_nodes = np.unique(surface.nodes6)
    diagonal = tetra.diagonal_mm
    magnitude = np.linalg.norm(u, axis=1)
    max_u = float(magnitude.max()) if len(magnitude) else 0.0
    notes: list[str] = []
    warnings: list[str] = []
    if scale == "auto":
        mode = "auto"
        factor = _g(AUTO_DEFORMATION_FRACTION * diagonal / max_u, 6) if max_u > 0.0 else 0.0
        if max_u == 0.0:
            warnings.append("every displacement is zero: deformation_scale 'auto' draws the undeformed shape (factor 0)")
    else:
        mode = "explicit" if scale != 0.0 else "undeformed"
        factor = float(scale)
    drawn_nodes = tetra.nodes + factor * u
    if not np.all(np.isfinite(drawn_nodes)) or np.abs(drawn_nodes).max() > MAX_COORDINATE_MM * 10:
        raise _failure("input.out_of_range", "/deformation_scale", f"the factor {factor:g} moves the shape beyond {MAX_COORDINATE_MM * 10:g} mm")
    exaggerated = factor > 1.0 and max_u > 0.0
    if factor != 0.0 and max_u == 0.0:
        notes.append(f"Displacements are zero: the shape is undeformed (factor x{factor:.6g} has nothing to scale)")
        warnings.append(f"every displacement is zero: deformation_scale {factor:.6g} draws the undeformed shape")
    elif factor > 1.0:
        notes.append(f"Shape exaggerated: displacements drawn x{factor:.6g}")
    elif 0.0 < factor < 1.0:
        notes.append(f"Shape scaled: displacements drawn x{factor:.6g} (less than true size)")
    elif factor == 1.0:
        notes.append("Shape deformed at true scale (x1)")
    undeformed_volume = enclosed_volume(tetra.nodes[surface.vertex_node], surface.triangles)
    drawn_volume = enclosed_volume(drawn_nodes[surface.vertex_node], surface.triangles) if factor != 0.0 else undeformed_volume
    inverted = bool(factor != 0.0 and undeformed_volume > 0.0 and drawn_volume <= 0.0)
    if inverted:
        notes.append("Drawn shape turned inside out: reduce deformation_scale")
        warnings.append(
            f"the deformed shape drawn at x{factor:.6g} is turned inside out (the signed volume of the drawn surface is {drawn_volume:.4g} mm3, "
            f"undeformed {undeformed_volume:.4g} mm3): it self-intersects; reduce deformation_scale"
        )

    # ---- the colours
    shown = values_all[surface_nodes]
    lo_data, hi_data = float(shown.min()), float(shown.max())
    widened = False
    if explicit is not None:
        lo, hi = explicit
        range_mode = "explicit"
    else:
        range_mode = "auto"
        if cmap == "diverging":
            m = max(abs(lo_data), abs(hi_data))
            lo, hi = (-m, m) if m > 0.0 else (-1.0, 1.0)
            widened = m == 0.0
        else:
            lo, hi = lo_data, hi_data
            magnitude_scale = max(abs(lo), abs(hi))
            if magnitude_scale == 0.0:
                lo, hi, widened = 0.0, 1.0, True
            elif hi - lo < MIN_SPAN_RELATIVE * magnitude_scale:
                centre, half = 0.5 * (lo + hi), 0.5 * MIN_SPAN_RELATIVE * magnitude_scale
                lo, hi, widened = centre - half, centre + half, True
    if widened:
        notes.append(f"Auto range widened to {lo * per_base:.6g} .. {hi * per_base:.6g} {symbol}: the field is (nearly) uniform")
    below = int(np.sum(shown < lo))
    above = int(np.sum(shown > hi))
    if below:
        notes.append(f"{below} node(s) below the range: drawn in the low-end colour")
    if above:
        notes.append(f"{above} node(s) above the range: drawn in the high-end colour")
    table = TABLES[cmap]
    vertex_values = values_all[surface.vertex_node]
    t = (vertex_values - lo) / (hi - lo)
    srgb = colours_srgb(t, table)
    linear = srgb_to_linear(srgb)
    label_values = vertex_values * per_base

    # ---- the lines
    lines: list[LinePrimitive] = []
    if mesh_edges:
        lines.append(_line_primitive("mesh_edges", MESH_EDGE_COLOUR, surface.edges, drawn_nodes, "element edges of the boundary facets, on the drawn shape"))
    outline_drawn = False
    if show_undeformed_edges and factor != 0.0:
        outline = surface.edges[surface.feature]
        if len(outline):
            lines.append(_line_primitive("undeformed_outline", OUTLINE_COLOUR, outline, tetra.nodes, f"feature edges of the undeformed shape: every facet edge whose two facets' normals differ by more than {FEATURE_ANGLE_DEG:g} degrees"))
            outline_drawn = True
        else:
            notes.append("No feature edges: the undeformed outline is not drawn")

    # ---- metadata
    legend = []
    for k in range(LEGEND_SAMPLES):
        value = lo + (hi - lo) * k / (LEGEND_SAMPLES - 1)
        rgb = colours_srgb(np.array([k / (LEGEND_SAMPLES - 1)]), table)[0]
        legend.append({"value": _g(value * per_base), "color": hex_colour(rgb), "srgb": [round(float(c), 6) for c in rgb], "linear_rgb": [round(float(c), 6) for c in srgb_to_linear(rgb)]})
    range_info = {
        "mode": range_mode,
        "low": _g(lo * per_base),
        "high": _g(hi * per_base),
        "unit": unit,
        "unit_symbol": symbol,
        "widened": widened,
        "clamped_below": below,
        "clamped_above": above,
        "clamp_rule": "values outside the range take the end colour; counts are of surface nodes",
    }
    deformation = {
        "mode": mode,
        "factor": factor,
        "exaggerated": exaggerated,
        "inverted": inverted,
        "max_displacement_mm": _g(max_u),
        "bbox_diagonal_mm": _g(diagonal),
        "drawn_max_displacement_mm": _g(factor * max_u),
        "rule": f"positions = node + factor x u; 'auto' makes factor x (largest |u|) = {AUTO_DEFORMATION_FRACTION:.0%} of the bounding-box diagonal of the mesh nodes",
    }
    node_extras = {
        "tool": "fem_view",
        "tool_version": __version__,
        "field": spec.name,
        "field_label": spec.label,
        "unit": unit,
        "unit_symbol": symbol,
        "colormap": cmap,
        "colormap_source": COLORMAP_SOURCES[cmap],
        "colormap_table_srgb": [[float(c) for c in row] for row in table],
        "colormap_rule": "t = (value - low) / (high - low) clamped to [0, 1]; colour = linear interpolation of the sRGB table at t (65 points)",
        "range": range_info,
        "legend": legend,
        "deformation": deformation,
        "notes": notes,
        "units": {"model_unit": "millimetre", "unit": "metre", "up_axis": "+Y", "conversion": CONVERSION_TEXT},
        "attributes": {
            "COLOR_0": "float32 linear RGB of the colour map (the table is sRGB; COLOR_0 = sRGB transfer undone)",
            "_FIELD": f"float32 nodal value of {spec.name} in {unit}",
            "_NODE": "float32 1-based node number of results.csv",
        },
        "source_sha256": dict(sorted(source.files.items())),
        "fem_static_job_id": source.job_id,
    }
    surface_extras = {
        "kind": "surface",
        "facets": int(len(surface.facets.corners)),
        "sub_triangles_per_facet": 4,
        "feature_edges": int(surface.feature.sum()),
        "meaning": "boundary of the tetrahedral mesh; every six-node facet as four flat triangles through its midside nodes",
    }
    deadline("write")
    positions = to_gltf(drawn_nodes[surface.vertex_node])
    normals_model = vertex_normals(surface, drawn_nodes)
    normals = np.stack([normals_model[:, 0], normals_model[:, 2], -normals_model[:, 1]], axis=1)
    node_ids = (surface.vertex_node + 1).astype(np.float64)
    for array in (positions, normals, linear, label_values):
        if not np.all(np.isfinite(array)):
            raise _failure("input.out_of_range", "/output_unit", "a value is not finite after conversion")
    payload = glb_bytes(
        name=title,
        positions=positions,
        normals=normals,
        colours=linear,
        field=label_values,
        node_ids=node_ids,
        indices=surface.triangles.reshape(-1),
        lines=lines,
        surface_extras=surface_extras,
        node_extras=node_extras,
    )
    glb_artifact = job.write_bytes(GLB_NAME, payload, fmt="glb")
    back = read_back(Path(glb_artifact.path).read_bytes())

    # ---- read-back checks: every number below comes from the file's bytes
    expected_triangles = 4 * len(surface.facets.corners)
    problems = []
    if len(back.triangles) != expected_triangles:
        problems.append(f"{len(back.triangles)} triangles instead of {expected_triangles}")
    if not np.array_equal(back.positions, positions.astype("<f4").astype(np.float64)):
        problems.append("positions")
    if not np.array_equal(back.field, label_values.astype("<f4").astype(np.float64)) or not np.array_equal(back.node_ids, node_ids):
        problems.append("field values or node numbers")
    if not (np.all(np.isfinite(back.colours)) and back.colours.min() >= 0.0 and back.colours.max() <= 1.0):
        problems.append("colours outside [0, 1]")
    for line in lines:
        if back.line_segments.get(line.kind) != len(line.indices) // 2:
            problems.append(f"{line.kind} segments")
    if problems:
        raise ToolFailure("fem.readback_mismatch", "the GLB read back differently: " + ", ".join(problems), {"param": "/result_dir"})
    field_min, field_max = float(back.field.min()), float(back.field.max())
    triangle_count = int(len(back.triangles))
    shown_volume = enclosed_volume(to_model(back.positions), back.triangles)

    # ---- the legend and view.json
    legend_notes = list(notes)
    legend_notes.append(f"Surface min {field_min:.6g}, max {field_max:.6g} {symbol}")
    legend_notes.append("Nodal values (CalculiX: extrapolated, averaged)")
    svg = legend_svg(name=title, label=spec.label, unit=symbol, table=table, lo=lo * per_base, hi=hi * per_base, notes=legend_notes)
    svg_artifact = job.write_text(LEGEND_NAME, svg, fmt="svg")

    # The extremes over all nodes, preferring a surface node when one ties: an interior extreme is reported only when it
    # exceeds the surface's beyond float32 resolution (the precision of the values in the GLB).
    i_all_max, i_all_min = int(np.argmax(values_all)), int(np.argmin(values_all))
    on_surface = np.zeros(len(tetra.nodes), dtype=bool)
    on_surface[surface_nodes] = True
    surface_max_node, surface_min_node = int(surface_nodes[np.argmax(shown)]), int(surface_nodes[np.argmin(shown)])
    if not beyond_float32(values_all[i_all_max], values_all[surface_max_node]):
        i_all_max = surface_max_node
    if not beyond_float32(values_all[surface_min_node], values_all[i_all_min]):
        i_all_min = surface_min_node
    statistics = {
        "surface": {
            "min": _g(field_min, 7),
            "max": _g(field_max, 7),
            "mean": _g(float(shown.mean()) * per_base),
            "nodes": int(len(surface_nodes)),
            "from": "the _FIELD attribute read back from the GLB (float32, 7 digits)",
        },
        "all_nodes": {
            "min": _g(float(values_all[i_all_min]) * per_base),
            "min_node": i_all_min + 1,
            "min_on_surface": bool(on_surface[i_all_min]),
            "max": _g(float(values_all[i_all_max]) * per_base),
            "max_node": i_all_max + 1,
            "max_on_surface": bool(on_surface[i_all_max]),
            "nodes": int(len(tetra.nodes)),
        },
    }
    for which, index in (("max", i_all_max), ("min", i_all_min)):
        if not on_surface[index]:
            warnings.append(
                f"the {which}imum of {spec.name} over all nodes ({float(values_all[index]) * per_base:.6g} {symbol}, node {index + 1}) is inside the part "
                "and is not visible on the surface; the GLB shows only surface values"
            )
    comparison = {}
    for key, fname in (("max_von_mises", "von_mises"), ("max_displacement", "displacement")):
        if spec.name == fname and key in source.static_values:
            given = source.static_values[key]
            try:
                reported = float(ureg.Quantity(given["quantity"], given["unit"]).to(unit).magnitude)
            except Exception:  # noqa: BLE001 - a unit text from the file that pint does not know: no comparison
                continue
            agrees = math.isclose(reported, float(values_all[i_all_max]) * per_base, rel_tol=1e-6, abs_tol=1e-12)
            comparison[key] = {"fem_static": _g(reported), "unit": unit, "agrees_with_all_nodes_max": agrees}
            if not agrees:
                warnings.append(f"fem_static reported {key} = {reported:.7g} {symbol}, but results.csv gives {float(values_all[i_all_max]) * per_base:.7g}")
    mesh_volume = None
    try:
        _, mesh_volume = meshing.check_and_measure(tetra)
    except ToolFailure:
        warnings.append("the mesh volume could not be measured (an element with a non-positive Jacobian); the closed-surface check is skipped")
    counts = {
        "boundary_facets": int(len(surface.facets.corners)),
        "triangles": triangle_count,
        "vertices": int(len(back.positions)),
        "surface_nodes": int(len(surface_nodes)),
        "mesh_nodes": int(len(tetra.nodes)),
        "elements": int(len(tetra.tets)),
        "feature_edges": int(surface.feature.sum()),
        "line_segments": back.line_segments,
    }
    losses = _losses(spec, factor, outline_drawn)
    view_doc = {
        "tool": "fem_view",
        "tool_version": __version__,
        "glb": GLB_NAME,
        "glb_sha256": glb_artifact.sha256,
        "legend_svg": LEGEND_NAME,
        "field": spec.name,
        "field_label": spec.label,
        "unit": unit,
        "colormap": cmap,
        "colormap_source": COLORMAP_SOURCES[cmap],
        "statistics": statistics,
        "range": range_info,
        "deformation": deformation,
        "counts": counts,
        "legend": legend,
        "notes": notes,
        "source_sha256": node_extras["source_sha256"],
        "fem_static_job_id": source.job_id,
        "units": node_extras["units"],
    }
    json_artifact = job.write_json(JSON_NAME, view_doc)
    if exaggerated:
        warnings.append(f"the shape is exaggerated: displacements are drawn {factor:.6g} times their size (deformation_factor)")
    if below or above:
        warnings.append(f"{below + above} surface node(s) lie outside the range {lo * per_base:.6g} .. {hi * per_base:.6g} {symbol} and take the end colours")
    data = {
        "field": spec.name,
        "field_label": spec.label,
        "unit": unit,
        "supported_fields": list(FIELDS),
        "colormap": cmap,
        "colormap_source": COLORMAP_SOURCES[cmap],
        "statistics": statistics,
        "fem_static_comparison": comparison,
        "range": range_info,
        "deformation": deformation,
        "legend": legend,
        "counts": counts,
        "closed_surface": {
            "enclosed_volume_mm3_undeformed": _g(undeformed_volume),
            "enclosed_volume_mm3_drawn": _g(shown_volume),
            "mesh_volume_mm3": None if mesh_volume is None else _g(mesh_volume),
            "rule": "signed volume of the drawn triangles (divergence theorem); equals the mesh volume where the facets are flat",
        },
        "source": {"mode": source.mode, "files_sha256": node_extras["source_sha256"], "fem_static_job_id": source.job_id},
        "output": {
            "glb": GLB_NAME,
            "glb_sha256": glb_artifact.sha256,
            "legend_svg": LEGEND_NAME,
            "view_json": JSON_NAME,
            "accessor_min_m": back.document["accessors"][back.document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]["min"],
            "accessor_max_m": back.document["accessors"][back.document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]["max"],
            "unit": "metre",
            "up_axis": "+Y",
            "conversion": CONVERSION_TEXT,
            "vertex_colours": "COLOR_0 float32 linear RGB",
            "material": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0], "metallicFactor": METALLIC, "roughnessFactor": ROUGHNESS, "doubleSided": False},
        },
        "limits": {"max_triangles": MAX_TRIANGLES, "max_elements": meshing.MAX_ELEMENTS, "max_nodes": meshing.MAX_NODES, "max_deformation_scale": MAX_DEFORMATION_SCALE},
        "losses": losses,
    }
    values = {
        "triangle_count": _count(triangle_count),
        "field_max": _q(field_max, unit),
        "field_min": _q(field_min, unit),
        "deformation_factor": QuantityOut(quantity=float(factor), unit="dimensionless"),
    }
    deadline("write")
    return Computed(values=values, primary="triangle_count", warnings=warnings, data=data, artifacts=[glb_artifact, svg_artifact, json_artifact])


def _line_primitive(kind: str, colour: str, edges: np.ndarray, nodes_mm: np.ndarray, meaning: str) -> LinePrimitive:
    """Each curved facet edge corner - mid - corner as two segments, on compact positions."""
    used, inverse = np.unique(edges.reshape(-1), return_inverse=True)
    local = inverse.reshape(-1, 3)
    segments = np.concatenate([local[:, [0, 1]], local[:, [1, 2]]], axis=0)
    return LinePrimitive(kind, colour, to_gltf(nodes_mm[used]), segments.reshape(-1), meaning)


def _losses(spec: Field, factor: float, outline: bool) -> list[dict[str, str]]:
    out = [
        {
            "kind": "nodal_values",
            "detail": "stresses are the nodal values CalculiX writes to the .frd (extrapolated from the four integration points of each element and "
            "averaged between the elements that share a node, six printed digits); they are not integration-point values and are drawn unchanged",
        },
        {"kind": "surface_only", "detail": "only the boundary of the mesh is drawn: interior stresses are not visible, and an interior maximum is reported as a warning"},
        {
            "kind": "singular_points",
            "detail": "at point loads, point supports, sharp re-entrant corners and the edges of fully fixed faces the nodal values grow without "
            "bound with refinement: the colour there is a property of the mesh, not of the part",
        },
        {
            "kind": "interpolation",
            "detail": "each six-node facet is four flat triangles through its midside nodes; the viewer interpolates the vertex colours linearly "
            "on them, which is not the quadratic interpolation of the element (between nodes the colour is approximate)",
        },
        {"kind": "coordinate_precision", "detail": "positions, normals, colours and _FIELD are 32-bit floats (about 7 significant digits)"},
        {"kind": "glb_axes_and_unit", "detail": "GLB is metres and +Y up: (x, y, z) mm -> (x, z, -y) / 1000 m"},
        {
            "kind": "normals",
            "detail": f"normals are the curved facets' normals at each node, averaged over the facets around the node that are joined by edges "
            f"where the normals differ by at most {FEATURE_ANGLE_DEG:g} degrees; across a sharper edge the node has separate vertices and normals",
        },
    ]
    if factor != 0.0:
        out.append(
            {
                "kind": "deformation",
                "detail": f"the shape is drawn with displacements x{factor:.6g}"
                + (" (exaggerated: not the real deformed shape)" if factor > 1.0 else "")
                + "; a shape drawn inside out (negative signed volume) is warned about, a partial self-intersection is not detected"
                + ("; the undeformed feature edges are drawn as lines" if outline else ""),
            }
        )
    if spec.name.startswith("principal"):
        out.append({"kind": "principal", "detail": "principal stresses are the eigenvalues of the nodal stress tensor from results.csv (nine digits), computed here"})
    return out


__all__ = ["COLORMAPS", "COLORMAP_SOURCES", "FIELDS", "TABLES", "Deadline", "colours_srgb", "legend_svg", "pointer", "read_back", "view"]
