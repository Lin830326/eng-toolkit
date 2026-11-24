"""A picture of a 3D model that a person can paste into a mail: a PNG written from numpy alone.

``geometry_view`` and ``fem_view`` write a GLB, which most people cannot open.  This module draws the same triangles, with the same
colours, into a PNG sheet: an isometric view (and, for a part, small front, top and right views), a legend or colour bar with units
and the value range, and the size of the model in millimetres.  There is no dependency beyond numpy: the rasteriser is a
z-buffer over triangles (orthographic camera, supersampled for anti-aliasing), the text is a 5 x 9 bitmap font defined below, and
the PNG is encoded with :mod:`zlib` and :mod:`struct`.  Same input, same bytes: nothing here reads a clock, a random number or a
font installed on the machine.

Conventions: model axes (x, y, z) in millimetres with +Z up.  The isometric camera looks from (+x, -y, +z); front looks along +y
from -y, top looks down from +z, right looks along -x from +x (screen right is +x, +x, +y).  The colours of a surface are the
colours of the GLB (sRGB), multiplied by a Lambert factor from a fixed light so that the form can be read; the legend swatches are
the exact GLB colours.

The rasteriser (:func:`_rasterise`) turns each triangle into one horizontal span per pixel row, vectorised over all triangles, so
its cost follows the covered area and not the bounding boxes of long thin triangles.  Triangles are processed in chunks of about a
million fragments, so memory stays bounded whatever the mesh size.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "ColourBar",
    "EXTRA_VIEWS",
    "Focus",
    "FocusLabel",
    "LegendItem",
    "LineSet",
    "Marker",
    "Mesh",
    "PreviewResult",
    "SheetSpec",
    "VIEW_NAMES",
    "draw_text",
    "encode_png",
    "render_sheet",
    "text_width",
]

VIEW_NAMES = ("iso", "front", "top", "right", "iso_back", "iso_under", "iso_under_back")
EXTRA_VIEWS = ("iso_back", "iso_under", "iso_under_back")  # tried in this order for a marker the first view hides
# name -> direction from the model towards the camera (model axes, +Z up); screen up is +Z, except for the top view (+Y)
_TOWARD = {
    "iso": (1.0, -1.0, 1.0),
    "front": (0.0, -1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "right": (1.0, 0.0, 0.0),
    "iso_back": (-1.0, 1.0, 1.0),
    "iso_under": (1.0, -1.0, -1.0),
    "iso_under_back": (-1.0, 1.0, -1.0),
}
_UP = {name: (0.0, 1.0, 0.0) if name == "top" else (0.0, 0.0, 1.0) for name in VIEW_NAMES}
VIEW_CAPTION = {
    "iso": "ISOMETRIC",
    "front": "FRONT (from -Y)",
    "top": "TOP (from +Z)",
    "right": "RIGHT (from +X)",
    "iso_back": "ISOMETRIC FROM THE BACK (from -X, +Y, +Z)",
    "iso_under": "ISOMETRIC FROM BELOW (from +X, -Y, -Z)",
    "iso_under_back": "ISOMETRIC FROM BELOW, BACK (from -X, +Y, -Z)",
}
LIGHT = (-0.25, 0.45, 0.85)  # in camera space: x right, y up, z towards the viewer
MAX_FRAGMENTS = 1_000_000  # fragments held at once
MAX_PAIRS = 2_000_000  # (triangle, pixel row) pairs held at once
MAX_CANVAS = (2400, 3200)
CROP_MARGIN = 0.2  # a close-up keeps this fraction of the framed size free on each side
CROP_MIN_FRACTION = 0.01  # ... but the framed size is at least this fraction of the model's diagonal (a flat face has no depth)
BACKGROUND = (255, 255, 255)
PANEL_BORDER = (200, 204, 210)
INK = (24, 24, 28)
MUTED = (84, 88, 96)
EDGE_RGB = (26, 26, 26)

Check = Callable[[str], None]


# --------------------------------------------------------------------------- what to draw


@dataclass(frozen=True)
class LineSet:
    segments: np.ndarray  # (s, 2, 3) float64 model mm
    rgb: tuple[float, float, float]  # sRGB 0..1
    width: float = 1.0  # in final pixels


@dataclass(frozen=True)
class Marker:
    point_mm: np.ndarray  # (3,)
    label: str
    rgb: tuple[int, int, int] = (0, 0, 0)
    draw_hidden: bool = False  # a marker no view can see is drawn thin in the isometric panel (a point inside the material)


@dataclass(frozen=True)
class FocusLabel:
    point_mm: np.ndarray  # (3,) on the surface of a face, or (k, 3): candidates, the first one the view can see is used
    text: str  # e.g. "F12"


@dataclass(frozen=True)
class Focus:
    """A close-up panel under the other views: the model framed on ``box_mm`` (plus :data:`CROP_MARGIN` of its size on each
    side), with a scale bar in millimetres and the ``labels`` of the faces that can be seen.  The view is the isometric one
    (from above, from behind, from below or from below behind) in which most pixels belong to ``triangles``."""

    box_mm: np.ndarray  # (2, 3): min and max corner of the faces to enlarge
    triangles: np.ndarray  # indices into Mesh.triangles of the triangles of those faces
    caption: str = "CLOSE-UP"
    labels: tuple[FocusLabel, ...] = ()


@dataclass(frozen=True)
class Mesh:
    """Triangles in model millimetres, counter-clockwise seen from outside.  ``normals`` are unit vertex normals (model axes).
    Colour comes from ``face_rgb`` (one sRGB 0..1 colour per triangle) or from ``vertex_rgb`` (per vertex, interpolated).
    ``tri_group`` (optional) names the legend group of each triangle so the picture can report which groups are visible."""

    vertices: np.ndarray
    triangles: np.ndarray
    normals: np.ndarray
    face_rgb: np.ndarray | None = None
    vertex_rgb: np.ndarray | None = None
    tri_group: np.ndarray | None = None
    lines: tuple[LineSet, ...] = ()
    markers: tuple[Marker, ...] = ()
    shade_strength: float = 0.45  # 0 = flat colours, 1 = black where the surface faces away from the light


@dataclass(frozen=True)
class LegendItem:
    label: str
    rgb: tuple[int, int, int]  # sRGB 0..255
    detail: str = ""
    kind: str = "swatch"  # "swatch" or "line"


@dataclass(frozen=True)
class ColourBar:
    heading: str  # e.g. "von Mises stress"
    unit: str
    colours: np.ndarray  # (k, 3) sRGB 0..1, low end first
    ticks: tuple[tuple[float, str], ...]  # (position 0..1, label)
    lines: tuple[str, ...] = ()  # caption lines under the bar


@dataclass(frozen=True)
class SheetSpec:
    title: str
    subtitle: tuple[str, ...] = ()
    legend: tuple[LegendItem, ...] = ()
    legend_heading: str = "Colours"
    colourbar: ColourBar | None = None
    info: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    views: tuple[str, ...] = ("iso",)  # 'iso' first, then any of 'front', 'top', 'right' as small views
    back_view_for_markers: bool = False  # add an isometric view from the back when a marker is hidden in the first one
    focus: Focus | None = None  # a close-up panel below the views
    ss: int = 0  # supersampling per axis; 0 = chosen from the triangle count


@dataclass(frozen=True)
class PreviewResult:
    png: bytes
    width: int
    height: int
    views: tuple[str, ...]
    supersampling: int
    group_pixels: dict[int, int]  # legend group index -> pixels it covers in the isometric view (full resolution)
    visible_pixels: int  # pixels the model covers in the isometric view
    markers_visible: tuple[bool, ...]  # per marker: seen in some view
    markers_view: tuple[str | None, ...]  # per marker: the first view that shows it
    group_pixels_all: dict[int, int]  # legend group index -> pixels in all views together
    size_mm: tuple[float, float, float]
    # per marker: (view, x, y, drawn_hidden) with the pixel of the ring centre in the PNG, or None when it was not drawn
    markers_px: tuple[tuple[str, float, float, bool] | None, ...] = ()
    focus: dict[str, Any] | None = None  # the close-up: view, zoom, scale, scale bar, panel rectangle, labels drawn


# --------------------------------------------------------------------------- the bitmap font

# Each glyph is (first row, rows); rows are 5 characters, '#' = ink.  Cell: 5 wide, 9 tall; capitals and digits on rows 0..6,
# lower-case x-height on rows 2..6, descenders on rows 7..8.  Drawn from scratch for this project.
_GLYPHS: dict[str, tuple[int, str]] = {
    " ": (0, "....."),
    "0": (0, ".###./#...#/#..##/#.#.#/##..#/#...#/.###."),
    "1": (0, "..#../.##../..#../..#../..#../..#../.###."),
    "2": (0, ".###./#...#/....#/...#./..#../.#.../#####"),
    "3": (0, ".###./#...#/....#/..##./....#/#...#/.###."),
    "4": (0, "...#./..##./.#.#./#..#./#####/...#./...#."),
    "5": (0, "#####/#..../####./....#/....#/#...#/.###."),
    "6": (0, "..##./.#.../#..../####./#...#/#...#/.###."),
    "7": (0, "#####/....#/...#./..#../.#.../.#.../.#..."),
    "8": (0, ".###./#...#/#...#/.###./#...#/#...#/.###."),
    "9": (0, ".###./#...#/#...#/.####/....#/...#./.##.."),
    "A": (0, ".###./#...#/#...#/#####/#...#/#...#/#...#"),
    "B": (0, "####./#...#/#...#/####./#...#/#...#/####."),
    "C": (0, ".###./#...#/#..../#..../#..../#...#/.###."),
    "D": (0, "####./#...#/#...#/#...#/#...#/#...#/####."),
    "E": (0, "#####/#..../#..../####./#..../#..../#####"),
    "F": (0, "#####/#..../#..../####./#..../#..../#...."),
    "G": (0, ".###./#...#/#..../#.###/#...#/#...#/.###."),
    "H": (0, "#...#/#...#/#...#/#####/#...#/#...#/#...#"),
    "I": (0, ".###./..#../..#../..#../..#../..#../.###."),
    "J": (0, "..###/...#./...#./...#./...#./#..#./.##.."),
    "K": (0, "#...#/#..#./#.#../##.../#.#../#..#./#...#"),
    "L": (0, "#..../#..../#..../#..../#..../#..../#####"),
    "M": (0, "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#"),
    "N": (0, "#...#/##..#/#.#.#/#..##/#...#/#...#/#...#"),
    "O": (0, ".###./#...#/#...#/#...#/#...#/#...#/.###."),
    "P": (0, "####./#...#/#...#/####./#..../#..../#...."),
    "Q": (0, ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#"),
    "R": (0, "####./#...#/#...#/####./#.#../#..#./#...#"),
    "S": (0, ".####/#..../#..../.###./....#/....#/####."),
    "T": (0, "#####/..#../..#../..#../..#../..#../..#.."),
    "U": (0, "#...#/#...#/#...#/#...#/#...#/#...#/.###."),
    "V": (0, "#...#/#...#/#...#/#...#/#...#/.#.#./..#.."),
    "W": (0, "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#"),
    "X": (0, "#...#/#...#/.#.#./..#../.#.#./#...#/#...#"),
    "Y": (0, "#...#/#...#/.#.#./..#../..#../..#../..#.."),
    "Z": (0, "#####/....#/...#./..#../.#.../#..../#####"),
    "a": (2, ".###./....#/.####/#...#/.####"),
    "b": (0, "#..../#..../####./#...#/#...#/#...#/####."),
    "c": (2, ".###./#...#/#..../#...#/.###."),
    "d": (0, "....#/....#/.####/#...#/#...#/#...#/.####"),
    "e": (2, ".###./#...#/#####/#..../.###."),
    "f": (0, "..##./.#..#/.#.../###../.#.../.#.../.#..."),
    "g": (2, ".####/#...#/#...#/#...#/.####/....#/.###."),
    "h": (0, "#..../#..../#.##./##..#/#...#/#...#/#...#"),
    "i": (0, "..#../...../.##../..#../..#../..#../.###."),
    "j": (0, "...#./...../..##./...#./...#./...#./...#./#..#./.##.."),
    "k": (0, "#..../#..../#..#./#.#../##.../#.#../#..#."),
    "l": (0, ".##../..#../..#../..#../..#../..#../.###."),
    "m": (2, "##.#./#.#.#/#.#.#/#.#.#/#.#.#"),
    "n": (2, "#.##./##..#/#...#/#...#/#...#"),
    "o": (2, ".###./#...#/#...#/#...#/.###."),
    "p": (2, "####./#...#/#...#/#...#/####./#..../#...."),
    "q": (2, ".####/#...#/#...#/#...#/.####/....#/....#"),
    "r": (2, "#.##./##..#/#..../#..../#...."),
    "s": (2, ".####/#..../.###./....#/####."),
    "t": (1, ".#.../###../.#.../.#.../.#..#/..##."),
    "u": (2, "#...#/#...#/#...#/#..##/.##.#"),
    "v": (2, "#...#/#...#/#...#/.#.#./..#.."),
    "w": (2, "#...#/#...#/#.#.#/#.#.#/.#.#."),
    "x": (2, "#...#/.#.#./..#../.#.#./#...#"),
    "y": (2, "#...#/#...#/#...#/#...#/.####/....#/.###."),
    "z": (2, "#####/...#./..#../.#.../#####"),
    ".": (5, ".##../.##.."),
    ",": (5, ".##../.##../..#../.#.."),
    ":": (2, ".##../.##../...../.##../.##.."),
    ";": (2, ".##../.##../...../.##../..#../.#.."),
    "-": (3, ".###."),
    "_": (8, "#####"),
    "+": (2, "..#../..#../#####/..#../..#.."),
    "=": (3, "#####/...../#####"),
    "/": (0, "....#/....#/...#./..#../.#.../#..../#...."),
    "\\": (0, "#..../#..../.#.../..#../...#./....#/....#"),
    "(": (0, "...#./..#../.#.../.#.../.#.../..#../...#."),
    ")": (0, ".#.../..#../...#./...#./...#./..#../.#..."),
    "[": (0, ".###./.#.../.#.../.#.../.#.../.#.../.###."),
    "]": (0, ".###./...#./...#./...#./...#./...#./.###."),
    "{": (0, "..##./.#.../.#.../##.../.#.../.#.../..##."),
    "}": (0, "##.../...#./...#./...##/...#./...#./##..."),
    "%": (0, "##..#/##..#/...#./..#../.#.../#..##/#..##"),
    "<": (1, "...#./..#../.#.../..#../...#."),
    ">": (1, ".#.../..#../...#./..#../.#..."),
    "*": (1, "..#../#.#.#/.###./#.#.#/..#.."),
    "#": (0, ".#.#./#####/.#.#./.#.#./#####/.#.#./.#.#."),
    "!": (0, "..#../..#../..#../..#../..#../...../..#.."),
    "?": (0, ".###./#...#/....#/...#./..#../...../..#.."),
    '"': (0, ".#.#./.#.#./.#.#."),
    "'": (0, "..#../..#../.#..."),
    "`": (0, ".#.../..#.."),
    "^": (0, "..#../.#.#./#...#"),
    "~": (3, ".##.#/#.##."),
    "@": (0, ".###./#...#/#.###/#.#.#/#.###/#..../.###."),
    "&": (0, ".##../#..#./#.#../.#.../#.#.#/#..#./.##.#"),
    "$": (0, "..#../.####/#.#../.###./..#.#/####./..#.."),
    "|": (0, "..#../..#../..#../..#../..#../..#../..#.."),
}
_TRANSLITERATE = {
    "µ": "u", "μ": "u", "·": "*", "×": "x", "±": "+/-", "°": " deg", "²": "^2", "³": "^3",
    "–": "-", "—": "-", "−": "-", "≤": "<=", "≥": ">=", "’": "'", "‘": "'", "“": '"', "”": '"',
    "\t": " ", "\n": " ", "\r": " ",
}
GLYPH_W, GLYPH_H, GLYPH_ADVANCE = 5, 9, 6


def _glyph_bitmap(rows: tuple[int, str]) -> np.ndarray:
    first, text = rows
    out = np.zeros((GLYPH_H, GLYPH_W), dtype=bool)
    for k, row in enumerate(text.split("/")):
        out[first + k] = [c == "#" for c in row.ljust(GLYPH_W, ".")[:GLYPH_W]]
    return out


_BITMAPS: dict[str, np.ndarray] = {ch: _glyph_bitmap(spec) for ch, spec in _GLYPHS.items()}


def _plain(text: str) -> str:
    out = []
    for ch in text:
        ch = _TRANSLITERATE.get(ch, ch)
        out.extend(c if c in _BITMAPS else "?" for c in ch)
    return "".join(out)


def text_width(text: str, scale: int = 2) -> int:
    n = len(_plain(text))
    return max(0, n * GLYPH_ADVANCE - 1) * scale


def _wrap(text: str, width_px: int, scale: int) -> list[str]:
    """Words of ``text`` broken into lines no wider than ``width_px`` (a word wider than the line is cut)."""
    per_line = max(4, (width_px // scale + 1) // GLYPH_ADVANCE)
    lines: list[str] = []
    current = ""
    for word in _plain(text).split(" "):
        while len(word) > per_line:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:per_line])
            word = word[per_line:]
        trial = word if not current else current + " " + word
        if len(trial) <= per_line:
            current = trial
        else:
            lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    return lines


def draw_text(img: np.ndarray, x: int, y: int, text: str, rgb: Sequence[int], scale: int = 2) -> int:
    """Ink ``text`` with its top-left corner at (x, y); returns the pixel width drawn.  Clipped to the image."""
    text = _plain(text)
    if not text:
        return 0
    cells = np.zeros((GLYPH_H, len(text) * GLYPH_ADVANCE), dtype=bool)
    for k, ch in enumerate(text):
        cells[:, k * GLYPH_ADVANCE : k * GLYPH_ADVANCE + GLYPH_W] = _BITMAPS[ch]
    mask = np.kron(cells, np.ones((scale, scale), dtype=bool))
    h, w = mask.shape
    x0, y0 = max(0, -x), max(0, -y)
    x1, y1 = min(w, img.shape[1] - x), min(h, img.shape[0] - y)
    if x1 > x0 and y1 > y0:
        region = img[y + y0 : y + y1, x + x0 : x + x1]
        region[mask[y0:y1, x0:x1]] = np.asarray(rgb, dtype=np.uint8)
    return (len(text) * GLYPH_ADVANCE - 1) * scale


# --------------------------------------------------------------------------- the camera


def _basis(view: str) -> np.ndarray:
    """Rows: screen right, screen up, towards the camera (model axes)."""
    toward = np.asarray(_TOWARD[view], dtype=np.float64)
    toward /= np.linalg.norm(toward)
    up = np.asarray(_UP[view], dtype=np.float64)
    up = up - toward * float(up @ toward)
    up /= np.linalg.norm(up)
    right = np.cross(up, toward)
    return np.stack([right, up, toward])


# --------------------------------------------------------------------------- the rasteriser


def _expand(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For ``counts = [2, 0, 3]``: owner ``[0, 0, 2, 2, 2]`` and offset ``[0, 1, 0, 1, 2]``."""
    total = int(counts.sum())
    owner = np.repeat(np.arange(len(counts), dtype=np.int64), counts)
    start = np.cumsum(counts) - counts
    return owner, np.arange(total, dtype=np.int64) - np.repeat(start, counts)


