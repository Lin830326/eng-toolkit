"""Our own SVG, as one PDF page: the first half of the PNG of a chart (the second half is PDFium, ``drawing/raster.py``).

Only what ``_svg.Canvas`` writes is understood: ``svg g defs clipPath rect path polyline line circle text`` (``title``, ``desc`` and
``style`` are ignored), the attributes ``fill stroke stroke-width stroke-dasharray stroke-linejoin stroke-linecap fill-opacity
clip-path text-anchor font-size font-weight transform=rotate(...)``, and in path data ``M L H V C A Z`` in both cases (relative arcs
become Bezier curves).  Anything else raises :class:`ValueError`, so a new element can never be silently left out of the picture.
The colours are the fallback attributes (the light theme for ``auto``); the page is 1 px = 1 pt, y downwards.

Text uses the standard PDF fonts, so nothing is embedded and the file holds no date, id or path: Helvetica and Helvetica-Bold
(WinAnsi: Latin-1 plus the usual punctuation), Symbol for Greek letters, minus, <=, >=, ~=, infinity, and the Adobe-CNS1 CJK font
(system substituted by PDFium) for Chinese, Japanese and Korean text.  Anything else is drawn as ``?`` and counted
(:attr:`Converted.substituted`).  Text is placed with the Helvetica advance widths (AFM), so ``text-anchor`` middle and end are exact.
"""

from __future__ import annotations

import math
import re
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass

SVG_NS = "{http://www.w3.org/2000/svg}"

# ------------------------------------------------------------------------------------------------ font metrics (Adobe AFM, 1/1000 em)
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556"
    " 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556"
    " 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611"
    " 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556"
    " 333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)
_REGULAR = [int(v) for v in _HELVETICA.split()]  # code 32..126
_BOLD = [int(v) for v in _HELVETICA_BOLD.split()]
_LATIN1_EXTRA = {  # (regular, bold) widths of the non-ASCII characters charts use
    "°": (400, 400), "µ": (556, 611), "±": (584, 584), "×": (584, 584), "÷": (584, 584), "·": (278, 278), "²": (333, 333), "³": (333, 333),
    "¹": (333, 333), "…": (1000, 1000), "–": (556, 556), "—": (1000, 1000), "•": (350, 350), "‰": (1000, 1000), "€": (556, 556),
}
_DEFAULT_WIDTH = (556, 611)

# Symbol (built-in encoding) code and width of each character that is drawn with the Symbol font
_GREEK_CAPITALS = "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"  # codes 0x41..0x5A in order
_GREEK_CAPITAL_WIDTHS = (722, 667, 722, 612, 611, 763, 603, 722, 333, 631, 722, 686, 889, 722, 722, 768, 741, 556, 592, 611, 690, 439, 768, 645, 795, 611)
_GREEK_SMALL = "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"  # codes 0x61..0x7A in order
_GREEK_SMALL_WIDTHS = (631, 549, 549, 494, 439, 521, 411, 603, 329, 603, 549, 549, 576, 521, 549, 549, 521, 549, 603, 439, 576, 713, 686, 493, 686, 494)
SYMBOL: dict[str, tuple[int, int]] = {}
for _i, (_c, _w) in enumerate(zip(_GREEK_CAPITALS, _GREEK_CAPITAL_WIDTHS)):
    SYMBOL[_c] = (0x41 + _i, _w)
for _i, (_c, _w) in enumerate(zip(_GREEK_SMALL, _GREEK_SMALL_WIDTHS)):
    SYMBOL[_c] = (0x61 + _i, _w)
SYMBOL.update({
    "−": (0x2D, 549), "≤": (0xA3, 549), "≥": (0xB3, 549), "∞": (0xA5, 713), "≈": (0xBB, 549), "≠": (0xB9, 549),
    "∂": (0xB6, 494), "∑": (0xE5, 713), "√": (0xD6, 549), "∫": (0xF2, 274), "Ω": (0x57, 768), "→": (0xAE, 987),
    "←": (0xAC, 987), "↑": (0xAD, 603), "↓": (0xAF, 603), "∆": (0x44, 612), "⌀": (0xC6, 823),
})

