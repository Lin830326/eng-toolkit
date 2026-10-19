"""Reads a DXF file into plain data: the ezdxf side of ``drawing_read`` (the second module allowed to import ezdxf).

``read(path, limits)`` runs in a child process (:mod:`eng_toolkit.isolated`) because the file is untrusted.  It returns
one dict that is JSON all the way down; the tool cuts it to size.  Nothing in the file is executed or followed: an
external reference or an image path is listed and never opened.

What it takes from ezdxf: loading (strict, then the recovery mode for damaged files), the code-page decoding, the entity
classes, the extended-data accessors.  What it does itself, because the library's answers are not the ones a reader
needs: the value of a dimension (DIMLFAC, text override, ``<>``; group 42 is never trusted), the extents (a block that
contains itself must not hang it), and the title block.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter, OrderedDict
from typing import Any

from eng_toolkit.domains.drawing import conventions as C
from eng_toolkit.domains.drawing import dimensions as D
from eng_toolkit.domains.drawing import plane as P
from eng_toolkit.domains.drawing import reading as Rd
from eng_toolkit.domains.drawing import titleblock as T
from eng_toolkit.errors import EngineUnavailable, ToolFailure
from eng_toolkit.licenses import install_hint

MODULE = DIST = "ezdxf"
RELEASES = {
    "AC1006": "R10",
    "AC1009": "R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}
LENGTH_TYPES = ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE")
GEOMETRY_TYPES = ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE", "INSERT", "SOLID", "TRACE", "3DFACE", "POINT", "LEADER", "HATCH")
VIEW_SKIP_LAYER = re.compile(r"^(FRAME|BORDER|TITLE|TTL|TB|DIM|DIMS|DIMENSION|DIMENSIONS|TEXT|NOTES?|ANNO\w*|DEFPOINTS|CENTER|CENTRE)", re.I)
MAX_AUDIT_MESSAGES = 8
MAX_DECIMALS = 16
MAX_ATTRIBS = 200  # per block reference, in the full result
MAX_WARNINGS = 30
UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
# dimension style variables: name -> DXF group code (the same codes in the DIMSTYLE table and in a DSTYLE override)
DIM_CODES = {"dimlfac": 144, "dimdec": 271, "dimzin": 78, "dimrnd": 45, "dimdsep": 278, "dimpost": 3, "dimlunit": 277, "dimadec": 179}
HEADER_FOR = {"dimlfac": "$DIMLFAC", "dimdec": "$DIMDEC", "dimzin": "$DIMZIN", "dimrnd": "$DIMRND", "dimdsep": "$DIMDSEP", "dimpost": "$DIMPOST", "dimlunit": "$DIMLUNIT", "dimadec": "$DIMADEC"}


def require_engine():
    """Import ezdxf, or raise ``engine.unavailable`` with the install command; keeps ezdxf's logger off stderr."""
    try:
        import ezdxf
    except (ImportError, OSError) as exc:
        raise EngineUnavailable(MODULE, DIST, exc, install=install_hint(DIST)) from exc
    logger = logging.getLogger("ezdxf")
    if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
        logger.addHandler(logging.NullHandler())
    return ezdxf


MAX_STRING = 4000  # longest string in the full result; a file's text is cut here
MAX_MESSAGE = 500


def _short(value: Any, limit: int = MAX_MESSAGE) -> Any:
    return Rd.cut_text(value, limit) if isinstance(value, str) else value