def _split(weights: np.ndarray, limit: int) -> list[tuple[int, int]]:
    """Consecutive index ranges whose summed weight stays near ``limit`` (a single heavier item gets a range of its own)."""
    cumulative = np.cumsum(weights, dtype=np.int64)
    ranges: list[tuple[int, int]] = []
    start, base = 0, 0
    n = len(weights)
    while start < n:
        end = int(np.searchsorted(cumulative, base + limit, side="right"))
        end = min(max(end, start + 1), n)
        ranges.append((start, end))
        base = int(cumulative[end - 1])
        start = end
    return ranges


@dataclass
class _Buffers:
    width: int
    height: int
    z: np.ndarray  # float32, larger = closer; -inf = empty
    tri: np.ndarray  # int32 index into the triangles given to _rasterise; -1 = empty
    b1: np.ndarray  # float32 barycentric weight of the second corner
    b2: np.ndarray  # float32 barycentric weight of the third corner


def _rasterise(
    sx: np.ndarray, sy: np.ndarray, depth: np.ndarray, tris: np.ndarray, width: int, height: int, check: Check | None
) -> _Buffers:
    """Z-buffer ``tris`` (indices into sx, sy, depth; pixel units, y down, depth in [0, 1], larger = closer).  Triangles that face
    away (clockwise on screen once y points down is the front) are dropped: the meshes are closed and counter-clockwise outside."""
    buf = _Buffers(
        width,
        height,
        np.full(width * height, -np.inf, dtype=np.float32),
        np.full(width * height, -1, dtype=np.int32),
        np.zeros(width * height, dtype=np.float32),
        np.zeros(width * height, dtype=np.float32),
    )
    if len(tris) == 0:
        return buf
    x = [sx[tris[:, k]] for k in range(3)]
    y = [sy[tris[:, k]] for k in range(3)]
    z = [depth[tris[:, k]] for k in range(3)]
    det = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
    ymin = np.minimum(np.minimum(y[0], y[1]), y[2])
    ymax = np.maximum(np.maximum(y[0], y[1]), y[2])
    xmin = np.minimum(np.minimum(x[0], x[1]), x[2])
    xmax = np.maximum(np.maximum(x[0], x[1]), x[2])
    keep = (det < -1e-12) & (ymax > 0) & (ymin < height) & (xmax > 0) & (xmin < width)
    ids = np.flatnonzero(keep)
    if len(ids) == 0:
        return buf
    x = [a[ids] for a in x]
    y = [a[ids] for a in y]
    z = [a[ids] for a in z]
    det = det[ids]
    row0 = np.maximum(np.ceil(ymin[ids] - 0.5), 0).astype(np.int64)
    row1 = np.minimum(np.ceil(ymax[ids] - 0.5) - 1, height - 1).astype(np.int64)
    n_rows = np.maximum(row1 - row0 + 1, 0)
    # plane equations: l1 = a1 x + b1 y + k1, l2 = a2 x + b2 y + k2, depth = az x + bz y + kz
    a1, b1 = (y[2] - y[0]) / det, -(x[2] - x[0]) / det
    k1 = (-x[0] * (y[2] - y[0]) + (x[2] - x[0]) * y[0]) / det
    a2, b2 = -(y[1] - y[0]) / det, (x[1] - x[0]) / det
    k2 = (x[0] * (y[1] - y[0]) - (x[1] - x[0]) * y[0]) / det
    d1, d2 = z[1] - z[0], z[2] - z[0]
    az, bz = a1 * d1 + a2 * d2, b1 * d1 + b2 * d2
    kz = z[0] + k1 * d1 + k2 * d2

    def edge_x(xa, ya, xb, yb, yc):
        swap = (yb < ya) | ((yb == ya) & (xb < xa))
        xa, ya, xb, yb = np.where(swap, xb, xa), np.where(swap, yb, ya), np.where(swap, xa, xb), np.where(swap, ya, yb)
        inside = (yc >= ya) & (yc < yb)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = xa + (yc - ya) * (xb - xa) / (yb - ya)
        return inside, xi

    for lo, hi in _split(n_rows, MAX_PAIRS):
        if check is not None:
            check("preview")
        owner, offset = _expand(n_rows[lo:hi])
        t = owner + lo
        yrow = row0[t] + offset
        yc = yrow + 0.5
        left = np.full(len(t), np.inf)
        right = np.full(len(t), -np.inf)
        for p, q in ((0, 1), (1, 2), (2, 0)):
            inside, xi = edge_x(x[p][t], y[p][t], x[q][t], y[q][t], yc)
            left = np.where(inside, np.minimum(left, xi), left)
            right = np.where(inside, np.maximum(right, xi), right)
        ok = np.isfinite(left) & np.isfinite(right)
        c0 = np.maximum(np.ceil(np.where(ok, left, 0.0) - 0.5), 0).astype(np.int64)
        c1 = np.minimum(np.ceil(np.where(ok, right, 0.0) - 0.5) - 1, width - 1).astype(np.int64)
        n_cols = np.where(ok, np.maximum(c1 - c0 + 1, 0), 0)
        live = np.flatnonzero(n_cols > 0)
        if len(live) == 0:
            continue
        t, yc, yrow, c0, n_cols = t[live], yc[live], yrow[live], c0[live], n_cols[live]
        xc = c0 + 0.5
        base1, base2, basez = a1[t] * xc + b1[t] * yc + k1[t], a2[t] * xc + b2[t] * yc + k2[t], az[t] * xc + bz[t] * yc + kz[t]
        step1, step2, stepz = a1[t], a2[t], az[t]
        for flo, fhi in _split(n_cols, MAX_FRAGMENTS):
            if check is not None:
                check("preview")
            pair, k = _expand(n_cols[flo:fhi])
            pair += flo
            kf = k.astype(np.float64)
            fz = basez[pair] + stepz[pair] * kf
            pix = yrow[pair] * width + (c0[pair] + k)
            # nearest fragment per pixel: sort by (pixel, -depth)
            key = pix.astype(np.float64) * 2.0 + (1.0 - np.clip(fz, 0.0, 1.0))
            order = np.argsort(key, kind="stable")
            pix_sorted = pix[order]
            first = np.ones(len(order), dtype=bool)
            first[1:] = pix_sorted[1:] != pix_sorted[:-1]
            take = order[first]
            pix_t, z_t = pix[take], fz[take].astype(np.float32)
            closer = z_t > buf.z[pix_t]
            take, pix_t, z_t = take[closer], pix_t[closer], z_t[closer]
            buf.z[pix_t] = z_t
            pair_t = pair[take]
            buf.tri[pix_t] = ids[t[pair_t]].astype(np.int32)
            kt = k[take].astype(np.float64)
            buf.b1[pix_t] = (base1[pair_t] + step1[pair_t] * kt).astype(np.float32)
            buf.b2[pix_t] = (base2[pair_t] + step2[pair_t] * kt).astype(np.float32)
    return buf


