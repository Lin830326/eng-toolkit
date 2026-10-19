"""What a DXF ``DIMENSION`` measures and what a viewer shows for it.  Pure arithmetic, no engine.

A dimension stores definition points, not a number.  The value on the sheet is ``measurement x DIMLFAC`` (linear,
radius, diameter and ordinate values; never angles), rounded and formatted by the dimension style, unless a text
override replaces it.  The ``DIMLFAC`` that applies is the one in the entity's own ``DSTYLE`` extended data, which is
where the dimensions of this toolkit's own drawings keep it, else the one in the dimension style table, else 1.
Group 42 of the entity ("actual measurement") is optional and some writers leave it stale, so it is never used.

Points are DXF definition points by group code: 10 (the dimension line / definition point), 13, 14, 15, 16.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float, float]

KINDS: dict[int, str] = {0: "linear", 1: "aligned", 2: "angular", 3: "diameter", 4: "radius", 5: "angular_3point", 6: "ordinate"}
ANGLE_KINDS = ("angular", "angular_3point")
DEGREE = "°"
DIAMETER = "Ø"
PLUS_MINUS = "±"


def kind_of(dimtype: int) -> str:
    """The dimension type from group 70; bits 32, 64 and 128 are flags, not part of the type."""
    code = dimtype & 0x0F
    return KINDS.get(code, f"unknown({code})")


def is_ordinate_x(dimtype: int) -> bool:
    """An ordinate dimension measures an X coordinate when bit 64 is set, else a Y coordinate."""
    return bool(dimtype & 64)


# --------------------------------------------------------------------------- what is measured


@dataclass(frozen=True)
class Measurement:
    value: float | None  # None: the definition points are missing or degenerate
    unit: str  # "length" in drawing units, or "angle" in degrees


def _sub(a: Point, b: Point) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def _angle_deg(v: tuple[float, float]) -> float:
    return math.degrees(math.atan2(v[1], v[0])) % 360.0


def _intersection(p: Point, q: Point, r: Point, s: Point) -> tuple[float, float] | None:
    """Where the line through p, q meets the line through r, s; None when they are parallel or a line has no direction."""
    d1, d2 = _sub(q, p), _sub(s, r)
    denominator = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denominator) < 1e-12 * max(1.0, math.hypot(*d1) * math.hypot(*d2)):
        return None
    w = _sub(r, p)
    t = (w[0] * d2[1] - w[1] * d2[0]) / denominator
    return (p[0] + t * d1[0], p[1] + t * d1[1])


def _two_line_angle(pts: dict[int, Point]) -> float | None:
    """The sector between two lines that holds the dimension arc: 0 to 180 degrees."""
    if not all(code in pts for code in (13, 14, 15, 10, 16)):
        return None
    centre = _intersection(pts[13], pts[14], pts[15], pts[10])
    if centre is None:
        return None
    toward_arc = (pts[16][0] - centre[0], pts[16][1] - centre[1])
    if math.hypot(*toward_arc) < 1e-12:
        return None
    a1, a2 = _angle_deg(_sub(pts[14], pts[13])), _angle_deg(_sub(pts[10], pts[15]))
    boundaries = sorted({round(b % 360.0, 9) for b in (a1, a1 + 180.0, a2, a2 + 180.0)})
    here = _angle_deg(toward_arc)
    below = [b for b in boundaries if b <= here]
    lower = below[-1] if below else boundaries[-1] - 360.0
    above = [b for b in boundaries if b > here]
    upper = above[0] if above else boundaries[0] + 360.0
    return upper - lower


def measure(kind: str, pts: dict[int, Point], *, rotation_deg: float = 0.0, x_type: bool = False) -> Measurement:
    """The geometric measurement of one dimension, before DIMLFAC; ``value`` is None when it cannot be told."""
    if kind in ANGLE_KINDS:
        return Measurement(_angle(kind, pts), "angle")
    return Measurement(_length(kind, pts, rotation_deg, x_type), "length")


def _length(kind: str, pts: dict[int, Point], rotation_deg: float, x_type: bool) -> float | None:
    if kind == "linear" and 13 in pts and 14 in pts:
        dx, dy = _sub(pts[14], pts[13])
        rad = math.radians(rotation_deg)
        return abs(dx * math.cos(rad) + dy * math.sin(rad))
    if kind == "aligned" and 13 in pts and 14 in pts:
        return math.hypot(*_sub(pts[14], pts[13]))
    if kind in ("diameter", "radius") and 10 in pts and 15 in pts:
        return math.hypot(*_sub(pts[10], pts[15]))
    if kind == "ordinate" and 10 in pts and 13 in pts:
        dx, dy = _sub(pts[13], pts[10])
        return abs(dx if x_type else dy)
    return None


def _angle(kind: str, pts: dict[int, Point]) -> float | None:
    if kind == "angular":
        return _two_line_angle(pts)
    if all(code in pts for code in (15, 13, 14)):
        first, second = _sub(pts[13], pts[15]), _sub(pts[14], pts[15])
        if math.hypot(*first) < 1e-12 or math.hypot(*second) < 1e-12:
            return None
        return (_angle_deg(second) - _angle_deg(first)) % 360.0
    return None


# --------------------------------------------------------------------------- DIMLFAC


@dataclass(frozen=True)
class Lfac:
    factor: float
    source: str  # "entity DSTYLE override", "dimension style" or "default"
    note: str = ""


def resolve_dimlfac(*, override: float | None, style: float | None, paper_space: bool) -> Lfac:
    """The linear scale factor that applies to one dimension, and where it came from."""
    if override is not None:
        raw, source = override, "entity DSTYLE override"
    elif style is not None:
        raw, source = style, "dimension style"
    else:
        return Lfac(1.0, "default")
    if raw == 0.0:
        return Lfac(1.0, source, "DIMLFAC is zero, which is invalid; read as 1")
    if raw < 0.0:
        note = "negative DIMLFAC applies to dimensions in paper space only, as its absolute value"
        return Lfac(abs(raw) if paper_space else 1.0, source, note)
    return Lfac(raw, source)


def displayed(measured: float, unit: str, factor: float) -> float:
    """DIMLFAC scales lengths, not angles."""
    return measured * factor if unit == "length" else measured


# --------------------------------------------------------------------------- the text


@dataclass(frozen=True)
class TextStyle:
    dec: int = 4  # DIMDEC; AutoCAD's own default for a drawing that does not say
    zin: int = 0  # DIMZIN: 4 suppresses leading zeros, 8 trailing zeros
    rnd: float = 0.0  # DIMRND: round to a multiple of this
    dsep: str = "."  # DIMDSEP
    post: str = ""  # DIMPOST
    adec: int = -1  # DIMADEC: angular precision, -1 = use DIMDEC
    lunit: int = 2  # DIMLUNIT: 2 decimal


def _zero_suppress(text: str, zin: int) -> str:
    if "." in text and zin & 8:
        text = text.rstrip("0").rstrip(".")
    if zin & 4 and text.startswith("0."):
        text = text[1:]
    return text or "0"


def format_value(value: float, unit: str, style: TextStyle, *, kind: str = "linear") -> str | None:
    """The text a dimension style makes of a value; None when its unit format (fractional, architectural) is not computed."""
    if unit == "angle":
        places = style.dec if style.adec < 0 else style.adec
        text = _zero_suppress(f"{value:.{places}f}", style.zin)
        return text.replace(".", style.dsep) + DEGREE
    if style.lunit != 2:
        return None
    if style.rnd > 0:
        value = round(value / style.rnd) * style.rnd
    text = f"{value:.{max(style.dec, 0)}f}"
    if text.lstrip("-").replace(".", "").strip("0") == "":
        text = text.lstrip("-")
    text = _zero_suppress(text, style.zin).replace(".", style.dsep)
    text = {"diameter": DIAMETER, "radius": "R"}.get(kind, "") + text
    if not style.post:
        return text
    return style.post.replace("<>", text) if "<>" in style.post else text + style.post


@dataclass(frozen=True)
class Shown:
    text: str
    hidden: bool  # a single space as the override hides the dimension text


def apply_override(override: str | None, formatted: str) -> Shown:
    """The text a viewer draws: the override with every ``<>`` replaced by the formatted value."""
    if override is None or override == "":
        return Shown(formatted, False)
    if override == " ":
        return Shown("", True)
    return Shown(override.replace("<>", formatted), False)


def unescape_percent(text: str) -> str:
    """AutoCAD's ``%%c`` (diameter), ``%%d`` (degree), ``%%p`` (plus-minus) and ``%%%`` (percent) codes in single-line text."""
    table = {"c": DIAMETER, "d": DEGREE, "p": PLUS_MINUS, "%": "%"}
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("%%", i) and i + 2 < len(text) and text[i + 2].lower() in table:
            out.append(table[text[i + 2].lower()])
            i += 3
        else:
            out.append(text[i])
            i += 1
    return "".join(out)