def _corrupt(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("drawing.corrupt", _short(message), {k: _short(v) for k, v in details.items()})


def _too_large(message: str, **details: Any) -> ToolFailure:
    return ToolFailure("drawing.too_large", message, details)


# --------------------------------------------------------------------------- the file


def _check_file(path: str, limits: Rd.Limits) -> int:
    size = os.path.getsize(path)
    if size > limits.max_file_bytes:
        raise _too_large(
            f"the file is {size} bytes, over the {limits.max_file_bytes} byte limit for a drawing", bytes=size, limit_bytes=limits.max_file_bytes
        )
    with open(path, "rb") as handle:
        head = handle.read(16)
        if not head.startswith(b"AutoCAD Binary DXF"):
            handle.seek(0)
            records = len(RECORD_START.findall(handle.read()))  # lines that are the group code 0: at least the number of entities
            if records > limits.max_entities * 1.2 + 1000:
                raise _too_large(
                    f"the file has {records} records, far more than the limit of {limits.max_entities} entities allows", records=records, limit=limits.max_entities
                )
    if head.startswith(b"%PDF"):
        raise ToolFailure("input.invalid", "the file is a PDF although its name says .dxf; read it as a .pdf", {"path": path})
    if re.match(rb"AC10\d\d", head) and b"\n" not in head[:6]:
        raise ToolFailure(
            "input.unsupported_format",
            "this is a DWG file, not a DXF; convert it to DXF first (for example with LibreDWG's dwg2dxf) and read the DXF",
            {"path": path, "format": "dwg"},
        )
    return size


RAW_HEADER_BYTES = 2_000_000
RECORD_START = re.compile(rb"(?:^|[\r\n])[ \t]*0[ \t]*(?=[\r\n])")


def _raw_header(path: str) -> dict[str, str] | None:
    """The first value of every variable in the file's own HEADER section, read as text without the library, or None for a binary DXF.

    The library fills the header variables a file does not have with defaults (a file with no HEADER section reports
    ``$INSUNITS`` 6, meters), so what the file says about its units, and about the dimension variables, is read here.
    """
    with open(path, "rb") as handle:
        head = handle.read(RAW_HEADER_BYTES)
    if head.startswith(b"AutoCAD Binary DXF"):
        return None
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    lines = re.split(r"\r\n|\r|\n", head.decode("latin-1"))  # not splitlines(): that also splits at U+0085 and other characters a string may hold
    out: dict[str, str] = {}
    in_header, current = False, None
    for i in range(0, len(lines) - 1, 2):
        code, value = lines[i].strip(), lines[i + 1].strip()
        if code == "0" and value == "SECTION" and i + 3 < len(lines):
            in_header = lines[i + 2].strip() == "2" and lines[i + 3].strip() == "HEADER"
            current = None
        elif code == "0" and value == "ENDSEC":
            if in_header:
                break
        elif in_header and code == "9":
            current = value
        elif in_header and current is not None and current not in out:
            out[current] = value
    return out


def _number(raw: dict[str, str] | None, name: str, cast=int):
    """A numeric header variable from the raw header, None when the file does not have it."""
    if raw is None or name not in raw:
        return None
    try:
        if cast is int:
            value = float(raw[name])
            return int(value) if math.isfinite(value) and value == int(value) else None  # 4.9 is not a unit code
        return cast(raw[name])
    except (ValueError, OverflowError):
        return None


CLOSERS = {b"SECTION": b"ENDSEC", b"TABLE": b"ENDTAB", b"BLOCK": b"ENDBLK"}


def _salvage(path: str) -> bytes | None:
    """The text of a DXF file cut off in the middle, completed: the last record (possibly half written) is dropped and
    every section, table and block left open is closed.  None when the file has no records at all."""
    lines = open(path, "rb").read().splitlines()
    if len(lines) % 2:
        lines.pop()
    starts = [i for i in range(0, len(lines) - 1, 2) if lines[i].strip() == b"0"]
    if not starts:
        return None
    kept = lines[: starts[-1]]
    open_items: list[bytes] = []
    for i in range(0, len(kept) - 1, 2):
        if kept[i].strip() != b"0":
            continue
        word = kept[i + 1].strip()
        if word in CLOSERS:
            open_items.append(CLOSERS[word])
        elif word in CLOSERS.values() and word in open_items:
            while open_items and open_items.pop() != word:
                pass
    for closer in reversed(open_items):
        kept += [b"0", closer]
    kept += [b"0", b"EOF"]
    return b"\n".join(kept) + b"\n"


def _load(ezdxf, path: str):
    """The document, how it was repaired (None, "truncated" or "recovered") and the audit messages."""
    import tempfile

    from ezdxf import recover

    try:
        return ezdxf.readfile(path), None, []
    except Exception as first:  # noqa: BLE001 - any parser complaint means: try the tolerant ways
        failure = f"{type(first).__name__}: {first}"
    salvaged = _salvage(path)
    if salvaged is not None:
        with tempfile.TemporaryDirectory(prefix="eng-toolkit-dxf-") as scratch:
            repaired = os.path.join(scratch, "salvaged.dxf")
            with open(repaired, "wb") as handle:
                handle.write(salvaged)
            try:
                return ezdxf.readfile(repaired), "truncated", [failure]
            except Exception:  # noqa: BLE001
                pass
    try:
        doc, auditor = recover.readfile(path)
    except Exception as second:  # noqa: BLE001
        raise _corrupt(f"not a readable DXF file: {type(second).__name__}: {second}", first_error=failure) from second
    messages = [str(m.message) for m in (*auditor.errors, *auditor.fixes)][:MAX_AUDIT_MESSAGES]
    return doc, "recovered", messages


def _encoding(doc, code: str) -> tuple[str, str]:
    if code >= "AC1021":
        return "utf-8", "DXF R2007 and later are UTF-8"
    page = doc.header.get("$DWGCODEPAGE")
    if page:
        return str(doc.encoding), f"$DWGCODEPAGE {page}"
    return str(doc.encoding), "no $DWGCODEPAGE in the file; the library default"


# --------------------------------------------------------------------------- text


def _clean(raw: str, *, mtext: bool = False) -> str:
    text = UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), raw or "")
    if mtext:
        from ezdxf.tools.text import plain_mtext

        text = plain_mtext(text, split=False)
    text = D.unescape_percent(text)
    return _short(" ".join(text.split()), MAX_STRING)


class NonFinite(ValueError):
    """A coordinate or length that is NaN or infinite."""


class _Notes:
    """What the reader noticed about the entities it passed: reported once, as a warning, at the end."""

    nonfinite = 0
    oblique = 0