def _srgb_pack(rgb01: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(np.asarray(rgb01, dtype=np.float64) * 255.0), 0, 255).astype(np.uint8)


def _render_view(
    mesh: Mesh, view: str, width: int, height: int, ss: int, check: Check | None, *, margin: int = 10, top: int = 0,
    crop: np.ndarray | None = None, points: Sequence[np.ndarray] = (), focus_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """One view of ``mesh`` as an (height, width, 3) uint8 image on a white panel, plus facts about it.

    ``crop`` (2, 3) frames the view on that box of model space (plus :data:`CROP_MARGIN` on each side) instead of the whole
    model; the depth still spans the whole model.  ``points`` are visibility-tested like the markers (they follow them in
    ``facts['markers']``); ``focus_mask`` (one flag per triangle) counts the pixels those triangles cover."""
    basis = _basis(view)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    screen = verts @ basis.T  # (n, 3): right, up, toward
    lo, hi = screen.min(axis=0), screen.max(axis=0)
    extent = np.maximum(hi - lo, 1e-9)
    fit_lo, fit_extent = lo, extent
    if crop is not None:
        box = np.asarray(crop, dtype=np.float64).reshape(2, 3)
        corners = np.array([[box[i, 0], box[j, 1], box[k, 2]] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
        projected = corners @ basis.T
        diagonal = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        size = np.maximum(projected.max(axis=0) - projected.min(axis=0), CROP_MIN_FRACTION * diagonal)
        centre = (projected.max(axis=0) + projected.min(axis=0)) / 2.0
        fit_extent = size * (1.0 + 2.0 * CROP_MARGIN)
        fit_lo = centre - fit_extent / 2.0
    # the fit uses the surface and keeps ``top`` pixels free for a caption
    scale = min((width - 2 * margin) * ss / fit_extent[0], (height - top - 2 * margin) * ss / fit_extent[1])
    cx = width * ss / 2.0 - (fit_lo[0] + fit_extent[0] / 2.0) * scale
    cy = (top + height) * ss / 2.0 + (fit_lo[1] + fit_extent[1] / 2.0) * scale  # y down: sy = cy - up * scale
    sx = screen[:, 0] * scale + cx
    sy = cy - screen[:, 1] * scale
    depth_range = max(extent[2], 1e-9)
    depth = (screen[:, 2] - lo[2]) / depth_range
    W, H = width * ss, height * ss
    buf = _rasterise(sx, sy, depth, np.asarray(mesh.triangles, dtype=np.int64), W, H, check)

    colour = np.empty((H * W, 3), dtype=np.float32)
    colour[:] = BACKGROUND
    covered = np.flatnonzero(buf.tri >= 0)
    strength = float(mesh.shade_strength)
    light = np.asarray(LIGHT, dtype=np.float64)
    light /= np.linalg.norm(light)
    tris = np.asarray(mesh.triangles, dtype=np.int64)
    normals = np.asarray(mesh.normals, dtype=np.float64)
    groups = np.zeros(0, dtype=np.int64)
    for lo_i, hi_i in ((a, min(a + 600_000, len(covered))) for a in range(0, len(covered), 600_000)):
        if check is not None:
            check("preview")
        pix = covered[lo_i:hi_i]
        t = buf.tri[pix].astype(np.int64)
        w1 = np.clip(buf.b1[pix].astype(np.float64), 0.0, 1.0)
        w2 = np.clip(buf.b2[pix].astype(np.float64), 0.0, 1.0)
        w0 = np.clip(1.0 - w1 - w2, 0.0, 1.0)
        total = w0 + w1 + w2
        w0, w1, w2 = w0 / total, w1 / total, w2 / total
        corner = tris[t]
        n = w0[:, None] * normals[corner[:, 0]] + w1[:, None] * normals[corner[:, 1]] + w2[:, None] * normals[corner[:, 2]]
        nv = n @ basis.T
        length = np.linalg.norm(nv, axis=1)
        length[length < 1e-12] = 1.0
        facing = np.clip((nv / length[:, None]) @ light, 0.0, 1.0)
        shade = (1.0 - strength) + strength * facing
        if mesh.face_rgb is not None:
            base = np.asarray(mesh.face_rgb, dtype=np.float64)[t]
        else:
            vc = np.asarray(mesh.vertex_rgb, dtype=np.float64)
            base = w0[:, None] * vc[corner[:, 0]] + w1[:, None] * vc[corner[:, 1]] + w2[:, None] * vc[corner[:, 2]]
        colour[pix] = (base * shade[:, None] * 255.0).astype(np.float32)
        if mesh.tri_group is not None:
            groups = np.concatenate([groups, np.asarray(mesh.tri_group, dtype=np.int64)[t]])

    # lines, depth-tested against the surface with a small bias that follows the local slope of the depth buffer
    z2d = buf.z.reshape(H, W)
    finite = np.isfinite(z2d)
    slope = np.zeros((H, W), dtype=np.float32)
    if finite.any():
        zf = np.where(finite, z2d, 0.0)
        both_x = finite[:, 1:] & finite[:, :-1]
        both_y = finite[1:, :] & finite[:-1, :]
        dx = np.where(both_x, np.abs(zf[:, 1:] - zf[:, :-1]), 0.0)
        dy = np.where(both_y, np.abs(zf[1:, :] - zf[:-1, :]), 0.0)
        slope[:, :-1] = np.maximum(slope[:, :-1], dx)
        slope[:, 1:] = np.maximum(slope[:, 1:], dx)
        slope[:-1, :] = np.maximum(slope[:-1, :], dy)
        slope[1:, :] = np.maximum(slope[1:, :], dy)
        slope = np.minimum(slope, 0.02)
    zbuf_for_lines = np.where(finite, z2d, -np.inf).reshape(-1).astype(np.float64)
    slope_flat = slope.reshape(-1).astype(np.float64)
    for line in mesh.lines:
        seg = np.asarray(line.segments, dtype=np.float64).reshape(-1, 2, 3)
        if len(seg) == 0:
            continue
        pts = seg.reshape(-1, 3) @ basis.T
        px = pts[:, 0] * scale + cx
        py = cy - pts[:, 1] * scale
        pz = ((pts[:, 2] - lo[2]) / depth_range).reshape(-1, 2)
        p = np.stack([px.reshape(-1, 2)[:, 0], py.reshape(-1, 2)[:, 0]], axis=1)
        q = np.stack([px.reshape(-1, 2)[:, 1], py.reshape(-1, 2)[:, 1]], axis=1)
        stamp = max(1, int(round(line.width * ss)))
        bias = 2.0e-3
        _draw_segments(colour, zbuf_for_lines, slope_flat, W, H, p, q, pz[:, 0], pz[:, 1], np.asarray(line.rgb) * 255.0, stamp, bias, check)

    # markers: is the point visible from here?
    marker_xy: list[tuple[float, float, bool]] = []
    for point in [m.point_mm for m in mesh.markers] + [np.asarray(q) for q in points]:
        pt = np.asarray(point, dtype=np.float64) @ basis.T
        mx, my = pt[0] * scale + cx, cy - pt[1] * scale
        mz = (pt[2] - lo[2]) / depth_range
        ix, iy = int(np.clip(math.floor(mx), 0, W - 1)), int(np.clip(math.floor(my), 0, H - 1))
        window = z2d[max(0, iy - ss) : iy + ss + 1, max(0, ix - ss) : ix + ss + 1]
        best = float(window.max()) if window.size and np.isfinite(window).any() else -np.inf
        marker_xy.append((mx / ss, my / ss, best <= mz + 0.01))

    # downsample: average in a gamma-2 space so thin dark lines keep their weight against a light ground
    img = colour.reshape(H, W, 3)
    if ss > 1:
        sq = (img.astype(np.float64) / 255.0) ** 2
        sq = sq.reshape(height, ss, width, ss, 3).mean(axis=(1, 3))
        out = _srgb_pack(np.sqrt(sq))
    else:
        out = _srgb_pack(img.astype(np.float64) / 255.0)
    facts = {
        "covered_pixels": int(len(covered)) / float(ss * ss),
        "group_counts": {int(g): float(c) / (ss * ss) for g, c in enumerate(np.bincount(groups)) if c} if len(groups) else {},
        "markers": marker_xy,
        "scale": scale / ss,  # pixels per mm
        "focus_pixels": 0.0 if focus_mask is None else float(np.asarray(focus_mask, dtype=bool)[buf.tri[covered]].sum()) / (ss * ss),
        "extent_mm": (float(extent[0]), float(extent[1])),
        "screen_origin": (cx / ss, cy / ss),
    }
    return out, facts


def _draw_segments(colour, zbuf, slope, width, height, p, q, zp, zq, rgb, stamp, bias, check) -> None:
    """Depth-tested lines on the supersampled colour buffer: ``p``, ``q`` are (s, 2) pixel positions of the ends, ``zp``, ``zq``
    their depths.  A sample is hidden only when the surface at its pixel is closer than the line by more than ``bias`` plus twice
    the depth slope there (so a line on a steep face is not eaten by the face it lies on); a pixel without surface shows it."""
    if len(p) == 0:
        return
    length = np.maximum(np.abs(q - p).max(axis=1), 1.0)
    steps = np.ceil(length * 1.5).astype(np.int64) + 1
    for lo, hi in _split(steps, MAX_FRAGMENTS):
        if check is not None:
            check("preview")
        owner, offset = _expand(steps[lo:hi])
        s = owner + lo
        f = offset / np.maximum(steps[s] - 1, 1)
        xs = p[s, 0] + (q[s, 0] - p[s, 0]) * f
        ys = p[s, 1] + (q[s, 1] - p[s, 1]) * f
        zs = zp[s] + (zq[s] - zp[s]) * f
        first = -((stamp - 1) // 2)
        for dy in range(first, first + stamp):
            for dx in range(first, first + stamp):
                ix = np.floor(xs).astype(np.int64) + dx
                iy = np.floor(ys).astype(np.int64) + dy
                inside = (ix >= 0) & (ix < width) & (iy >= 0) & (iy < height)
                ix, iy, zz = ix[inside], iy[inside], zs[inside]
                pix = iy * width + ix
                visible = zz + bias + 2.0 * slope[pix] >= zbuf[pix]
                colour[pix[visible]] = rgb


# --------------------------------------------------------------------------- drawing on the sheet


def _fill(img: np.ndarray, x: int, y: int, w: int, h: int, rgb: Sequence[int]) -> None:
    x0, y0, x1, y1 = max(0, x), max(0, y), min(img.shape[1], x + w), min(img.shape[0], y + h)
    if x1 > x0 and y1 > y0:
        img[y0:y1, x0:x1] = np.asarray(rgb, dtype=np.uint8)


def _frame(img: np.ndarray, x: int, y: int, w: int, h: int, rgb: Sequence[int]) -> None:
    _fill(img, x, y, w, 1, rgb)
    _fill(img, x, y + h - 1, w, 1, rgb)
    _fill(img, x, y, 1, h, rgb)
    _fill(img, x + w - 1, y, 1, h, rgb)


def _blend(img: np.ndarray, x0: int, y0: int, coverage: np.ndarray, rgb: Sequence[int]) -> None:
    h, w = coverage.shape
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(img.shape[1], x0 + w), min(img.shape[0], y0 + h)
    if xb <= xa or yb <= ya:
        return
    cov = coverage[ya - y0 : yb - y0, xa - x0 : xb - x0, None]
    region = img[ya:yb, xa:xb].astype(np.float64)
    img[ya:yb, xa:xb] = np.rint(region * (1.0 - cov) + np.asarray(rgb, dtype=np.float64) * cov).astype(np.uint8)


def _segment(img: np.ndarray, a: tuple[float, float], b: tuple[float, float], rgb: Sequence[int], width: float = 2.0) -> None:
    """An anti-aliased line of ``width`` pixels (distance to the segment decides the coverage)."""
    pad = int(math.ceil(width)) + 2
    x0, y0 = int(math.floor(min(a[0], b[0]))) - pad, int(math.floor(min(a[1], b[1]))) - pad
    x1, y1 = int(math.ceil(max(a[0], b[0]))) + pad, int(math.ceil(max(a[1], b[1]))) + pad
    gy, gx = np.mgrid[y0:y1, x0:x1]
    px, py = gx + 0.5 - a[0], gy + 0.5 - a[1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = np.clip((px * dx + py * dy) / length2, 0.0, 1.0) if length2 > 0 else np.zeros_like(px)
    dist = np.hypot(px - t * dx, py - t * dy)
    _blend(img, x0, y0, np.clip(width / 2.0 + 0.5 - dist, 0.0, 1.0), rgb)


def _ring(img: np.ndarray, centre: tuple[float, float], radius: float, rgb: Sequence[int], width: float = 3.0) -> None:
    pad = int(math.ceil(radius + width)) + 2
    x0, y0 = int(math.floor(centre[0])) - pad, int(math.floor(centre[1])) - pad
    gy, gx = np.mgrid[y0 : y0 + 2 * pad + 1, x0 : x0 + 2 * pad + 1]
    dist = np.hypot(gx + 0.5 - centre[0], gy + 0.5 - centre[1])
    _blend(img, x0, y0, np.clip(width / 2.0 + 0.5 - np.abs(dist - radius), 0.0, 1.0), rgb)


def _axis_triad(img: np.ndarray, origin: tuple[float, float], length: float) -> None:
    basis = _basis("iso")
    colours = {"X": (200, 40, 40), "Y": (30, 150, 60), "Z": (40, 70, 200)}
    for k, name in enumerate("XYZ"):
        axis = np.zeros(3)
        axis[k] = 1.0
        s = basis @ axis
        end = (origin[0] + s[0] * length, origin[1] - s[1] * length)
        _segment(img, origin, end, colours[name], 3.0)
        label_x = int(end[0] + (6 if s[0] >= 0 else -16))
        label_y = int(end[1] - 9 + (-6 if s[1] > 0.3 else 6 if s[1] < -0.3 else 0))
        draw_text(img, label_x, label_y, name, colours[name], 2)


def _colour_bar(img: np.ndarray, x: int, y: int, bar: ColourBar, width: int, height: int) -> None:
    n = len(bar.colours)
    positions = (height - 1 - np.arange(height)) / max(height - 1, 1)  # top row = high end
    idx = positions * (n - 1)
    i0 = np.minimum(np.floor(idx).astype(np.int64), n - 2)
    frac = (idx - i0)[:, None]
    rows = _srgb_pack(np.asarray(bar.colours)[i0] * (1 - frac) + np.asarray(bar.colours)[i0 + 1] * frac)
    img[y : y + height, x : x + width] = rows[:, None, :]
    _frame(img, x - 1, y - 1, width + 2, height + 2, INK)
    for position, label in bar.ticks:
        ty = int(round(y + (1.0 - position) * (height - 1)))
        _fill(img, x + width + 1, ty, 6, 2, INK)
        draw_text(img, x + width + 14, ty - 8, label, INK, 2)


# --------------------------------------------------------------------------- the PNG


def encode_png(rgb: np.ndarray) -> bytes:
    """An 8-bit truecolour PNG of an (h, w, 3) uint8 array: filter 'Up', one IDAT, no time or text chunk (same pixels, same bytes)."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("encode_png needs an (h, w, 3) uint8 array")
    h, w, _ = rgb.shape
    rows = rgb.reshape(h, w * 3)
    filtered = np.empty((h, w * 3 + 1), dtype=np.uint8)
    filtered[:, 0] = 2
    filtered[0, 0] = 0
    filtered[0, 1:] = rows[0]
    filtered[1:, 1:] = rows[1:] - rows[:-1]  # uint8 wraps modulo 256, as the filter needs

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(filtered.tobytes(), 6))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------- the sheet

SHEET_WIDTH = 1200
MARGIN = 16
RIGHT_X = 800
RIGHT_W = SHEET_WIDTH - RIGHT_X - MARGIN
LEFT_W = RIGHT_X - 2 * MARGIN


def _nice_length(target_mm: float) -> float:
    """The largest of 1, 2, 5 x 10^k not above ``target_mm`` (a scale bar that reads at a glance)."""
    if not target_mm > 0.0 or not math.isfinite(target_mm):
        return 1.0
    exponent = math.floor(math.log10(target_mm))
    for step in (5.0, 2.0, 1.0):
        value = step * 10.0**exponent
        if value <= target_mm * (1.0 + 1e-9):
            return float(f"{value:.12g}")
    return float(f"{10.0 ** exponent:.12g}")


def _length_label(value_mm: float) -> str:
    return f"{value_mm:.0f} mm" if value_mm >= 1 else f"{value_mm:.3g} mm"


def _choose_ss(triangles: int, requested: int) -> int:
    if requested:
        return max(1, min(4, requested))
    return 3 if triangles <= 120_000 else 2


def render_sheet(mesh: Mesh, spec: SheetSpec, *, check: Check | None = None) -> PreviewResult:
    """Draw ``mesh`` into a PNG sheet as ``spec`` says.  ``check(phase)`` is called between chunks of work (a deadline)."""
    unknown = [v for v in spec.views if v not in VIEW_NAMES]
    if unknown or not spec.views or spec.views[0] != "iso" or any(v in EXTRA_VIEWS for v in spec.views):
        raise ValueError(f"views must start with 'iso' and be among {VIEW_NAMES}, got {spec.views}")
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    if verts.ndim != 2 or verts.shape[1] != 3 or len(verts) == 0 or len(mesh.triangles) == 0 or not np.isfinite(verts).all():
        raise ValueError("the mesh needs triangles and finite (n, 3) vertices")
    if (mesh.face_rgb is None) == (mesh.vertex_rgb is None):
        raise ValueError("the mesh needs exactly one of face_rgb (per triangle) and vertex_rgb (per vertex)")
    ss = _choose_ss(len(mesh.triangles), spec.ss)
    size = tuple(float(v) for v in (verts.max(axis=0) - verts.min(axis=0)))

    small_views = [v for v in spec.views[1:] if v in ("front", "top", "right")]
    extra = bool(small_views)
    top = 84
    small_h = 170
    canvas = np.empty((MAX_CANVAS[1], SHEET_WIDTH, 3), dtype=np.uint8)
    canvas[:] = BACKGROUND

    # title block
    y = MARGIN
    for line in _wrap(spec.title, SHEET_WIDTH - 2 * MARGIN, 3)[:1]:
        draw_text(canvas, MARGIN, y, line, INK, 3)
    y += 34
    for line in spec.subtitle:
        for part in _wrap(line, SHEET_WIDTH - 2 * MARGIN, 2)[:1]:
            draw_text(canvas, MARGIN, y, part, MUTED, 2)
        y += 20

    # the views: the isometric one first; a back view only when a marker is hidden in it
    iso_h = 500 if extra else 640
    image, facts = _render_view(mesh, "iso", LEFT_W, iso_h, ss, check, top=22)
    back = None
    if spec.back_view_for_markers and not extra and any(not m[2] for m in facts["markers"]):
        iso_h, back_h = 420, 300
        image, facts = _render_view(mesh, "iso", LEFT_W, iso_h, ss, check, top=22)
        best = -1
        for candidate in EXTRA_VIEWS:  # the view that shows most of the hidden markers (the earlier one on a tie)
            picture, found = _render_view(mesh, candidate, LEFT_W, back_h, ss, check, top=22)
            score = sum(1 for m_iso, m_new in zip(facts["markers"], found["markers"]) if not m_iso[2] and m_new[2])
            if score > best:
                best, back = score, (picture, found, back_h, candidate)
            if score == sum(1 for m in facts["markers"] if not m[2]):
                break
    group_all: dict[int, float] = dict(facts["group_counts"])
    marker_px: list[tuple[str, float, float, bool] | None] = [None] * len(mesh.markers)

    def add_groups(found: dict[int, float]) -> None:
        for g, c in found.items():
            group_all[g] = group_all.get(g, 0.0) + c


    def place(x0: int, y0: int, w: int, h: int, picture: np.ndarray, caption: str, scale: int = 2) -> None:
        canvas[y0 : y0 + h, x0 : x0 + w] = picture
        _frame(canvas, x0, y0, w, h, PANEL_BORDER)
        draw_text(canvas, x0 + 8, y0 + 6, caption, MUTED, scale)

    def mark(
        x0: int, y0: int, w: int, found: list[tuple[float, float, bool]], name: str = "iso", radius: float = 9.0, labelled: bool = True,
        h: int | None = None,
    ) -> None:
        shown: list[tuple[int, float, float]] = []
        for k, (marker, (mx, my, visible)) in enumerate(zip(mesh.markers, found)):
            if not visible or (h is not None and not (radius <= mx <= w - radius and 24 <= my <= h - radius)):
                continue  # not seen, or (in a close-up) outside the panel
            cx, cy = x0 + mx, y0 + my
            _ring(canvas, (cx, cy), radius, (255, 255, 255), radius * 2.0 / 3.0)
            _ring(canvas, (cx, cy), radius, marker.rgb, radius / 3.0)
            if marker_px[k] is None:
                marker_px[k] = (name, float(cx), float(cy), False)
            shown.append((k, cx, cy))
        if not labelled:
            return
        # markers closer than a ring radius share one label ("D1 D26"); a label that would cover an earlier one is tried on the
        # other side of its ring and dropped when that is taken too
        clusters: list[list[tuple[int, float, float]]] = []
        for item in shown:
            for cluster in clusters:
                if math.hypot(item[1] - cluster[0][1], item[2] - cluster[0][2]) <= radius:
                    cluster.append(item)
                    break
            else:
                clusters.append([item])
        taken: list[tuple[int, int, int, int]] = []
        for cluster in clusters:
            names = [mesh.markers[k].label for k, _, _ in cluster if mesh.markers[k].label]
            if not names:
                continue
            label = " ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
            rgb = mesh.markers[cluster[0][0]].rgb
            cx, cy = cluster[0][1], cluster[0][2]
            tw, th = text_width(label, 2) + 6, 22
            right = int(cx + radius + 5) - 3
            left = int(cx - radius - 5 - text_width(label, 2)) - 3
            if right + tw > x0 + w - 3:
                right, left = left, right
            for lx in (right, left):
                ly = int(cy - 9) - 2
                if lx < x0 + 2 or lx + tw > x0 + w - 2:
                    continue
                if any(lx < ox + ow and ox < lx + tw and ly < oy + oh and oy < ly + th for ox, oy, ow, oh in taken):
                    continue
                taken.append((lx, ly, tw, th))
                _fill(canvas, lx, ly, tw, th, (255, 255, 255))
                draw_text(canvas, lx + 3, ly + 2, label, rgb, 2)
                break

    place(MARGIN, top, LEFT_W, iso_h, image, VIEW_CAPTION["iso"])
    _axis_triad(canvas, (MARGIN + 52, top + iso_h - 52), 36.0)
    mark(MARGIN, top, LEFT_W, facts["markers"][: len(mesh.markers)])
    seen_in: list[str | None] = [("iso" if m[2] else None) for m in facts["markers"][: len(mesh.markers)]]
    left_bottom = top + iso_h
    if back is not None:
        back_image, back_facts, back_h, back_name = back
        back_y = top + iso_h + 12
        place(MARGIN, back_y, LEFT_W, back_h, back_image, VIEW_CAPTION[back_name])
        mark(MARGIN, back_y, LEFT_W, back_facts["markers"], back_name)
        seen_in = [first or (back_name if m[2] else None) for first, m in zip(seen_in, back_facts["markers"])]
        add_groups(back_facts["group_counts"])
        left_bottom = back_y + back_h
    if extra:
        gap = 10
        w = (LEFT_W - 2 * gap) // 3
        small_y = top + iso_h + 12
        for k, view in enumerate(small_views[:3]):
            sx0 = MARGIN + k * (w + gap)
            image_small, small_facts = _render_view(mesh, view, w, small_h, max(1, ss - 1), check, margin=8, top=20)
            place(sx0, small_y, w, small_h, image_small, VIEW_CAPTION[view], 1 if w < 200 else 2)
            add_groups(small_facts["group_counts"])
            if mesh.markers:
                mark(sx0, small_y, w, small_facts["markers"], view, radius=5.0, labelled=False)
                seen_in = [first or (view if m[2] else None) for first, m in zip(seen_in, small_facts["markers"])]
        left_bottom = small_y + small_h
        if spec.back_view_for_markers and any(view is None for view in seen_in):
            back_h = 300
            back_y = left_bottom + 12
            best = -1
            for candidate in EXTRA_VIEWS:  # as above: the view that shows most of the markers no earlier view shows
                picture, found = _render_view(mesh, candidate, LEFT_W, back_h, ss, check, top=22)
                score = sum(1 for first, m_new in zip(seen_in, found["markers"]) if first is None and m_new[2])
                if score > best:
                    best, back = score, (picture, found, back_h, candidate)
            if back is not None and best > 0:
                back_image, back_facts, back_h, back_name = back
                place(MARGIN, back_y, LEFT_W, back_h, back_image, VIEW_CAPTION[back_name])
                mark(MARGIN, back_y, LEFT_W, back_facts["markers"], back_name)
                seen_in = [first or (back_name if m[2] else None) for first, m in zip(seen_in, back_facts["markers"])]
                add_groups(back_facts["group_counts"])
                left_bottom = back_y + back_h
    for k, marker in enumerate(mesh.markers):  # a point inside the material: thin, in the isometric panel, labelled
        if marker.draw_hidden and seen_in[k] is None:
            mx, my, _ = facts["markers"][k]
            cx, cy = MARGIN + mx, top + my
            _ring(canvas, (cx, cy), 8.0, (255, 255, 255), 4.0)
            _ring(canvas, (cx, cy), 8.0, marker.rgb, 1.5)
            lx = int(cx + 13)
            if lx + text_width(marker.label, 2) > MARGIN + LEFT_W - 6:
                lx = int(cx - 13 - text_width(marker.label, 2))
            draw_text(canvas, lx, int(cy - 9), marker.label, marker.rgb, 2)
            marker_px[k] = ("iso", float(cx), float(cy), True)
    markers_visible = [view is not None for view in seen_in]

    focus_data = None
    if spec.focus is not None and len(spec.focus.triangles):
        focus = spec.focus
        focus_h = 440
        focus_y = left_bottom + 12
        mask = np.zeros(len(mesh.triangles), dtype=bool)
        mask[np.asarray(focus.triangles, dtype=np.int64)] = True
        candidates = [np.atleast_2d(np.asarray(lab.point_mm, dtype=np.float64)) for lab in focus.labels]
        label_points = [point for group in candidates for point in group]
        first_of = np.concatenate([[0], np.cumsum([len(group) for group in candidates])]).astype(int)
        view_name, best_pixels = "iso", -1.0
        for candidate in ("iso",) + EXTRA_VIEWS:  # the isometric view unless another shows 15 % more of the faces
            _, trial = _render_view(mesh, candidate, LEFT_W // 2, 220, 1, check, top=22, crop=focus.box_mm, focus_mask=mask)
            if trial["focus_pixels"] > max(best_pixels * 1.15, 0.0) or best_pixels < 0:
                view_name, best_pixels = candidate, trial["focus_pixels"]
        close, close_facts = _render_view(mesh, view_name, LEFT_W, focus_h, ss, check, top=22, crop=focus.box_mm, points=label_points, focus_mask=mask)
        scale_px = close_facts["scale"]
        zoom = scale_px / facts["scale"] if facts["scale"] > 0 else 0.0
        caption = f"{focus.caption} - {VIEW_CAPTION[view_name]} - x{zoom:.3g}"
        place(MARGIN, focus_y, LEFT_W, focus_h, close, caption)
        _fill(canvas, MARGIN + 1, focus_y + 1, text_width(caption, 2) + 16, 22, (255, 255, 255))  # the model may run under the caption
        draw_text(canvas, MARGIN + 8, focus_y + 6, caption, MUTED, 2)
        close_markers = close_facts["markers"]
        mark(MARGIN, focus_y, LEFT_W, close_markers[: len(mesh.markers)], view_name, radius=9.0, h=focus_h)
        drawn_labels: list[str] = []
        hidden_labels: list[str] = []
        taken: list[tuple[int, int, int, int]] = []
        found_points = close_markers[len(mesh.markers) :]
        for index, lab in enumerate(focus.labels):
            seen = [
                (lx, ly)
                for lx, ly, visible in found_points[first_of[index] : first_of[index + 1]]
                if visible and 0 <= lx < LEFT_W and 24 <= ly < focus_h
            ]
            if not seen:
                hidden_labels.append(lab.text)
                continue
            lx, ly = seen[0]
            tw, th = text_width(lab.text, 2) + 6, 22
            x0, y0 = int(MARGIN + lx - tw / 2), int(focus_y + ly - th / 2)
            for _ in range(8):  # slide a label down while it covers another one
                if any(x0 < tx + tww and tx < x0 + tw and y0 < ty + thh and ty < y0 + th for tx, ty, tww, thh in taken):
                    y0 += th + 2
                else:
                    break
            x0 = max(MARGIN + 2, min(x0, MARGIN + LEFT_W - tw - 2))
            y0 = max(focus_y + 24, min(y0, focus_y + focus_h - th - 24))
            taken.append((x0, y0, tw, th))
            _fill(canvas, x0, y0, tw, th, (255, 255, 255))
            _frame(canvas, x0, y0, tw, th, INK)
            draw_text(canvas, x0 + 3, y0 + 2, lab.text, INK, 2)
            drawn_labels.append(lab.text)
        bar_mm = _nice_length(0.3 * LEFT_W / scale_px) if scale_px > 0 else 1.0
        bar_px = bar_mm * scale_px
        bx, by = MARGIN + 16, focus_y + focus_h - 22
        _fill(canvas, bx - 2, by - 26, int(bar_px) + 4 + text_width(_length_label(bar_mm), 2) + 12, 36, (255, 255, 255))
        _fill(canvas, bx, by, int(round(bar_px)), 3, INK)
        _fill(canvas, bx, by - 5, 2, 13, INK)
        _fill(canvas, bx + int(round(bar_px)) - 2, by - 5, 2, 13, INK)
        draw_text(canvas, bx + int(round(bar_px)) + 10, by - 8, _length_label(bar_mm), INK, 2)
        draw_text(canvas, bx, by - 22, "scale in the picture plane", MUTED, 1)
        left_bottom = focus_y + focus_h
        focus_data = {
            "view": view_name,
            "zoom": float(f"{zoom:.4g}"),
            "scale_px_per_mm": float(f"{scale_px:.6g}"),
            "scale_bar_mm": bar_mm,
            "panel": {"x": MARGIN, "y": focus_y, "width": LEFT_W, "height": focus_h},
            "labels_drawn": drawn_labels,
            "labels_not_drawn": hidden_labels,
            "focus_pixels": int(round(close_facts["focus_pixels"])),
            "box_mm": [[float(f"{v:.6g}") for v in row] for row in np.asarray(focus.box_mm, dtype=np.float64).reshape(2, 3)],
        }

    # the right column
    y = top
    right_w = RIGHT_W

    def heading(text: str) -> None:
        nonlocal y
        draw_text(canvas, RIGHT_X, y, text, INK, 3)
        y += 34

    if spec.legend:
        heading(spec.legend_heading)
        for item in spec.legend:
            if item.kind == "line":
                _fill(canvas, RIGHT_X, y + 10, 28, 3, item.rgb)
            else:
                _fill(canvas, RIGHT_X, y + 1, 28, 18, item.rgb)
                _frame(canvas, RIGHT_X, y + 1, 28, 18, INK)
            lines = _wrap(item.label, right_w - 40, 2)
            for part in lines:
                draw_text(canvas, RIGHT_X + 38, y + 2, part, INK, 2)
                y += 20
            for part in _wrap(item.detail, right_w - 40, 2) if item.detail else []:
                draw_text(canvas, RIGHT_X + 38, y + 1, part, MUTED, 2)
                y += 20
            y += 8 - (0 if len(lines) > 1 else 0)
        y += 6
    if spec.colourbar is not None:
        bar = spec.colourbar
        for part in _wrap(bar.heading, right_w, 3):
            draw_text(canvas, RIGHT_X, y, part, INK, 3)
            y += 30
        draw_text(canvas, RIGHT_X, y, f"[{bar.unit}]", MUTED, 2)
        y += 30
        bar_h = 330
        _colour_bar(canvas, RIGHT_X + 4, y, bar, 34, bar_h)
        y += bar_h + 18
        for line in bar.lines:
            for part in _wrap(line, right_w, 2):
                draw_text(canvas, RIGHT_X, y, part, INK, 2)
                y += 20
            y += 4
        y += 8
    if spec.info:
        for line in spec.info:
            for part in _wrap(line, right_w, 2):
                draw_text(canvas, RIGHT_X, y, part, INK, 2)
                y += 20
            y += 4
        y += 8
    for line in spec.notes:
        for part in _wrap(line, right_w, 2):
            draw_text(canvas, RIGHT_X, y, part, MUTED, 2)
            y += 20
        y += 6

    height = min(MAX_CANVAS[1], max(left_bottom, y) + MARGIN)
    png = encode_png(canvas[:height])
    group_pixels = {g: int(math.ceil(c)) for g, c in facts["group_counts"].items()}
    group_pixels_all = {g: int(math.ceil(c)) for g, c in group_all.items()}
    return PreviewResult(
        png=png,
        width=SHEET_WIDTH,
        height=height,
        views=tuple(spec.views),
        supersampling=ss,
        group_pixels=group_pixels,
        visible_pixels=int(round(facts["covered_pixels"])),
        markers_visible=tuple(markers_visible),
        markers_view=tuple(seen_in),
        group_pixels_all=group_pixels_all,
        size_mm=size,  # type: ignore[arg-type]
        markers_px=tuple(marker_px),
        focus=focus_data,
    )