FONT_REGULAR, FONT_BOLD, FONT_SYMBOL, FONT_CJK = "F1", "F2", "F3", "F4"
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_TOKEN = re.compile(r"([MmLlHhVvCcAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|[\s,]+")
_ROTATE = re.compile(r"rotate\(\s*(" + _NUMBER.pattern + r")\s+(" + _NUMBER.pattern + r")\s+(" + _NUMBER.pattern + r")\s*\)")
_URL = re.compile(r"url\(#([A-Za-z0-9-]+)\)")
KAPPA = 0.5522847498307936


def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F") and ord(ch) <= 0xFFFF


def _latin(ch: str) -> bytes | None:
    """The WinAnsi byte of ``ch`` or None (C0/C1 controls and what cp1252 lacks are not drawable with Helvetica)."""
    code = ord(ch)
    if 32 <= code < 127:
        return bytes([code])
    if code < 32 or 127 <= code < 160:
        return None
    try:
        return ch.encode("cp1252")
    except UnicodeEncodeError:
        return None


def char_width(ch: str, bold: bool) -> float:
    """Advance of one character in 1/1000 em."""
    code = ord(ch)
    if 32 <= code < 127:
        return (_BOLD if bold else _REGULAR)[code - 32]
    if _latin(ch) is not None:
        return _LATIN1_EXTRA.get(ch, _DEFAULT_WIDTH)[1 if bold else 0]
    if ch in SYMBOL:
        return SYMBOL[ch][1]
    return 1000.0 if _is_wide(ch) else _DEFAULT_WIDTH[1 if bold else 0]


def text_runs(text: str, bold: bool) -> tuple[list[tuple[str, bytes, float]], int]:
    """(runs, substituted): runs are (font, bytes, width in 1/1000 em); characters nothing can draw become '?'."""
    runs: list[tuple[str, bytes, float]] = []
    substituted = 0
    for ch in text:
        latin = _latin(ch)
        if latin is not None:
            font, raw, width = (FONT_BOLD if bold else FONT_REGULAR), latin, char_width(ch, bold)
        elif ch in SYMBOL:
            font, raw, width = FONT_SYMBOL, bytes([SYMBOL[ch][0]]), float(SYMBOL[ch][1])
        elif _is_wide(ch):
            font, raw, width = FONT_CJK, ord(ch).to_bytes(2, "big"), 1000.0
        else:
            font, raw, width = (FONT_BOLD if bold else FONT_REGULAR), b"?", char_width("?", bold)
            substituted += 1
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + raw, runs[-1][2] + width)
        else:
            runs.append((font, raw, width))
    return runs, substituted


# ------------------------------------------------------------------------------------------------ number and colour formats


def n(v: float) -> str:
    if not math.isfinite(v):
        v = 0.0
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def colour(value: str | None, default: str = "#000000") -> str | None:
    """PDF colour operands for ``#RRGGBB`` (``None`` for 'none')."""
    value = (value or default).strip()
    if value == "none":
        return None
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"unsupported colour {value!r}")
    return " ".join(n(int(value[i:i + 2], 16) / 255.0) for i in (1, 3, 5))


# ------------------------------------------------------------------------------------------------ paths