_notes = _Notes()
OCS_KINDS = ("CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "TEXT", "SOLID", "TRACE", "INSERT")


def _xy(vec) -> tuple[float, float]:
    x, y = float(vec[0]), float(vec[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise NonFinite("non-finite coordinate")
    return (x, y)


def _extrusion_flip(e) -> int:
    """1 when the entity's plane is the XY plane, -1 when it is mirrored (extrusion 0 0 -1: x is negated), 0 for any other plane."""
    if e.dxftype() not in OCS_KINDS or not e.dxf.hasattr("extrusion"):
        return 1
    x, y, z = (float(v) for v in e.dxf.extrusion)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return 1 if z > 0 else -1
    _notes.oblique += 1
    return 0


def _leader_position(e) -> tuple[float, float] | None:
    """Where the text of a MULTILEADER sits, when the entity says."""
    try:
        return _xy(e.context.mtext.insert)
    except Exception:  # noqa: BLE001
        try:
            return _xy(e.context.base_point)
        except Exception:  # noqa: BLE001
            return None


# --------------------------------------------------------------------------- geometry of one entity


def _geometry(e) -> tuple[float, list[P.Pt]]:
    """(length, points) of an entity; length counts only the types in LENGTH_TYPES, points are what its extents are made of."""
    length, points = _plain_geometry(e)
    if not math.isfinite(length) or not all(math.isfinite(c) for p in points for c in p):
        raise NonFinite("non-finite length or coordinate")
    if _extrusion_flip(e) == -1:
        points = [(-x, y) for x, y in points]
    return length, points


def _plain_geometry(e) -> tuple[float, list[P.Pt]]:
    kind = e.dxftype()
    d = e.dxf
    if kind == "LINE":
        a, b = _xy(d.start), _xy(d.end)
        return math.dist(a, b), [a, b]
    if kind == "CIRCLE":
        c = _xy(d.center)
        return 2.0 * math.pi * d.radius, P.circle_points(c[0], c[1], d.radius)
    if kind == "ARC":
        c = _xy(d.center)
        sweep = (d.end_angle - d.start_angle) % 360.0
        return math.radians(sweep) * d.radius, P.arc_points(c[0], c[1], d.radius, d.start_angle, sweep)
    if kind == "LWPOLYLINE":
        vertices = [(float(x), float(y), float(b)) for x, y, b in e.get_points("xyb")]
        if not vertices:
            return 0.0, []
        return P.polyline(vertices, bool(e.closed))
    if kind == "POLYLINE":
        rows = [(*_xy(v.dxf.location), float(v.dxf.get("bulge", 0.0))) for v in e.vertices if not getattr(v, "is_face_record", False)]
        if not rows:
            return 0.0, []
        length, points = P.polyline(rows, bool(e.is_closed))
        return (length if e.is_2d_polyline else 0.0), points
    if kind == "ELLIPSE":
        c = _xy(d.center)
        return P.ellipse_points(c[0], c[1], _xy(d.major_axis), d.ratio, d.start_param, d.end_param)
    if kind == "SPLINE":
        return 0.0, [_xy(p) for p in e.control_points]
    if kind == "POINT":
        return 0.0, [_xy(d.location)]
    if kind in ("SOLID", "TRACE", "3DFACE"):
        return 0.0, [_xy(d.get(f"vtx{i}")) for i in range(4) if d.hasattr(f"vtx{i}")]
    if kind == "LEADER":
        return 0.0, [_xy(p) for p in e.vertices]
    if kind in ("TEXT", "MTEXT"):
        return 0.0, [_xy(d.insert)]
    return 0.0, []


# --------------------------------------------------------------------------- blocks and extents


class Blocks:
    """Block geometry as convex hulls of points, computed once per block, so a block that contains ten copies of the next
    one, twenty deep, costs twenty hulls and not ten to the twentieth visits."""

    def __init__(self, doc, limits: Rd.Limits, warnings: list[str]):
        self.doc, self.limits, self.warnings = doc, limits, warnings
        self.hulls: dict[str, list[P.Pt]] = {}
        self.cycles: list[list[str]] = []
        self.spent = 0
        self.truncated_depth = False
        self.out_of_budget = False

    def max_nesting(self) -> int:
        """Longest chain of block-in-block over the whole block table; iterative, so no depth of nesting can overflow the stack."""
        children: dict[str, list[str]] = {}
        for block in self.doc.blocks:
            if block.name.lower().startswith(("*model_space", "*paper_space")):
                continue  # the layouts are where the drawing starts, not blocks nested in anything
            children[block.name] = sorted({e.dxf.name for e in block if e.dxftype() == "INSERT" and e.dxf.hasattr("name")})
        depth: dict[str, int] = {}
        for root in children:
            if root in depth:
                continue
            stack, iters, on_path = [root], {root: iter(children[root])}, {root}
            while stack:
                name = stack[-1]
                for child in iters[name]:
                    if child in on_path or child in depth or child not in children:
                        continue  # a cycle (counted elsewhere), a finished block, or a reference to a block that does not exist
                    stack.append(child)
                    iters[child] = iter(children[child])
                    on_path.add(child)
                    break
                else:
                    stack.pop()
                    on_path.discard(name)
                    depth[name] = 1 + max((depth[c] for c in children[name] if c in depth), default=0)
        return max(depth.values(), default=0)

    def hull_of(self, name: str, path: tuple[str, ...] = ()) -> list[P.Pt]:
        if name in path:
            cycle = [*path[path.index(name) :], name]
            if cycle not in self.cycles:
                self.cycles.append(cycle)
            return []
        if len(path) >= self.limits.max_nesting:
            self.truncated_depth = True
            return []
        if name in self.hulls:
            return self.hulls[name]
        try:
            block = self.doc.blocks.get(name)
        except Exception:  # noqa: BLE001
            block = None
        if block is None:  # a reference to a block that does not exist
            self.hulls[name] = []
            return []
        points: list[P.Pt] = []
        for e in block:
            points += self.entity_points(e, (*path, name))
            if self.out_of_budget:
                break
        result = P.hull(points)
        self.hulls[name] = result
        return result

    def entity_points(self, e, path: tuple[str, ...]) -> list[P.Pt]:
        kind = e.dxftype()
        if kind == "INSERT":
            return self.insert_points(e, path)
        if kind == "DIMENSION":
            pts = [_xy(e.dxf.get(a)) for a in ("defpoint", "defpoint2", "defpoint3", "defpoint4") if e.dxf.hasattr(a)]
            if e.dxf.hasattr("geometry") and e.dxf.geometry:
                pts += self.hull_of(e.dxf.geometry, path)
            return pts
        try:
            return _geometry(e)[1]
        except NonFinite:
            _notes.nonfinite += 1
            return []
        except Exception:  # noqa: BLE001 - an entity with broken data contributes nothing
            return []

    def insert_points(self, e, path: tuple[str, ...]) -> list[P.Pt]:
        d = e.dxf
        _extrusion_flip(e)  # counts a block reference in a tilted plane (placed as if it were not); a mirrored one is not handled either
        local = self.hull_of(d.name, path)
        if not local:
            return []
        block_base = (0.0, 0.0)
        try:
            block_base = _xy(self.doc.blocks.get(d.name).block.dxf.base_point)
        except Exception:  # noqa: BLE001
            pass
        columns, rows = max(int(d.get("column_count", 1)), 1), max(int(d.get("row_count", 1)), 1)
        cs, rs = float(d.get("column_spacing", 0.0)), float(d.get("row_spacing", 0.0))
        rotation = float(d.get("rotation", 0.0))
        c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
        at = _xy(d.insert)
        out: list[P.Pt] = []
        # the union of an array is the union of its four corner instances
        for i in sorted({0, columns - 1}):
            for j in sorted({0, rows - 1}):
                offset = (i * cs * c - j * rs * s, i * cs * s + j * rs * c)
                m = P.compose((float(d.get("xscale", 1.0)), float(d.get("yscale", 1.0))), rotation, (at[0] + offset[0], at[1] + offset[1]), block_base)
                self.spent += len(local)
                if self.spent > self.limits.max_transformed_points:
                    self.out_of_budget = True
                    return out
                out += [P.apply(m, p) for p in local]
        return out


# --------------------------------------------------------------------------- dimensions


def _dstyle_override(e) -> dict[int, Any]:
    """The dimension variables stored in the entity's own DSTYLE extended data: group code -> value."""
    try:
        tags = list(e.get_xdata("ACAD"))
    except Exception:  # noqa: BLE001 - no extended data of that application
        return {}
    found: dict[int, Any] = {}
    i = 0
    while i < len(tags) and not (tags[i].code == 1000 and str(tags[i].value).strip() == "DSTYLE"):
        i += 1
    i += 1
    if i < len(tags) and tags[i].code == 1002 and tags[i].value == "{":
        i += 1
        while i + 1 < len(tags) and not (tags[i].code == 1002 and tags[i].value == "}"):
            if tags[i].code == 1070:
                found[int(tags[i].value)] = tags[i + 1].value
                i += 2
            else:
                i += 1
    return found


class DimContext:
    def __init__(self, doc, header: dict[str, str] | None, units: dict[str, Any], warnings: list[str]):
        self.doc, self.header, self.units, self.warnings = doc, header, units, warnings
        self._block_texts: dict[str, str | None] = {}

    def style_value(self, style, name: str):
        """(value, origin) from the dimension style table entry, else from the header (the current style's value, which a
        file with a minimal or missing DIMSTYLE table relies on); (None, "default") when neither says."""
        if style is not None and style.dxf.hasattr(name):
            return style.dxf.get(name), "dimension style"
        if self.header is None:  # a binary DXF: the library's header is all there is
            header = self.doc.header.get(HEADER_FOR[name])
        else:
            header = self.header.get(HEADER_FOR[name])
            if header is not None and name != "dimpost":
                header = float(header) if name in ("dimlfac", "dimrnd") else int(float(header))
        if header is not None:
            return header, "header"
        return None, "default"

    def row(self, e, space: str, paper: bool, mm_per_unit: float | None) -> dict[str, Any]:
        d = e.dxf
        code = int(d.get("dimtype", 0))
        kind = D.kind_of(code)
        pts = {c: tuple(float(v) for v in d.get(a)) for c, a in ((10, "defpoint"), (13, "defpoint2"), (14, "defpoint3"), (15, "defpoint4"), (16, "defpoint5")) if d.hasattr(a)}
        rotation = float(d.get("angle", 0.0))
        measurement = D.measure(kind, pts, rotation_deg=rotation, x_type=D.is_ordinate_x(code))
        if measurement.value is not None and not math.isfinite(measurement.value):
            measurement = D.Measurement(None, measurement.unit)
        override = _dstyle_override(e)
        style_name = d.get("dimstyle", "STANDARD")
        try:
            style = self.doc.dimstyles.get(style_name)
        except Exception:  # noqa: BLE001
            style = None

        def pick(name: str):
            if DIM_CODES[name] in override:
                return override[DIM_CODES[name]], "entity DSTYLE override"
            return self.style_value(style, name)

        lfac_value, lfac_origin = pick("dimlfac")
        try:
            finite = lfac_value is None or math.isfinite(float(lfac_value))
        except (TypeError, ValueError):
            finite = False
        if not finite:
            self.warnings.append(f"dimension {e.dxf.handle}: DIMLFAC {_short(str(lfac_value), 40)} is not a finite number and was not applied")
            lfac_value = None
        lfac = D.resolve_dimlfac(
            override=float(lfac_value) if lfac_origin == "entity DSTYLE override" and lfac_value is not None else None,
            style=float(lfac_value) if lfac_origin != "entity DSTYLE override" and lfac_value is not None else None,
            paper_space=paper,
        )
        source = lfac.source
        if lfac_origin == "header" and lfac.source == "dimension style":
            source = "header $DIMLFAC (dimension style %s does not set it)" % style_name
        if lfac.note:
            self.warnings.append(f"dimension {e.dxf.handle}: {lfac.note}")
        if self.units["mm_per_unit"] and lfac.factor != 1.0 and any(abs(lfac.factor / f - 1.0) < 1e-6 for f in (25.4, 1.0 / 25.4)):
            self.warnings.append(
                f"dimension {e.dxf.handle}: DIMLFAC {lfac.factor:g} looks like an inch/millimetre unit conversion, so the value shown may already be in the "
                "other unit and value_mm may be wrong; compare it with the text"
            )

        def number(name: str, default, cast=float):
            raw = pick(name)[0]
            try:
                value = default if raw is None else cast(raw)
            except (TypeError, ValueError, OverflowError):
                return default
            return value if not isinstance(value, float) or math.isfinite(value) else default

        dsep = number("dimdsep", 46, int)
        style_text = D.TextStyle(
            dec=max(0, min(number("dimdec", 4, int), MAX_DECIMALS)),
            zin=number("dimzin", 0, int),
            rnd=number("dimrnd", 0.0),
            dsep=chr(dsep) if 32 <= dsep < 127 else ".",
            post=_short(str(pick("dimpost")[0] or ""), 80),
            adec=max(-1, min(number("dimadec", -1, int), MAX_DECIMALS)),
            lunit=number("dimlunit", 2, int),
        )
        value = None if measurement.value is None else D.displayed(measurement.value, measurement.unit, lfac.factor)
        formatted = None if value is None else D.format_value(value, measurement.unit, style_text, kind=kind)
        raw_override = d.get("text", "")
        override_text = _clean(raw_override, mtext=True) if raw_override.strip() else raw_override
        shown = D.apply_override(override_text, formatted if formatted is not None else "") if (formatted is not None or override_text.strip()) else None
        computed = shown.text if shown else None
        block_text = self.block_text(d.get("geometry", ""))

        if shown is not None and shown.hidden:
            text, source_of_text, hidden = "", "override", True
        elif block_text is not None:
            text, source_of_text, hidden = block_text, "block", False
        elif computed is not None:
            text, source_of_text, hidden = computed, "override" if (override_text and override_text != "<>" and override_text != " ") else "computed", False
        else:
            text, source_of_text, hidden = None, "unavailable", False
        if block_text is not None and computed is not None and not hidden and _differs(block_text, computed):
            self.warnings.append(f"dimension {d.handle}: the text drawn in the dimension block ({block_text!r}) differs from the value computed from its points and DIMLFAC ({computed!r})")
        return {
            "handle": str(d.handle),
            "kind": kind,
            "layer": d.layer,
            "space": space,
            "dimstyle": style_name,
            "at": list(_xy(d.defpoint)) if d.hasattr("defpoint") else None,
            "measured": None if measurement.value is None else Rd.round_float(measurement.value),
            "measured_unit": measurement.unit,
            "measured_mm": Rd.mm(measurement.value, mm_per_unit) if measurement.unit == "length" else None,
            "dimlfac": lfac.factor,
            "dimlfac_source": source,
            "value": None if value is None else Rd.round_float(value),
            "value_mm": Rd.mm(value, mm_per_unit) if measurement.unit == "length" else None,
            "text": text,
            "text_source": source_of_text,
            "text_override": raw_override if raw_override.strip() or raw_override == " " else None,
            "computed_text": computed,
            "block_text": block_text,
            "hidden": hidden,
        }

    def block_text(self, name: str) -> str | None:
        """The text drawn in a dimension's anonymous block: what a viewer shows."""
        if not name:
            return None
        if name in self._block_texts:
            return self._block_texts[name]
        try:
            block = self.doc.blocks.get(name)
        except Exception:  # noqa: BLE001
            block = None
        parts = []
        for e in block or ():
            if e.dxftype() == "MTEXT":
                parts.append(_clean(e.text, mtext=True))
            elif e.dxftype() == "TEXT":
                parts.append(_clean(e.dxf.text))
        parts = [p for p in parts if p]
        text = _short(" ".join(parts), MAX_STRING) if parts else None
        self._block_texts[name] = text  # a dimension block is shared by many dimensions
        return text


def _differs(block_text: str, computed: str) -> bool:
    a, b = block_text.replace(" ", "").casefold(), computed.replace(" ", "").casefold()
    return a != b and not a.startswith(b)


# --------------------------------------------------------------------------- the reader


def _layout_space(layout) -> str:
    return "model" if layout.is_modelspace else "paper"


def read(path: str, limits_dict: dict[str, int] | None = None) -> dict[str, Any]:
    ezdxf = require_engine()
    _notes.nonfinite = _notes.oblique = 0
    limits = Rd.Limits.from_dict(limits_dict)
    size = _check_file(path, limits)
    doc, repair, audit = _load(ezdxf, path)
    recovered = repair is not None
    warnings: list[str] = []
    if repair == "truncated":
        warnings.append(
            "The file is damaged: it ends in the middle of its data. It was read up to the last complete record and the "
            "open sections were closed, so everything after the cut is missing from every number here."
        )
    elif repair == "recovered":
        warnings.append(
            "The file is damaged and was read in recovery mode, so some content may be missing or repaired"
            + (": " + "; ".join(audit) if audit else "")
        )
    if recovered and not any(len(layout) for layout in doc.layouts):
        raise _corrupt("the file is damaged and no entities could be recovered from it", repair=repair)
    total_in_file = len(doc.entitydb)
    if total_in_file > limits.max_entities:
        raise _too_large(
            f"the file holds {total_in_file} entities, over the limit of {limits.max_entities}", entities=total_in_file, limit=limits.max_entities
        )

    header = _raw_header(path)
    code = str((header or {}).get("$ACADVER") or doc.header.get("$ACADVER", doc.dxfversion))
    loaded = str(getattr(doc, "loaded_dxfversion", None) or code)
    encoding, encoding_source = _encoding(doc, loaded)
    if header is None:
        insunits, measurement = doc.header.get("$INSUNITS"), doc.header.get("$MEASUREMENT")
    else:
        insunits, measurement = _number(header, "$INSUNITS"), _number(header, "$MEASUREMENT")
    units, unit_warnings = Rd.units_info(insunits, measurement)
    warnings += unit_warnings
    mm_per_unit = units["mm_per_unit"]

    blocks = Blocks(doc, limits, warnings)
    ctx = DimContext(doc, header, units, warnings)

    layer_rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    layer_key: dict[str, str] = {}  # layer names are case-insensitive: folded name -> the name first seen
    for layer in doc.layers:
        layer_key.setdefault(layer.dxf.name.casefold(), layer.dxf.name)
        layer_rows[layer.dxf.name] = {
            "name": layer.dxf.name,
            "entities": 0,
            "length": 0.0,
            "color": abs(int(layer.dxf.get("color", 7))),
            "linetype": layer.dxf.get("linetype", "CONTINUOUS"),
            "on": not layer.is_off(),
            "frozen": bool(layer.is_frozen()),
            "locked": bool(layer.is_locked()),
            "declared": True,
        }
    type_counts: Counter[str] = Counter()
    space_counts = {"model": 0, "paper": 0}
    layouts: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    text_recs: list[T.TextRec] = []
    attrib_blocks: list[T.AttribBlock] = []
    with_attributes: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    images: list[dict[str, str]] = []
    insert_count = 0
    totals = {"texts": 0, "dimensions": 0, "with_attributes": 0}
    box: dict[str, P.Box | None] = {"model": None, "paper": None}
    pieces: list[tuple[P.Box, str]] = []  # (box, layer) of geometry in model space, for view clustering
    cap = limits.max_list_items

    for layout in doc.layouts:
        space = _layout_space(layout)
        count = 0
        for e in layout:
            count += 1
            kind = e.dxftype()
            layer_name = layer_key.setdefault(e.dxf.get("layer", "0").casefold(), e.dxf.get("layer", "0"))
            type_counts[kind] += 1
            row = layer_rows.get(layer_name)
            if row is None:
                row = layer_rows[layer_name] = {"name": layer_name, "entities": 0, "length": 0.0, "color": None, "linetype": None, "on": None, "frozen": None, "locked": None, "declared": False}
            row["entities"] += 1
            try:
                if kind == "INSERT":
                    insert_count += 1
                    pts = blocks.insert_points(e, ())
                    attribs = [(_short(a.dxf.tag, 80), _clean(a.dxf.text)) for a in e.attribs][:MAX_ATTRIBS]
                    if attribs:
                        totals["with_attributes"] += 1
                        with_attributes.append({"block": e.dxf.name, "layer": layer_name, "at": list(_xy(e.dxf.insert)), "space": space, "attribs": dict(attribs), "attribs_total": len(e.attribs)} if len(with_attributes) < cap else None)
                        attrib_blocks.append(T.AttribBlock(e.dxf.name, layer_name, attribs))
                        for a in list(e.attribs)[:MAX_ATTRIBS]:
                            totals["texts"] += 1
                            if len(texts) < cap:
                                texts.append({"text": _clean(a.dxf.text), "layer": a.dxf.get("layer", layer_name), "at": list(_xy(a.dxf.insert)), "height": float(a.dxf.get("height", 0.0)), "kind": "ATTRIB", "tag": a.dxf.tag, "space": space})
                elif kind == "DIMENSION":
                    pts = blocks.entity_points(e, ())
                    totals["dimensions"] += 1
                    if len(dimensions) < cap:
                        dimensions.append(ctx.row(e, space, space == "paper", mm_per_unit))
                elif kind in ("TEXT", "MTEXT"):
                    mtext = kind == "MTEXT"
                    cleaned = _clean(e.text if mtext else e.dxf.text, mtext=mtext)
                    at = _xy(e.dxf.insert)
                    if kind == "TEXT" and e.dxf.hasattr("align_point") and (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)):
                        at = _xy(e.dxf.align_point)
                    height = float(e.dxf.get("char_height" if mtext else "height", 0.0))
                    if cleaned:
                        totals["texts"] += 1
                    if cleaned and len(texts) < cap:
                        texts.append({"text": cleaned, "layer": layer_name, "at": list(at), "height": height, "kind": kind, "space": space})
                    if cleaned:
                        text_recs.append(T.TextRec(cleaned, at[0], at[1], height, layer_name))
                    pts = [at]
                elif kind in ("MULTILEADER", "TOLERANCE"):
                    # callouts ("4X dia 6.6 THRU") and feature control frames: their text is content; GD&T symbols are font letters and are not interpreted
                    raw = e.get_mtext_content() if kind == "MULTILEADER" else e.dxf.get("content", "")
                    cleaned = _clean(raw, mtext=True)
                    where = _leader_position(e) if kind == "MULTILEADER" else _xy(e.dxf.insert)
                    pts = [where] if where else []
                    if cleaned:
                        totals["texts"] += 1
                        if len(texts) < cap:
                            texts.append({"text": cleaned, "layer": layer_name, "at": list(where) if where else None, "height": None, "kind": kind, "space": space})
                elif kind == "IMAGE":
                    pts = []
                    handle = e.dxf.get("image_def_handle")
                    image_def = doc.entitydb.get(handle) if handle else None
                    if image_def is not None and len(images) < cap:
                        images.append({"layer": layer_name, "path": str(image_def.dxf.get("filename", ""))})
                else:
                    length, pts = _geometry(e)
                    row["length"] += length
            except NonFinite:
                pts = []
                _notes.nonfinite += 1
            except Exception as exc:  # noqa: BLE001 - one broken entity must not lose the drawing
                pts = []
                warnings.append(f"{kind} {e.dxf.get('handle', '?')} could not be read ({type(exc).__name__}) and was skipped")
            piece = P.box_of(pts)
            box[space] = P.union(box[space], piece)
            if piece and space == "model" and kind in GEOMETRY_TYPES:
                pieces.append((piece, layer_name))
        space_counts[space] += count
        layouts.append({"name": layout.name, "space": space, "entities": count})

    # --- blocks, external references, nesting
    xrefs = []
    definitions = 0
    for block in doc.blocks:
        name = block.name
        if name.startswith("*"):
            continue
        definitions += 1
        flags = int(block.block.dxf.get("flags", 0))
        if flags & 12:
            xrefs.append({"name": name, "path": str(block.block.dxf.get("xref_path", ""))})
    nesting = blocks.max_nesting()
    for ref in xrefs:
        warnings.append(f"external reference {_short(ref['name'], 80)!r} ({_short(ref['path'], 200)!r}) is not loaded: nothing outside this file is opened or followed, so what it draws is missing from every number here")
    for cycle in blocks.cycles:
        warnings.append("a block refers back to itself: " + " -> ".join(cycle) + "; the repeated reference was not followed")
    if blocks.truncated_depth or nesting > limits.max_nesting:
        warnings.append(f"block nesting is {nesting} deep, deeper than {limits.max_nesting}: content below that depth is not in the extents")
    if blocks.out_of_budget:
        warnings.append("the block references expand to more points than the work limit allows: the extents are incomplete")
    for image in images:
        warnings.append(f"image reference {_short(image['path'], 200)!r} is listed, not opened")
    if _notes.nonfinite:
        warnings.append(f"{_notes.nonfinite} entities have non-finite (NaN or infinite) coordinates or lengths and were ignored")
    if _notes.oblique:
        warnings.append(f"{_notes.oblique} entities lie in a plane other than XY (an extrusion direction that is neither the Z axis nor its mirror): their extents are approximate")

    extents: dict[str, Any] = {}
    for space in ("model", "paper"):
        b = box[space]
        if b is None:
            extents[space] = None
            continue
        size_xy = [b[2] - b[0], b[3] - b[1]]
        extents[space] = {
            "min": [Rd.round_float(b[0]), Rd.round_float(b[1])],
            "max": [Rd.round_float(b[2]), Rd.round_float(b[3])],
            "size": [Rd.round_float(v) for v in size_xy],
            "size_mm": [Rd.mm(v, mm_per_unit) for v in size_xy] if mm_per_unit else None,
            "complete": not (blocks.out_of_budget or blocks.truncated_depth or bool(blocks.cycles)),
            "method": "geometry points (arcs and circles sampled every 15 degrees plus axis extremes; text is its insertion point; hatches, images and viewports are not included)",
        }

    layers_out = []
    for row in layer_rows.values():
        row["length"] = Rd.round_float(row["length"])
        row["length_mm"] = Rd.mm(row["length"], mm_per_unit)
        layers_out.append(row)

    tb = T.build(attrib_blocks, text_recs)
    title_block = {
        "method": tb.method,
        "block": tb.block,
        "fields": {k: {"value": f.value, "source": f.source, "inferred": f.inferred, "confidence": f.confidence} for k, f in tb.fields.items()},
        "status": tb.status,
        "conflicts": tb.conflicts,
        "unmatched_attributes": [{"tag": t, "value": v} for t, v in tb.unmatched_attributes],
        "empty_labels": tb.empty_labels,
        "limited": tb.limited,
    }
    if tb.limited:
        warnings.append(f"the drawing has more than {T.MAX_LABELS} label-like or {T.MAX_CANDIDATES} other texts: the title block was searched in the first of them only")

    dimstyle_names = {s.dxf.name for s in doc.dimstyles}
    layer_names = set(layer_rows)
    recognised = "eng-toolkit drawing_make_views" if {l.name for l in C.LAYERS} <= layer_names and C.DIMSTYLE in dimstyle_names and (tb.status or "").startswith("UNREVIEWED") else None

    result: dict[str, Any] = {
        "kind": "dxf",
        "file": {"name": os.path.basename(path), "bytes": size},
        "dxf": {"version_code": code, "release": RELEASES.get(code, "unknown"), "loaded_as": loaded, "encoding": encoding, "encoding_source": encoding_source, "recovered": recovered, "audit": audit},
        "units": units,
        "extents": extents,
        "layouts": layouts,
        "entities": {"total": space_counts["model"] + space_counts["paper"], "model": space_counts["model"], "paper": space_counts["paper"], "by_type": dict(type_counts), "length_counts": list(LENGTH_TYPES)},
        "layers": layers_out,
        "texts": texts,
        "texts_total": totals["texts"],
        "dimensions_total": totals["dimensions"],
        "dimensions": dimensions,
        "blocks": {"definitions": definitions, "inserts": insert_count, "max_nesting": nesting, "with_attributes": [w for w in with_attributes if w], "xrefs": xrefs, "cycles": blocks.cycles},
        "images": images,
        "title_block": title_block,
        "views": _views(pieces, box["model"]),
        "recognised_as": recognised,
        "warnings": list(dict.fromkeys(_short(w, 1000) for w in warnings))[:MAX_WARNINGS],
    }
    return _bound(result)


def _bound(value: Any) -> Any:
    """Every string of the result cut to MAX_STRING characters (a 10 MB text or a 3 MB layer name must not become a 10 MB answer)."""
    if isinstance(value, str):
        return _short(value, MAX_STRING)
    if isinstance(value, dict):
        return {_short(k, 200) if isinstance(k, str) else k: _bound(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound(v) for v in value]
    return value


def _views(pieces: list[tuple[P.Box, str]], overall: P.Box | None) -> dict[str, Any]:
    """Groups of geometry apart from each other: the views of a drawing.  A guess from positions, and said to be."""
    method = "clusters of geometry (not on sheet-furniture layers such as FRAME, TITLE, DIM, TEXT) that lie within 1.5% of the drawing size of each other"
    geometry = [(b, layer) for b, layer in pieces if not VIEW_SKIP_LAYER.match(layer)]
    if overall is None or not geometry:
        return {"count": 0, "inferred": True, "confidence": 0.0, "method": method, "items": []}
    union = None
    for b, _ in geometry:
        union = P.union(union, b)
    assert union is not None
    span = max(union[2] - union[0], union[3] - union[1])
    if len(geometry) > 1:  # a frame drawn on a layer of its own name is still a frame: drop what covers most of the geometry
        geometry = [(b, l) for b, l in geometry if not ((b[2] - b[0]) >= 0.9 * (union[2] - union[0]) and (b[3] - b[1]) >= 0.9 * (union[3] - union[1]))] or geometry
        union = None
        for b, _ in geometry:
            union = P.union(union, b)
        span = max(union[2] - union[0], union[3] - union[1])  # type: ignore[index]
    groups = P.cluster([b for b, _ in geometry], gap=0.015 * span)
    if groups is None:
        return {"count": None, "inferred": True, "confidence": 0.0, "method": method + "; too many separate groups to say", "items": []}
    items = [
        {"bbox": [Rd.round_float(v) for v in gbox], "entities": len(members), "layers": sorted({geometry[i][1] for i in members})}
        for gbox, members in sorted(groups, key=lambda g: (-g[0][3], g[0][0]))
    ]
    return {"count": len(items), "inferred": True, "confidence": 0.7, "method": method, "items": items}