def arc_curves(x1: float, y1: float, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool, x2: float, y2: float) -> list[tuple[float, ...]]:
    """An SVG elliptical arc as cubic Bezier segments (c1x, c1y, c2x, c2y, x, y), at most a quarter turn each (SVG 1.1 appendix F.6)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0.0 or ry == 0.0:
        return [(x1, y1, x2, y2, x2, y2)]
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) * (-1.0 if large == sweep else 1.0)
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2.0, sp * cxp + cp * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    theta = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    parts = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / parts
    alpha = 4.0 / 3.0 * math.tan(step / 4.0)
    out = []
    t = theta
    for _ in range(parts):
        t2 = t + step
        c1, s1, c2, s2 = math.cos(t), math.sin(t), math.cos(t2), math.sin(t2)
        pts = [(c1 - alpha * s1, s1 + alpha * c1), (c2 + alpha * s2, s2 - alpha * c2), (c2, s2)]
        flat: list[float] = []
        for ux, uy in pts:
            ex, ey = rx * ux, ry * uy
            flat += [cp * ex - sp * ey + cx, sp * ex + cp * ey + cy]
        out.append(tuple(flat))
        t = t2
    last = out[-1]
    out[-1] = (*last[:4], x2, y2)  # land exactly on the end point
    return out


def path_ops(d: str) -> str:
    """PDF path construction operators for SVG path data."""
    tokens: list[tuple[str | None, str | None]] = []
    pos = 0
    while pos < len(d):
        m = _PATH_TOKEN.match(d, pos)
        if m is None:
            raise ValueError(f"unsupported path data {d[pos:pos + 20]!r}")  # a command or character the converter does not know
        if m.group(1) or m.group(2):
            tokens.append((m.group(1), m.group(2)))
        pos = m.end()
    out: list[str] = []
    i = 0
    cx = cy = sx = sy = 0.0
    cmd = ""

    def take(count: int) -> list[float]:
        nonlocal i
        vals = []
        for _ in range(count):
            if i >= len(tokens) or tokens[i][1] is None:
                raise ValueError(f"malformed path data near {d[:60]!r}")
            vals.append(float(tokens[i][1]))
            i += 1
        return vals

    while i < len(tokens):
        if tokens[i][0]:
            cmd = tokens[i][0]
            i += 1
            if cmd in "Zz":
                out.append("h")
                cx, cy = sx, sy
                continue
        elif cmd == "":
            raise ValueError(f"path data does not start with a command: {d[:60]!r}")
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = take(2)
            cx, cy = (cx + x, cy + y) if rel else (x, y)
            sx, sy = cx, cy
            out.append(f"{n(cx)} {n(cy)} m")
            cmd = "l" if rel else "L"
        elif c == "L":
            x, y = take(2)
            cx, cy = (cx + x, cy + y) if rel else (x, y)
            out.append(f"{n(cx)} {n(cy)} l")
        elif c == "H":
            (x,) = take(1)
            cx = cx + x if rel else x
            out.append(f"{n(cx)} {n(cy)} l")
        elif c == "V":
            (y,) = take(1)
            cy = cy + y if rel else y
            out.append(f"{n(cx)} {n(cy)} l")
        elif c == "C":
            v = take(6)
            if rel:
                v = [v[0] + cx, v[1] + cy, v[2] + cx, v[3] + cy, v[4] + cx, v[5] + cy]
            out.append(" ".join(n(a) for a in v) + " c")
            cx, cy = v[4], v[5]
        elif c == "A":
            rx, ry, rot, fa, fs, x, y = take(7)
            if rel:
                x, y = cx + x, cy + y
            for seg in arc_curves(cx, cy, rx, ry, rot, fa != 0, fs != 0, x, y):
                out.append(" ".join(n(a) for a in seg) + " c")
            cx, cy = x, y
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
    return "\n".join(out)


def rect_ops(x: float, y: float, w: float, h: float, rx: float = 0.0) -> str:
    if rx <= 0:
        return f"{n(x)} {n(y)} {n(w)} {n(h)} re"
    rx = min(rx, w / 2, h / 2)
    k = rx * (1 - KAPPA)
    x1, y1 = x + w, y + h
    return "\n".join([
        f"{n(x + rx)} {n(y)} m", f"{n(x1 - rx)} {n(y)} l", f"{n(x1 - k)} {n(y)} {n(x1)} {n(y + k)} {n(x1)} {n(y + rx)} c",
        f"{n(x1)} {n(y1 - rx)} l", f"{n(x1)} {n(y1 - k)} {n(x1 - k)} {n(y1)} {n(x1 - rx)} {n(y1)} c",
        f"{n(x + rx)} {n(y1)} l", f"{n(x + k)} {n(y1)} {n(x)} {n(y1 - k)} {n(x)} {n(y1 - rx)} c",
        f"{n(x)} {n(y + rx)} l", f"{n(x)} {n(y + k)} {n(x + k)} {n(y)} {n(x + rx)} {n(y)} c", "h",
    ])


# ------------------------------------------------------------------------------------------------ the converter


@dataclass
class Converted:
    pdf: bytes
    width: float
    height: float
    substituted: int  # characters drawn as '?'
    cjk: bool  # CJK text was drawn with a system-substituted font


class _Page:
    def __init__(self, root: ET.Element):
        view = (root.get("viewBox") or "").split()
        if len(view) != 4:
            raise ValueError("the SVG has no viewBox")
        self.width, self.height = float(view[2]), float(view[3])
        self.clips: dict[str, tuple[float, float, float, float]] = {}
        self.ops: list[str] = []
        self.opacities: dict[str, int] = {}
        self.fonts: set[str] = set()
        self.substituted = 0
        self._collect_clips(root)

    # --- clip paths
    def _collect_clips(self, root: ET.Element) -> None:
        for el in root.iter(SVG_NS + "clipPath"):
            rect = el.find(SVG_NS + "rect")
            if rect is None:
                raise ValueError("a clipPath without a rect")
            self.clips[el.get("id", "")] = tuple(float(rect.get(k, "0")) for k in ("x", "y", "width", "height"))  # type: ignore[assignment]

    def _clip(self, el: ET.Element) -> str | None:
        ref = el.get("clip-path")
        if not ref:
            return None
        m = _URL.fullmatch(ref)
        if not m or m.group(1) not in self.clips:
            raise ValueError(f"unknown clip path {ref!r}")
        x, y, w, h = self.clips[m.group(1)]
        return f"{n(x)} {n(y)} {n(w)} {n(h)} re W n"

    # --- graphics state of one shape
    def _gs(self, opacity: float) -> str:
        key = n(opacity)
        index = self.opacities.setdefault(key, len(self.opacities) + 1)
        return f"/GS{index} gs"

    def _paint(self, el: ET.Element, shape: str, *, default_fill: str = "#000000") -> None:
        fill = colour(el.get("fill"), default_fill)
        stroke = colour(el.get("stroke"), "none")
        head = ["q"]
        clip = self._clip(el)
        if clip:
            head.append(clip)
        opacity = float(el.get("fill-opacity", "1"))
        if fill is not None and opacity < 1.0:
            head.append(self._gs(opacity))
        if fill is not None:
            head.append(f"{fill} rg")
        if stroke is not None:
            head.append(f"{stroke} RG")
            head.append(f"{n(float(el.get('stroke-width', '1')))} w")
            join = {"miter": 0, "round": 1, "bevel": 2}[el.get("stroke-linejoin", "miter")]
            cap = {"butt": 0, "round": 1, "square": 2}[el.get("stroke-linecap", "butt")]
            head.append(f"{join} j {cap} J")
            dash = el.get("stroke-dasharray")
            if dash:
                parts = [float(v) for v in re.split(r"[\s,]+", dash.strip()) if v]
                head.append("[" + " ".join(n(v) for v in parts) + "] 0 d")
        painter = {(True, True): "B", (True, False): "f", (False, True): "S", (False, False): "n"}[(fill is not None, stroke is not None)]
        self.ops.append("\n".join(head + [shape, painter, "Q"]))

    # --- elements
    def draw(self, el: ET.Element) -> None:
        tag = el.tag[len(SVG_NS):] if el.tag.startswith(SVG_NS) else el.tag
        if tag in ("title", "desc", "style", "defs"):
            return
        if tag in ("svg", "g"):
            clip = self._clip(el) if tag == "g" else None
            if clip:
                self.ops.append("q\n" + clip)
            for child in el:
                self.draw(child)
            if clip:
                self.ops.append("Q")
        elif tag == "rect":
            x, y, w, h = (float(el.get(k, "0")) for k in ("x", "y", "width", "height"))
            self._paint(el, rect_ops(x, y, w, h, float(el.get("rx", "0"))))
        elif tag == "path":
            self._paint(el, path_ops(el.get("d", "")))
        elif tag == "polyline":
            pts = [tuple(float(v) for v in p.split(",")) for p in (el.get("points") or "").split()]
            if len(pts) < 1:
                return
            shape = "\n".join([f"{n(pts[0][0])} {n(pts[0][1])} m"] + [f"{n(a)} {n(b)} l" for a, b in pts[1:]])
            self._paint(el, shape, default_fill="none")
        elif tag == "line":
            x1, y1, x2, y2 = (float(el.get(k, "0")) for k in ("x1", "y1", "x2", "y2"))
            self._paint(el, f"{n(x1)} {n(y1)} m {n(x2)} {n(y2)} l", default_fill="none")
        elif tag == "circle":
            cx, cy, r = (float(el.get(k, "0")) for k in ("cx", "cy", "r"))
            self._paint(el, path_ops(f"M{cx - r} {cy}a{r} {r} 0 1 0 {2 * r} 0a{r} {r} 0 1 0 {-2 * r} 0Z"))
        elif tag == "text":
            self._text(el)
        else:
            raise ValueError(f"unsupported element <{tag}>")

    def _text(self, el: ET.Element) -> None:
        text = el.text or ""
        if not text:
            return
        fill = colour(el.get("fill"))
        size = float(el.get("font-size", "12"))
        weight = el.get("font-weight", "normal")
        bold = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
        x, y = float(el.get("x", "0")), float(el.get("y", "0"))
        angle = 0.0
        transform = el.get("transform")
        if transform:
            m = _ROTATE.fullmatch(transform.strip())
            if not m:
                raise ValueError(f"unsupported transform {transform!r}")
            angle = float(m.group(1))
        runs, substituted = text_runs(text, bold)
        self.substituted += substituted
        total = sum(w for _, _, w in runs) * size / 1000.0
        anchor = el.get("text-anchor", "start")
        u = -total / 2.0 if anchor == "middle" else (-total if anchor == "end" else 0.0)
        cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        lines = ["q", f"{fill} rg", "BT"]
        for font, raw, width in runs:
            self.fonts.add(font)
            lines.append(f"/{font} {n(size)} Tf")
            lines.append(f"{n(cos)} {n(sin)} {n(sin)} {n(-cos)} {n(x + u * cos)} {n(y + u * sin)} Tm")
            lines.append(f"<{raw.hex().upper()}> Tj")
            u += width * size / 1000.0
        lines += ["ET", "Q"]
        self.ops.append("\n".join(lines))


def _obj(number: int, body: bytes) -> bytes:
    return f"{number} 0 obj\n".encode() + body + b"\nendobj\n"


def svg_to_pdf(svg: str) -> Converted:
    """One-page PDF of a chart SVG written by ``_svg.Canvas``; :class:`ValueError` for anything the converter does not know."""
    root = ET.fromstring(svg)
    page = _Page(root)
    page.draw(root)
    content = zlib.compress(f"1 0 0 -1 0 {n(page.height)} cm\n".encode() + "\n".join(page.ops).encode("ascii"), 9)

    objects: dict[int, bytes] = {}
    font_ref: dict[str, int] = {}
    next_id = 5
    specs = {
        FONT_REGULAR: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        FONT_BOLD: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        FONT_SYMBOL: b"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol >>",
    }
    for name in sorted(page.fonts):
        font_ref[name] = next_id
        if name == FONT_CJK:
            objects[next_id] = _obj(next_id, f"<< /Type /Font /Subtype /Type0 /BaseFont /MSung-Light /Encoding /UniCNS-UCS2-H /DescendantFonts [{next_id + 1} 0 R] >>".encode())
            objects[next_id + 1] = _obj(
                next_id + 1,
                b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /MSung-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (CNS1) /Supplement 0 >> /DW 1000 >>",
            )
            next_id += 2
        else:
            objects[next_id] = _obj(next_id, specs[name])
            next_id += 1
    fonts = " ".join(f"/{k} {v} 0 R" for k, v in sorted(font_ref.items()))
    states = " ".join(f"/GS{i} << /ca {k} /CA 1 >>" for k, i in sorted(page.opacities.items(), key=lambda kv: kv[1]))
    resources = f"<< /Font << {fonts} >> /ExtGState << {states} >> >>"
    objects[1] = _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    objects[2] = _obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects[3] = _obj(3, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {n(page.width)} {n(page.height)}] /Resources {resources} /Contents 4 0 R >>".encode())
    objects[4] = _obj(4, f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode() + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += objects[number]
    xref = len(out)
    count = max(objects) + 1
    out += f"xref\n0 {count}\n0000000000 65535 f \n".encode()
    for number in range(1, count):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return Converted(bytes(out), page.width, page.height, page.substituted, FONT_CJK in page.fonts)


__all__ = ["Converted", "arc_curves", "path_ops", "svg_to_pdf", "text_runs"]
