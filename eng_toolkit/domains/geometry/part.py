"""The part spec language: one module owns its grammar, its validation and its interpretation.

A spec is JSON, ``{"params": {...}, "features": [...]}``.  Every number is a unit string; ``$name`` refers to an
earlier param; an expression is evaluated by :func:`eng_toolkit.units.parse_quantity` (pint's parser, never ``eval``)
after :func:`check_expression` has refused the inputs that make pint compute for ever.  :data:`OPS` is the one registry
the parser, the tool description and the docs are generated from.  This module imports no engine: it calls
:mod:`eng_toolkit.brep`, which imports OpenCASCADE only when a model is built.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from eng_toolkit import brep
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import parse_quantity

MAX_SPEC_BYTES = 64 * 1024
MAX_FEATURES = 200
MAX_HOLE_POSITIONS = 1000
MAX_EXPRESSION_CHARS = 200
MAX_NESTING = 8
MAX_LITERAL = 1e9
MAX_EXPONENT = 6.0
MAX_LENGTH_MM = 1e6
MAX_TEXT = 64
MAX_PATH_TEXT = 512
MAX_PATTERN_COUNT = 256  # instances of one pattern, the original included
MAX_PATTERN_COPIES = 500  # copies made by all patterns of a spec together; each copy is one boolean
MAX_TAPER_RAD = math.radians(45.0)
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
REFERENCE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
TWO_PI = 2.0 * math.pi
AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
PLANES: tuple[str, ...] = ("xy", "xz", "yz")
REQUIRED: Any = object()  # a field with this default must be given; None means optional and absent


# --------------------------------------------------------------------------- errors


def _escape(key: Any) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _bad(pointer: str, message: str, allowed: Sequence[str] = (), **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", f"{pointer or '/'}: {message}", {"pointer": pointer, "allowed": list(allowed), **details})


# --------------------------------------------------------------------------- the expression guard


_EXPONENT = re.compile(r"\*\*|\^")
_EXPONENT_LITERAL = re.compile(r"\s*[+-]?\s*(\d+(?:\.\d+)?)(?![\w.(])")
_CHAINED = re.compile(r"\s*(?:\*\*|\^)")
_LITERAL = re.compile(r"(?<![\w$.])(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def check_expression(text: str, pointer: str, allowed: Sequence[str] = ()) -> None:
    """Refuse what makes pint compute for ever or for long, before pint sees it.

    ``9**9**9`` is a 369-million-digit integer.  So: at most 200 characters; parentheses nested at most 8 deep;
    every number at most 1e9 in magnitude; and every ``**`` or ``^`` followed by a plain number of at most 6 that
    is not itself raised to a power.
    """
    if len(text) > MAX_EXPRESSION_CHARS:
        raise _bad(pointer, f"an expression is at most {MAX_EXPRESSION_CHARS} characters, this one has {len(text)}", allowed, limit=MAX_EXPRESSION_CHARS)
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            if depth > MAX_NESTING:
                raise _bad(pointer, f"parentheses are nested more than {MAX_NESTING} deep", allowed, limit=MAX_NESTING)
        elif ch == ")":
            depth = max(0, depth - 1)
    for match in _LITERAL.finditer(text):
        if abs(float(match.group(0))) > MAX_LITERAL:
            raise _bad(pointer, f"the number {match.group(0)[:30]} is larger than {MAX_LITERAL:g}; write big lengths with a larger unit, e.g. '5 m'", allowed, limit=MAX_LITERAL)
    for match in _EXPONENT.finditer(text):
        rest = text[match.end() :]
        literal = _EXPONENT_LITERAL.match(rest)
        if literal is None or float(literal.group(1)) > MAX_EXPONENT or _CHAINED.match(rest[literal.end() :]):
            raise _bad(pointer, f"a power ('**' or '^') must be a plain number of at most {MAX_EXPONENT:g} and cannot be chained, e.g. 'x ** 2'", allowed, limit=MAX_EXPONENT)


def _clean(value: float) -> float:
    return float(f"{value:.12g}") + 0.0  # 12 significant digits: unit conversion noise would otherwise reach the hash


class Resolver:
    """Resolves ``$name`` parameters, then evaluates the quantity with ``units.parse_quantity`` (pint, no eval)."""

    def __init__(self, declared: Sequence[str] = ()):
        self._declared = set(declared)
        self._values: dict[str, Any] = {}
        self._defining = ""
        self.allowed: list[str] = []
        self.hole_positions = 0
        self.pattern_copies = 0
        self.names: dict[str, list[tuple[int, str, str, int]]] = {}  # feature name -> [(index, op, mode, hole entries)] of the features parsed so far
        self.referenced: set[str] = set()  # names used by of: terms, patterns and mirrors

    def tool_feature(self, name: str, pointer: str, *, sources_only: bool = False) -> tuple[int, str, str, int]:
        """The earlier feature a name refers to: a body, hole, pattern or mirror (``sources_only``: a body or hole that can be copied)."""
        entries = self.names.get(name)
        known = sorted(self.names)
        if not entries:
            raise _bad(pointer, f"{name!r} names no earlier feature; the earlier features are {known}", known)
        if len(entries) > 1:
            raise _bad(pointer, f"{name!r} names {len(entries)} features; give the one you mean a name of its own", known)
        index, op, mode, count = entries[0]
        if op in ("fillet", "chamfer", "shell"):
            raise _bad(pointer, f"{name!r} is a {op}, which has no tool shape; use a body, hole, pattern or mirror feature", known)
        if sources_only:
            if op in ("pattern", "mirror"):
                raise _bad(pointer, f"{name!r} is a {op}; copy the feature it copies, a body or a hole", known)
            if OPS[op].role == "body" and mode == "intersect":
                raise _bad(pointer, f"{name!r} has mode intersect, which cannot be copied; use mode add or cut", known)
        return entries[0]

    def _substitute(self, text: str, pointer: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self._values:
                value = self._values[name]
                return f"({float(value.magnitude)!r} * {value.units})"
            if name == self._defining:
                raise _bad(pointer, f"${name} refers to itself; parameters cannot use themselves, directly or in a cycle", self.allowed)
            if name in self._declared:
                raise _bad(pointer, f"${name} is defined later in params; a parameter can use only earlier ones (no forward references, no cycles)", self.allowed)
            raise _bad(pointer, f"${name} is not a parameter; the parameters defined here are {sorted(self._values)}", self.allowed)

        return REFERENCE.sub(replace, text)

    def quantity(self, raw: Any, pointer: str, dimension: str | None):
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise _bad(pointer, f"expected a unit string such as '10 mm', got {type(raw).__name__}", self.allowed)
        if isinstance(raw, str):
            if raw.lstrip().startswith("{"):
                raise _bad(pointer, "write a quantity as a string such as '10 mm', not as an object", self.allowed)
            check_expression(raw, pointer, self.allowed)
            text = self._substitute(raw, pointer)
        else:
            text = raw
        try:
            return parse_quantity(text, name="quantity", dimension=dimension)
        except ToolFailure as exc:
            details = {k: v for k, v in exc.details.items() if k != "param"}
            raise ToolFailure(exc.family, f"{pointer}: {exc.message}", {**details, "pointer": pointer, "allowed": list(self.allowed)}) from exc

    def length(self, raw: Any, pointer: str) -> float:
        """A length in millimetres."""
        value = _clean(float(self.quantity(raw, pointer, "length").to("millimeter").magnitude))
        if abs(value) > MAX_LENGTH_MM:
            raise _bad(pointer, f"{value:g} mm is beyond the supported size of {MAX_LENGTH_MM:g} mm", self.allowed, limit_mm=MAX_LENGTH_MM)
        return value

    def angle(self, raw: Any, pointer: str) -> float:
        """An angle in radians; a plain number or a length is refused."""
        value = self.quantity(raw, pointer, "dimensionless")
        if str(value.to_base_units().units) != "radian":
            raise ToolFailure(
                "units.bare_number",
                f"{pointer}: an angle needs an angle unit such as '90 deg' or '1.57 rad'",
                {"pointer": pointer, "allowed": list(self.allowed)},
            )
        return float(value.to("radian").magnitude) + 0.0

    def define(self, name: str, raw: Any, pointer: str) -> None:
        self._defining = name
        value = self.quantity(raw, pointer, None)
        self._defining = ""
        if not (value.check("[length]") or str(value.to_base_units().units) == "radian"):
            raise _bad(pointer, f"a parameter must be a length such as '10 mm' or an angle such as '45 deg', got {raw!r}", self.allowed)
        self._values[name] = value

    def params(self) -> dict[str, str]:
        """The parameters as canonical unit strings: lengths in mm, angles in rad."""
        out = {}
        for name, value in sorted(self._values.items()):
            out[name] = _length_text(_clean(float(value.to("millimeter").magnitude))) if value.check("[length]") else _angle_text(float(value.to("radian").magnitude))
        return out


def _length_text(mm: float) -> str:
    return f"{mm + 0.0!r} mm"


def _angle_text(rad: float) -> str:
    return f"{rad + 0.0!r} rad"


# --------------------------------------------------------------------------- field kinds


@dataclass(frozen=True)
class Kind:
    label: str  # the type name in the tool description
    parse: Callable[[Resolver, Any, str], Any]
    dump: Callable[[Any], Any]


@dataclass(frozen=True)
class Field:
    kind: str
    default: Any = REQUIRED
    default_text: str = ""  # shown after the type in the description when there is a default


def _list(rs: Resolver, raw: Any, pointer: str, count: int | None, shape: str) -> list:
    if not isinstance(raw, list) or (count is not None and len(raw) != count):
        raise _bad(pointer, f"expected {shape}", rs.allowed)
    return raw


def _length_kind(*, sign: str) -> Callable[[Resolver, Any, str], float]:
    def parse(rs: Resolver, raw: Any, pointer: str) -> float:
        value = rs.length(raw, pointer)
        if sign == "positive" and not value > 0.0:
            raise _bad(pointer, f"must be greater than 0, got {value:g} mm", rs.allowed)
        if sign == "nonnegative" and value < 0.0:
            raise _bad(pointer, f"must be 0 or more, got {value:g} mm", rs.allowed)
        if sign == "nonzero" and value == 0.0:
            raise _bad(pointer, "must not be 0", rs.allowed)
        return value

    return parse


def _vector_kind(count: int, *, positive: bool) -> Callable[[Resolver, Any, str], tuple[float, ...]]:
    shape = "a list " + ("[x, y, z]" if count == 3 else "[u, v]") + f" of {count} unit strings"
    item = _length_kind(sign="positive" if positive else "any")

    def parse(rs: Resolver, raw: Any, pointer: str) -> tuple[float, ...]:
        items = _list(rs, raw, pointer, count, shape)
        return tuple(item(rs, value, f"{pointer}/{i}") for i, value in enumerate(items))

    return parse


def _angle_kind(*, upper: float, upper_open: bool, words: str) -> Callable[[Resolver, Any, str], float]:
    def parse(rs: Resolver, raw: Any, pointer: str) -> float:
        value = rs.angle(raw, pointer)
        if value <= 0.0 or value > upper * (1.0 + 1e-9) or (upper_open and value >= upper):
            raise _bad(pointer, f"must be {words}, got {math.degrees(value):g} deg", rs.allowed)
        return min(value, upper)

    return parse


def _enum_kind(values: Sequence[str]) -> Callable[[Resolver, Any, str], str]:
    def parse(rs: Resolver, raw: Any, pointer: str) -> str:
        if not isinstance(raw, str) or raw not in values:
            raise _bad(pointer, f"{raw!r} is not valid; use one of {list(values)}", values)
        return raw

    return parse


def _parse_depth(rs: Resolver, raw: Any, pointer: str) -> float | None:
    if isinstance(raw, str) and raw.strip().lower() == "thru":
        return None
    return _length_kind(sign="positive")(rs, raw, pointer)


def _parse_flag(rs: Resolver, raw: Any, pointer: str) -> bool:
    if not isinstance(raw, bool):
        raise _bad(pointer, f"expected true or false, got {raw!r}", ["true", "false"])
    return raw


def _parse_text(rs: Resolver, raw: Any, pointer: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_TEXT or not raw.isprintable():
        raise _bad(pointer, f"expected a short text of 1 to {MAX_TEXT} printable characters, got {raw!r:.80}", rs.allowed)
    return raw.strip()


def _of_terms(rs: Resolver, selector: brep.EdgeSelector | brep.FaceSelector, pointer: str) -> None:
    for name in brep.selector_features(selector):
        rs.tool_feature(name, pointer)
        rs.referenced.add(name)


def _parse_selector(rs: Resolver, raw: Any, pointer: str) -> brep.EdgeSelector:
    try:
        selector = brep.parse_edge_selector(raw)
    except ToolFailure as exc:
        raise _bad(pointer, exc.message, [*brep.EDGE_TERMS, "of:<feature name>"], **{k: v for k, v in exc.details.items() if k == "limit"}) from exc
    _of_terms(rs, selector, pointer)
    return selector


def _parse_face_selector(rs: Resolver, raw: Any, pointer: str) -> brep.FaceSelector:
    try:
        selector = brep.parse_face_selector(raw)
    except ToolFailure as exc:
        raise _bad(pointer, exc.message, [*brep.FACE_TERMS, "of:<feature name>"], **{k: v for k, v in exc.details.items() if k == "limit"}) from exc
    _of_terms(rs, selector, pointer)
    return selector


def _parse_count(rs: Resolver, raw: Any, pointer: str) -> int:
    whole = isinstance(raw, int) and not isinstance(raw, bool) or (isinstance(raw, float) and math.isfinite(raw) and raw == int(raw))
    if not whole:
        raise _bad(pointer, f"expected a whole number of instances such as 4, got {raw!r:.40}", rs.allowed)
    if not 2 <= raw <= MAX_PATTERN_COUNT:
        raise _bad(pointer, f"a pattern has 2 to {MAX_PATTERN_COUNT} instances (the original counts as one), got {raw:g}", rs.allowed, limit=MAX_PATTERN_COUNT)
    return int(raw)


def _parse_ref(rs: Resolver, raw: Any, pointer: str) -> str:
    if not isinstance(raw, str) or not NAME.fullmatch(raw):
        raise _bad(pointer, f"expected the name of an earlier feature, got {raw!r:.80}", sorted(rs.names))
    rs.tool_feature(raw, pointer)  # exists and is unique; whether it can be copied is the op's check
    return raw


def _parse_path_text(rs: Resolver, raw: Any, pointer: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_PATH_TEXT or not raw.isprintable():
        raise _bad(pointer, f"expected the path of a model file (1 to {MAX_PATH_TEXT} printable characters), got {raw!r:.80}", rs.allowed)
    if not raw.strip().lower().endswith(brep.SOLID_EXTENSIONS):
        raise _bad(pointer, f"the file must end in one of {list(brep.SOLID_EXTENSIONS)}, got {raw.strip()!r:.80}", list(brep.SOLID_EXTENSIONS))
    return raw.strip()


def _parse_taper(rs: Resolver, raw: Any, pointer: str) -> float:
    value = rs.angle(raw, pointer)
    if value < 0.0:
        raise _bad(pointer, f"a taper narrows the walls along the extrusion and is 0 to 45 deg, got {math.degrees(value):g} deg; to widen, extrude the other way (negative distance) from the wider profile", rs.allowed, limit_deg=45)
    if value > MAX_TAPER_RAD * (1.0 + 1e-9):
        raise _bad(pointer, f"a taper is 0 to 45 deg, got {math.degrees(value):g} deg", rs.allowed, limit_deg=45)
    return value


SEGMENT_FIELDS: dict[str, Field] = {
    "to": Field("length2"),
    "radius": Field("length_pos", None),
    "turn": Field("turn", None),
    "via": Field("length2", None),
}


def _parse_segments(rs: Resolver, raw: Any, pointer: str) -> tuple[brep.PathSegment, ...]:
    items = _list(rs, raw, pointer, None, "a list of segments")
    if not 2 <= len(items) <= brep.MAX_PATH_SEGMENTS:
        raise _bad(pointer, f"a path has 2 to {brep.MAX_PATH_SEGMENTS} segments, this one has {len(items)}", rs.allowed)
    out = []
    for i, item in enumerate(items):
        values = _parse_object(rs, item, SEGMENT_FIELDS, f"{pointer}/{i}")
        if values["via"] is not None and (values["radius"] is not None or values["turn"] is not None):
            raise _bad(f"{pointer}/{i}/via", "an arc has either via (a point it passes through) or radius with an optional turn, not both", list(SEGMENT_FIELDS))
        if values["turn"] is not None and values["radius"] is None:
            raise _bad(f"{pointer}/{i}/turn", "turn needs a radius", list(SEGMENT_FIELDS))
        out.append(brep.PathSegment(to=values["to"], radius=values["radius"], turn=values["turn"] or "left", via=values["via"]))
    return tuple(out)


def _dump_segments(segments: Sequence[brep.PathSegment]) -> list[dict[str, Any]]:
    out = []
    for seg in segments:
        item: dict[str, Any] = {"to": _dump_vector(seg.to)}
        if seg.radius is not None:
            item["radius"] = _length_text(seg.radius)
            item["turn"] = seg.turn
        if seg.via is not None:
            item["via"] = _dump_vector(seg.via)
        out.append(item)
    return out


def _parse_points2(rs: Resolver, raw: Any, pointer: str) -> tuple[tuple[float, float], ...]:
    items = _list(rs, raw, pointer, None, "a list of at least 3 points [u, v]")
    if len(items) < 3:
        raise _bad(pointer, "a polygon needs at least 3 points", rs.allowed)
    vector = _vector_kind(2, positive=False)
    return tuple(vector(rs, point, f"{pointer}/{i}") for i, point in enumerate(items))  # type: ignore[misc]


def _parse_entries(rs: Resolver, raw: Any, pointer: str) -> tuple[tuple[float, float, float], ...]:
    items = _list(rs, raw, pointer, None, "a list of entry points [[x, y, z], ...]")
    if not items:
        raise _bad(pointer, "give at least one entry point [x, y, z]", rs.allowed)
    rs.hole_positions += len(items)
    if rs.hole_positions > MAX_HOLE_POSITIONS:
        raise _bad(pointer, f"a spec has at most {MAX_HOLE_POSITIONS} hole positions in all", rs.allowed, limit=MAX_HOLE_POSITIONS)
    vector = _vector_kind(3, positive=False)
    points: list[tuple[float, float, float]] = []
    for i, item in enumerate(items):
        point = vector(rs, item, f"{pointer}/{i}")
        if point in points:
            raise _bad(f"{pointer}/{i}", f"the same entry point as /{points.index(point)}", rs.allowed)
        points.append(point)  # type: ignore[arg-type]
    return tuple(points)


def _parse_object(rs: Resolver, raw: Any, fields: Mapping[str, Field], pointer: str, *, reserved: Sequence[str] = ()) -> dict[str, Any]:
    """The fields of an object: defaults filled, unknown names refused, each value checked by its kind."""
    allowed = [*reserved, *fields]
    previous, rs.allowed = rs.allowed, allowed
    try:
        if not isinstance(raw, dict):
            raise _bad(pointer, f"expected an object with the fields {allowed}", allowed)
        for key in raw:
            if key not in allowed:
                raise _bad(f"{pointer}/{_escape(key)}", f"unknown field {key!r}; the fields here are {allowed}", allowed)
        out: dict[str, Any] = {}
        for name, spec in fields.items():
            if name in raw:
                out[name] = KINDS[spec.kind].parse(rs, raw[name], f"{pointer}/{_escape(name)}")
                rs.allowed = allowed
            elif spec.default is REQUIRED:
                raise _bad(f"{pointer}/{_escape(name)}", f"{name!r} is required", allowed)
            else:
                out[name] = spec.default
        return out
    finally:
        rs.allowed = previous


def _dump_object(values: Mapping[str, Any], fields: Mapping[str, Field]) -> dict[str, Any]:
    return {name: KINDS[spec.kind].dump(values[name]) for name, spec in fields.items() if values.get(name) is not None}


PROFILES: dict[str, dict[str, Field]] = {
    "rect": {"size": Field("length2_pos"), "at": Field("length2", (0.0, 0.0), "[0 mm, 0 mm]"), "centered": Field("flag", False, "false")},
    "circle": {"diameter": Field("length_pos"), "at": Field("length2", (0.0, 0.0), "[0 mm, 0 mm]")},
    "polygon": {"points": Field("points2")},
    "slot": {
        "length": Field("length_pos"),
        "width": Field("length_pos"),
        "at": Field("length2", (0.0, 0.0), "[0 mm, 0 mm]"),
        "angle": Field("angle", 0.0, "0 deg"),
    },
    "path": {"start": Field("length2"), "segments": Field("segments")},
}
PROFILE_TYPES = {"rect": brep.Rect2, "circle": brep.Circle2, "polygon": brep.Polygon2, "slot": brep.Slot2, "path": brep.Path2}
COUNTERBORE: dict[str, Field] = {"diameter": Field("length_pos"), "depth": Field("length_pos")}
COUNTERSINK: dict[str, Field] = {"diameter": Field("length_pos"), "angle": Field("angle_open")}


def _parse_profile(rs: Resolver, raw: Any, pointer: str) -> brep.Profile2:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise _bad(pointer, f"a profile is an object with exactly one key from {list(PROFILES)}, e.g. {{\"circle\": {{\"diameter\": \"10 mm\"}}}}", list(PROFILES))
    ((shape, body),) = raw.items()
    if shape not in PROFILES:
        raise _bad(f"{pointer}/{_escape(shape)}", f"unknown profile {shape!r}; use one of {list(PROFILES)}", list(PROFILES))
    values = _parse_object(rs, body, PROFILES[shape], f"{pointer}/{shape}")
    if shape == "slot" and not values["length"] > values["width"]:
        raise _bad(f"{pointer}/slot/length", f"the overall slot length must be greater than its width ({values['width']:g} mm)", list(PROFILES["slot"]))
    profile = PROFILE_TYPES[shape](**values)
    if shape == "path":
        problems = brep.check_path(profile)
        if problems:
            first = problems[0]
            where = f"{pointer}/path/segments" + ("" if first.index is None else f"/{first.index}" + ("" if first.field == "segments" else f"/{first.field}"))
            raise _bad(where, first.message, list(PROFILES["path"]))
    return profile


def _dump_profile(profile: brep.Profile2) -> dict[str, Any]:
    shape = next(name for name, cls in PROFILE_TYPES.items() if isinstance(profile, cls))
    values = {f: getattr(profile, f) for f in PROFILES[shape]}
    return {shape: _dump_object(values, PROFILES[shape])}


def _dump_vector(value: Sequence[float]) -> list[str]:
    return [_length_text(v) for v in value]


def _dump_cbore(value: tuple[float, float]) -> dict[str, Any]:
    return {"diameter": _length_text(value[0]), "depth": _length_text(value[1])}


def _dump_csink(value: tuple[float, float]) -> dict[str, Any]:
    return {"diameter": _length_text(value[0]), "angle": _angle_text(value[1])}


def _parse_cbore(rs: Resolver, raw: Any, pointer: str) -> tuple[float, float]:
    values = _parse_object(rs, raw, COUNTERBORE, pointer)
    return values["diameter"], values["depth"]


def _parse_csink(rs: Resolver, raw: Any, pointer: str) -> tuple[float, float]:
    values = _parse_object(rs, raw, COUNTERSINK, pointer)
    return values["diameter"], values["angle"]


KINDS: dict[str, Kind] = {
    "length": Kind("L", _length_kind(sign="any"), _length_text),
    "length_pos": Kind("L", _length_kind(sign="positive"), _length_text),
    "length_nonzero": Kind("L", _length_kind(sign="nonzero"), _length_text),
    "length_nonneg": Kind("L", _length_kind(sign="nonnegative"), _length_text),
    "length2": Kind("V2", _vector_kind(2, positive=False), _dump_vector),
    "length2_pos": Kind("V2", _vector_kind(2, positive=True), _dump_vector),
    "length3": Kind("V3", _vector_kind(3, positive=False), _dump_vector),
    "length3_pos": Kind("V3", _vector_kind(3, positive=True), _dump_vector),
    "points2": Kind("POINTS", _parse_points2, lambda points: [_dump_vector(p) for p in points]),
    "entries": Kind("ENTRIES", _parse_entries, lambda points: [_dump_vector(p) for p in points]),
    "angle": Kind("A", lambda rs, raw, ptr: rs.angle(raw, ptr), _angle_text),
    "angle_turn": Kind("A", _angle_kind(upper=TWO_PI, upper_open=False, words="above 0 and at most 360 deg"), _angle_text),
    "angle_open": Kind("A", _angle_kind(upper=math.pi, upper_open=True, words="above 0 and below 180 deg"), _angle_text),
    "axis": Kind("AXIS", _enum_kind(tuple(AXIS_VECTORS)), str),
    "mode": Kind("MODE", _enum_kind(brep.MODES), str),
    "plane": Kind("PLANE", _enum_kind(PLANES), str),
    "uv": Kind("UV", _enum_kind(("u", "v")), str),
    "depth": Kind("DEPTH", _parse_depth, lambda v: "thru" if v is None else _length_text(v)),
    "selector": Kind("SEL", _parse_selector, lambda s: " or ".join(" & ".join(group) for group in s.groups)),
    "fselector": Kind("FSEL", _parse_face_selector, lambda s: " or ".join(" & ".join(group) for group in s.groups)),
    "count": Kind("INT", _parse_count, int),
    "ref": Kind("NAME", _parse_ref, str),
    "path_text": Kind("PATH", _parse_path_text, str),
    "taper": Kind("A", _parse_taper, _angle_text),
    "turn": Kind("TURN", _enum_kind(("left", "right")), str),
    "segments": Kind("SEGS", _parse_segments, _dump_segments),
    "text": Kind("TEXT", _parse_text, str),
    "flag": Kind("BOOL", _parse_flag, bool),
    "profile": Kind("PROFILE", _parse_profile, _dump_profile),
    "cbore": Kind("CBORE", _parse_cbore, _dump_cbore),
    "csink": Kind("CSINK", _parse_csink, _dump_csink),
}

TYPES: dict[str, str] = {
    "L": 'unit string like "25 mm" or "1.5 in"',
    "V2": "list [L, L]",
    "V3": "list [L, L, L]",
    "POINTS": "list of at least 3 V2",
    "A": 'angle like "90 deg" or "1.57 rad"',
    "AXIS": '"+x" "-x" "+y" "-y" "+z" "-z"',
    "MODE": '"add" | "cut" | "intersect"',
    "PLANE": '"xy" | "xz" | "yz" (sketch axes u, v = x, y | x, z | y, z; normal +z | +y | +x)',
    "UV": '"u" | "v"',
    "DEPTH": 'L or "thru"',
    "ENTRIES": "list of V3",
    "CBORE": '{"diameter": L, "depth": L}',
    "CSINK": '{"diameter": L, "angle": A} (included angle)',
    "TEXT": "short text",
    "SEL": "edge selector",
    "FSEL": "face selector",
    "INT": "whole number",
    "NAME": "an earlier feature's name",
    "PATH": "file path",
    "SEGS": 'list of {"to": V2} (line) | {"to": V2, "radius": L, "turn": "left"|"right"="left"} (arc, shorter way; left = counter-clockwise) | {"to": V2, "via": V2} (arc through via); the last to = start',
}


# --------------------------------------------------------------------------- ops


@dataclass(frozen=True)
class OpDef:
    name: str
    role: Literal["body", "modifier"]
    fields: dict[str, Field]
    summary: str
    check: Callable[[dict[str, Any], str, Resolver], None] | None = None


def _check_hole(args: dict[str, Any], pointer: str, rs: Resolver) -> None:
    allowed = [*OPS["hole"].fields]
    diameter, depth = args["diameter"], args["depth"]
    cbore, csink = args["counterbore"], args["countersink"]
    if cbore is not None and csink is not None:
        raise _bad(f"{pointer}/countersink", "a hole has a counterbore or a countersink, not both", allowed)
    if cbore is not None:
        if not cbore[0] > diameter:
            raise _bad(f"{pointer}/counterbore/diameter", f"must be larger than the hole diameter {diameter:g} mm", list(COUNTERBORE))
        if depth is not None and not cbore[1] < depth:
            raise _bad(f"{pointer}/counterbore/depth", f"must be less than the hole depth {depth:g} mm", list(COUNTERBORE))
    if csink is not None:
        if not csink[0] > diameter:
            raise _bad(f"{pointer}/countersink/diameter", f"must be larger than the hole diameter {diameter:g} mm", list(COUNTERSINK))
        cone = (csink[0] - diameter) / 2.0 / math.tan(csink[1] / 2.0)
        if depth is not None and not cone < depth:
            raise _bad(f"{pointer}/countersink/diameter", f"a cone {cone:g} mm deep does not fit in the hole depth {depth:g} mm", list(COUNTERSINK))


def _check_pattern(args: dict[str, Any], pointer: str, rs: Resolver) -> None:
    allowed = [*OPS["pattern"].fields]
    index, op, mode, entries = rs.tool_feature(args["of"], f"{pointer}/of", sources_only=True)
    step, angle = args["step"], args["angle"]
    if (step is None) == (angle is None):
        raise _bad(f"{pointer}/{'angle' if step is not None else 'step'}", "give step (a linear pattern) or angle (a polar pattern), exactly one of them", allowed)
    count = args["count"]
    if step is not None:
        for name in ("center", "axis"):
            if args[name] is not None:
                raise _bad(f"{pointer}/{name}", f"{name} belongs to a polar pattern (angle), not to a linear one (step)", allowed)
        if not any(step):
            raise _bad(f"{pointer}/step", "the step must not be [0 mm, 0 mm, 0 mm]: every copy would sit on the original", allowed)
        reach = max(abs(c) for c in step) * (count - 1)
        if reach > MAX_LENGTH_MM:
            raise _bad(f"{pointer}/step", f"{count - 1} steps reach {reach:g} mm, beyond the supported size of {MAX_LENGTH_MM:g} mm", allowed, limit_mm=MAX_LENGTH_MM)
    rs.pattern_copies += count - 1
    if rs.pattern_copies > MAX_PATTERN_COPIES:
        raise _bad(f"{pointer}/count", f"all the patterns of a spec make at most {MAX_PATTERN_COPIES} copies, this one brings it to {rs.pattern_copies}", allowed, limit=MAX_PATTERN_COPIES)
    if OPS[op].role == "modifier":  # a hole: its entry points are repeated
        rs.hole_positions += entries * (count - 1)
        if rs.hole_positions > MAX_HOLE_POSITIONS:
            raise _bad(f"{pointer}/count", f"a spec has at most {MAX_HOLE_POSITIONS} hole positions in all, this pattern brings it to {rs.hole_positions}", allowed, limit=MAX_HOLE_POSITIONS)


def _check_mirror(args: dict[str, Any], pointer: str, rs: Resolver) -> None:
    if args["of"] is not None:
        _, op, _, entries = rs.tool_feature(args["of"], f"{pointer}/of", sources_only=True)
        if OPS[op].role == "modifier":
            rs.hole_positions += entries
            if rs.hole_positions > MAX_HOLE_POSITIONS:
                raise _bad(f"{pointer}/of", f"a spec has at most {MAX_HOLE_POSITIONS} hole positions in all", [*OPS["mirror"].fields], limit=MAX_HOLE_POSITIONS)


def _origin() -> tuple[float, float, float]:
    return (0.0, 0.0, 0.0)


OPS: dict[str, OpDef] = {
    "box": OpDef(
        "box",
        "body",
        {
            "size": Field("length3_pos"),
            "at": Field("length3", _origin(), "origin"),
            "mode": Field("mode", "add", '"add"'),
        },
        "block; at is its minimum corner",
    ),
    "cylinder": OpDef(
        "cylinder",
        "body",
        {
            "diameter": Field("length_pos"),
            "height": Field("length_pos"),
            "at": Field("length3", _origin(), "origin"),
            "axis": Field("axis", "+z", '"+z"'),
            "mode": Field("mode", "add", '"add"'),
        },
        "round bar from its base centre at, rising along axis",
    ),
    "sphere": OpDef(
        "sphere",
        "body",
        {
            "diameter": Field("length_pos"),
            "at": Field("length3", _origin(), "origin"),
            "mode": Field("mode", "add", '"add"'),
        },
        "ball; at is its centre",
    ),
    "cone": OpDef(
        "cone",
        "body",
        {
            "diameter": Field("length_pos"),
            "top_diameter": Field("length_nonneg"),
            "height": Field("length_pos"),
            "at": Field("length3", _origin(), "origin"),
            "axis": Field("axis", "+z", '"+z"'),
            "mode": Field("mode", "add", '"add"'),
        },
        "cone or frustum from its base centre at, rising along axis; top_diameter 0 mm makes a point",
    ),
    "extrude": OpDef(
        "extrude",
        "body",
        {
            "plane": Field("plane", "xy", '"xy"'),
            "offset": Field("length", 0.0, "0 mm"),
            "profile": Field("profile"),
            "distance": Field("length_nonzero"),
            "taper": Field("taper", None),
            "mode": Field("mode", "add", '"add"'),
        },
        "extrude a 2D profile along the plane normal; a negative distance reverses it; taper (0 to 45 deg) narrows the walls along the extrusion",
    ),
    "import": OpDef(
        "import",
        "body",
        {
            "file": Field("path_text"),
            "at": Field("length3", _origin(), "origin"),
            "mode": Field("mode", "add", '"add"'),
        },
        "the one solid of a STEP, IGES or BREP file (read in mm), moved by at",
    ),
    "revolve": OpDef(
        "revolve",
        "body",
        {
            "plane": Field("plane", "xy", '"xy"'),
            "offset": Field("length", 0.0, "0 mm"),
            "profile": Field("profile"),
            "axis": Field("uv", "v", '"v"'),
            "angle": Field("angle_turn", TWO_PI, "360 deg"),
            "mode": Field("mode", "add", '"add"'),
        },
        "turn a profile about the plane's u or v axis through its origin; keep the profile on one side of it",
    ),
    "hole": OpDef(
        "hole",
        "modifier",
        {
            "at": Field("entries"),
            "axis": Field("axis", "-z", '"-z"'),
            "diameter": Field("length_pos"),
            "depth": Field("depth", None, '"thru"'),
            "counterbore": Field("cbore", None),
            "countersink": Field("csink", None),
            "thread": Field("text", None),
            "fit": Field("text", None),
        },
        "drill at each entry point along axis; depth counts from the entry; thread and fit are text callouts only",
        _check_hole,
    ),
    "pattern": OpDef(
        "pattern",
        "modifier",
        {
            "of": Field("ref"),
            "count": Field("count"),
            "step": Field("length3", None),
            "angle": Field("angle_turn", None),
            "center": Field("length3", None, "origin"),
            "axis": Field("axis", None, '"+z"'),
        },
        "repeat an earlier body or hole feature (rather than computing copies or rotated outlines by hand) to count instances in all, the original included: linear by step, or polar about axis "
        "through center over angle (360 deg = evenly round, less = first to last)",
        _check_pattern,
    ),
    "mirror": OpDef(
        "mirror",
        "modifier",
        {"plane": Field("plane"), "offset": Field("length", 0.0, "0 mm"), "of": Field("ref", None)},
        "reflect in a plane (xy: z = offset, xz: y = offset, yz: x = offset) and add it: the whole part so far, or only the feature of",
        _check_mirror,
    ),
    "shell": OpDef(
        "shell",
        "modifier",
        {"faces": Field("fselector"), "thickness": Field("length_pos")},
        "hollow the solid: remove the selected faces, walls grow inward",
    ),
    "fillet": OpDef("fillet", "modifier", {"edges": Field("selector"), "radius": Field("length_pos")}, "round the selected edges"),
    "chamfer": OpDef("chamfer", "modifier", {"edges": Field("selector"), "distance": Field("length_pos")}, "bevel the selected edges symmetrically"),
}

EXAMPLE_SPEC: dict[str, Any] = {
    "params": {"L": "100 mm", "W": "50 mm", "T": "20 mm", "d": "10 mm"},
    "features": [
        {"op": "box", "name": "body", "size": ["$L", "$W", "$T"]},
        {"op": "hole", "name": "bore", "at": [["$L / 4", "$W / 2", "$T"]], "axis": "-z", "diameter": "$d", "depth": "thru", "fit": "H7"},
        {"op": "fillet", "name": "corners", "edges": "|z & convex", "radius": "2 mm"},
    ],
}


def _field_text(name: str, spec: Field) -> str:
    text = f"{name}{'*' if spec.default is REQUIRED else ''} {KINDS[spec.kind].label}"
    return f"{text}={spec.default_text}" if spec.default_text else text


def _profile_text() -> str:
    parts = []
    for shape, fields in PROFILES.items():
        inner = ", ".join(_field_text(name, spec) for name, spec in fields.items())
        parts.append(f'{{"{shape}": {{{inner}}}}}')
    return " | ".join(parts)


def grammar_text() -> str:
    """The spec grammar as compact text, generated from :data:`OPS` (the tool description embeds it)."""
    used = {KINDS[f.kind].label for fields in [*(op.fields for op in OPS.values()), *PROFILES.values()] for f in fields.values()}
    types = "; ".join(f"{label} = {text}" for label, text in TYPES.items() if label in used)
    lines = [
        'The spec is a JSON object with "params" (name: expression, optional) and "features" (a list, at most '
        f'{MAX_FEATURES}). Every number is a unit string such as "25 mm" or "90 deg"; bare numbers are rejected. '
        '"$name" uses an earlier param; an expression may use + - * / ( ) and one plain power of at most '
        f'{MAX_EXPONENT:g}, e.g. "($L - 2 mm) / 2", at most {MAX_EXPRESSION_CHARS} characters. Features run in order on one solid; '
        'the first must be a body with mode "add". Each feature has "op" and an optional "name" (letters, digits, _ and -).',
        f"Types: {types}. * = required.",
        "Ops:",
    ]
    for name, op in OPS.items():
        fields = ", ".join(_field_text(f, spec) for f, spec in op.fields.items())
        lines.append(f"- {name} ({op.role}): {fields}. {op.summary[0].upper()}{op.summary[1:]}.")
    lines.append(f"Profiles: {_profile_text()} (a rect's at is its minimum corner unless centered).")
    terms = " ".join(brep.EDGE_TERMS)
    faces = " ".join(brep.FACE_TERMS)
    lines.append(
        f"Edge selectors join terms with &: {terms}. |x = straight edges along x; >x = edges in the +x face of the part's bounding "
        "box; convex and concave = outside and inside corners (a cylinder seam is neither). "
        f"Face selectors (shell): {faces}; >z = the top face. "
        "Both accept of:NAME = what the earlier body, hole, pattern or mirror NAME created, e.g. \"of:boss & circle\". "
        "Both union up to 8 groups with lowercase ' or '; & binds tighter; no parentheses."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- the parsed spec


@dataclass(frozen=True)
class Feature:
    index: int  # 1-based position in the spec; the JSON pointer is /features/<index - 1>
    op: str
    name: str
    args: dict[str, Any]

    @property
    def pointer(self) -> str:
        return f"/features/{self.index - 1}"


@dataclass(frozen=True)
class PartSpec:
    features: tuple[Feature, ...]
    params: dict[str, str]  # resolved: lengths in mm, angles in rad, as unit strings
    canonical: str  # the parsed spec as canonical JSON; it is itself a valid spec
    referenced: frozenset[str] = frozenset()  # feature names that of: terms, patterns and mirrors refer to

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()


def _parse_feature(rs: Resolver, raw: Any, i: int) -> Feature:
    pointer = f"/features/{i}"
    if not isinstance(raw, dict):
        raise _bad(pointer, 'a feature is an object such as {"op": "box", "size": [...]}', ["op", "name"])
    if "op" not in raw:
        raise _bad(f"{pointer}/op", f"'op' is required; use one of {list(OPS)}", list(OPS))
    op = raw["op"]
    if not isinstance(op, str) or op not in OPS:
        raise _bad(f"{pointer}/op", f"unknown op {op!r}; use one of {list(OPS)}", list(OPS))
    definition = OPS[op]
    if i == 0 and definition.role != "body":
        bodies = [n for n, d in OPS.items() if d.role == "body"]
        raise _bad(f"{pointer}/op", f"the first feature must be a body ({', '.join(bodies)}), not {op!r}", bodies)
    name = raw.get("name", f"{op}{i + 1}")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise _bad(f"{pointer}/name", f"a name is 1 to 64 letters, digits, '_' or '-', got {name!r:.80}", ["op", "name", *definition.fields])
    body = {key: value for key, value in raw.items() if key not in ("op", "name")}
    args = _parse_object(rs, body, definition.fields, pointer, reserved=("op", "name"))
    if i == 0 and args.get("mode", "add") != "add":
        raise _bad(f"{pointer}/mode", 'the first feature must have mode "add"', list(brep.MODES))
    if definition.check is not None:
        definition.check(args, pointer, rs)
    rs.names.setdefault(name, []).append((i + 1, op, args.get("mode", "add"), len(args["at"]) if op == "hole" else 0))
    return Feature(index=i + 1, op=op, name=name, args=args)


def _canonical(features: Sequence[Feature], params: Mapping[str, str]) -> str:
    dumped = [
        {"op": f.op, "name": f.name, **_dump_object(f.args, OPS[f.op].fields)}
        for f in features
    ]
    return json.dumps({"features": dumped, "params": dict(params)}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_spec(text: str) -> PartSpec:
    """Parse and validate a spec; ``input.invalid`` carries ``details.pointer`` and ``details.allowed``."""
    if not isinstance(text, str):
        raise _bad("", "the spec must be a JSON string")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise _bad("", "the spec holds a character that is not valid Unicode text (a lone surrogate)") from None
    if size > MAX_SPEC_BYTES:
        raise _bad("", f"the spec is {size} bytes; the limit is {MAX_SPEC_BYTES}", limit_bytes=MAX_SPEC_BYTES)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _bad("", f"not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})") from exc
    except RecursionError as exc:
        raise _bad("", "the JSON is nested too deeply") from exc
    if not isinstance(document, dict):
        raise _bad("", 'the spec must be a JSON object {"params": {...}, "features": [...]}', ["params", "features"])
    for key in document:
        if key not in ("params", "features"):
            raise _bad(f"/{_escape(key)}", f"unknown key {key!r}; a spec has only params and features", ["params", "features"])
    declared = document.get("params", {})
    if not isinstance(declared, dict):
        raise _bad("/params", 'params is an object of name: expression, e.g. {"L": "100 mm"}', ["params", "features"])
    rs = Resolver(declared)
    rs.allowed = ["params", "features"]
    for name, raw in declared.items():
        pointer = f"/params/{_escape(name)}"
        if not PARAM_NAME.fullmatch(name):
            raise _bad(pointer, f"a parameter name is letters, digits and '_', not starting with a digit, got {name!r:.80}", rs.allowed)
        rs.define(name, raw, pointer)
    if "features" not in document:
        raise _bad("/features", "'features' is required: a list of features", ["params", "features"])
    items = document["features"]
    if not isinstance(items, list) or not items:
        raise _bad("/features", "features is a non-empty list of feature objects", ["params", "features"])
    if len(items) > MAX_FEATURES:
        raise _bad("/features", f"a spec has at most {MAX_FEATURES} features, this one has {len(items)}", ["params", "features"], limit=MAX_FEATURES)
    features = tuple(_parse_feature(rs, raw, i) for i, raw in enumerate(items))
    params = rs.params()
    return PartSpec(features=features, params=params, canonical=_canonical(features, params), referenced=frozenset(rs.referenced))


# --------------------------------------------------------------------------- building


@dataclass(frozen=True)
class FeatureRecord:
    index: int
    name: str
    op: str
    volume_after_mm3: float
    edges_used: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name, "op": self.op, "volume_after_mm3": self.volume_after_mm3, "edges_used": self.edges_used}


@dataclass(frozen=True)
class DeclaredHole:
    """A hole as declared in the spec (its intent), resolved to millimetres."""

    feature: str
    feature_index: int
    entry_mm: tuple[float, float, float]
    axis: tuple[float, float, float]
    diameter_mm: float
    depth_mm: float | None
    counterbore: tuple[float, float] | None
    countersink: tuple[float, float] | None
    thread: str
    fit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "feature_index": self.feature_index,
            "entry_mm": list(self.entry_mm),
            "axis": list(self.axis),
            "diameter_mm": self.diameter_mm,
            "depth_mm": self.depth_mm,
            "thru": self.depth_mm is None,
            "counterbore": None if self.counterbore is None else {"diameter_mm": self.counterbore[0], "depth_mm": self.counterbore[1]},
            "countersink": None
            if self.countersink is None
            else {"diameter_mm": self.countersink[0], "angle_deg": _clean(math.degrees(self.countersink[1]))},
            "thread": self.thread,
            "fit": self.fit,
        }


@dataclass(frozen=True)
class ImportRecord:
    """A model file brought in by an ``import`` feature: the path as written and the sha256 of the bytes that were read."""

    feature: str
    feature_index: int
    file: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "feature_index": self.feature_index, "file": self.file, "sha256": self.sha256}


@dataclass(frozen=True)
class BuiltPart:
    solid: brep.Solid
    records: tuple[FeatureRecord, ...]
    holes: tuple[DeclaredHole, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    imports: tuple[ImportRecord, ...] = field(default_factory=tuple)


OpenFile = Callable[[str, str], tuple[Path, str]]  # (the path as written, the JSON pointer) -> (a readable copy, its sha256)
PLANE_AXIS = {"yz": 0, "xz": 1, "xy": 2}  # the coordinate a mirror plane holds fixed


def _volume(solid: brep.Solid) -> float:
    volume = brep.mass_properties(solid).volume_mm3
    return 0.0 if volume is None else volume


def _snap(values: Sequence[float], digits: int) -> tuple[float, ...]:
    return tuple(round(float(v), digits) + 0.0 for v in values)  # rounds away the 1e-16 noise of a rotation


class _Motion:
    """A rigid motion or a reflection that carries a feature's tool to one of its copies."""

    def __init__(self, *, matrix: np.ndarray | None = None, shift: np.ndarray | None = None, plane: brep.SketchPlane | None = None):
        self.matrix = np.eye(3) if matrix is None else matrix
        self.shift = np.zeros(3) if shift is None else shift
        self.plane = plane

    def point(self, p: Sequence[float]) -> tuple[float, float, float]:
        if self.plane is not None:
            q = list(map(float, p))
            axis = PLANE_AXIS[self.plane.name]
            q[axis] = 2.0 * self.plane.offset - q[axis]
            return _snap(q, 9)  # type: ignore[return-value]
        return _snap(self.matrix @ np.asarray(p, dtype=np.float64) + self.shift, 9)  # type: ignore[return-value]

    def vector(self, v: Sequence[float]) -> tuple[float, float, float]:
        if self.plane is not None:
            q = list(map(float, v))
            q[PLANE_AXIS[self.plane.name]] *= -1.0
            return _snap(q, 12)  # type: ignore[return-value]
        return _snap(self.matrix @ np.asarray(v, dtype=np.float64), 12)  # type: ignore[return-value]

    def solid(self, tool: brep.Solid) -> brep.Solid:
        if self.plane is not None:
            return brep.mirror(tool, self.plane)
        return brep.transform(tool, self.matrix.tolist(), tuple(float(c) for c in self.shift))


def _rotation(axis: Sequence[float], angle: float) -> np.ndarray:
    u = np.asarray(axis, dtype=np.float64)
    cross = np.array([[0.0, -u[2], u[1]], [u[2], 0.0, -u[0]], [-u[1], u[0], 0.0]])
    matrix = math.cos(angle) * np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * np.outer(u, u)
    return np.round(matrix, 14) + 0.0


def _pattern_motions(args: dict[str, Any]) -> list[_Motion]:
    """The motions of copies 1 to count - 1 of a pattern."""
    count = args["count"]
    if args["step"] is not None:
        step = np.asarray(args["step"], dtype=np.float64)
        return [_Motion(shift=step * k) for k in range(1, count)]
    centre = np.asarray(args["center"] if args["center"] is not None else _origin(), dtype=np.float64)
    axis = AXIS_VECTORS[args["axis"] or "+z"]
    angle = args["angle"]
    around = abs(angle - TWO_PI) <= 1e-9  # a full turn: count evenly spaced instances, not first-to-last
    motions = []
    for k in range(1, count):
        theta = TWO_PI * k / count if around else angle * k / (count - 1)
        matrix = _rotation(axis, theta)
        motions.append(_Motion(matrix=matrix, shift=centre - matrix @ centre))
    return motions


def _changes(before: brep.Solid, tool: brep.Solid, mode: str) -> bool:
    """Would applying ``tool`` in ``mode`` change ``before``?  Cut and add are judged by how much of the tool lies inside the part."""
    try:
        overlap = _volume(brep.combine(before, tool, "intersect"))
    except ToolFailure:  # an empty intersection is reported as a failed boolean
        overlap = 0.0
    if mode == "cut":
        return overlap > 1e-9 * max(_volume(before), 1.0)
    inside = _volume(tool)
    return inside - overlap > 1e-9 * max(inside, 1.0)


class _Builder:
    def __init__(self, spec: PartSpec, open_file: OpenFile | None):
        self.spec = spec
        self.open_file = open_file
        self.solid: brep.Solid | None = None
        self.records: list[FeatureRecord] = []
        self.holes: list[DeclaredHole] = []
        self.warnings: list[str] = []
        self.imports: list[ImportRecord] = []
        self.created: dict[str, brep.Created] = {}  # edges and faces of the features that of: terms refer to
        self.tools: dict[int, brep.Solid] = {}  # the tool shape of every body feature, by feature index

    # -- helpers

    def _source(self, feature: Feature, name: str) -> Feature:
        for earlier in reversed(self.spec.features[: feature.index - 1]):
            if earlier.name == name:
                return earlier
        raise _bad(f"{feature.pointer}/of", f"{name!r} names no earlier feature", [f.name for f in self.spec.features[: feature.index - 1]])

    def _record_created(self, names: Sequence[str], before: brep.Solid | None, after: brep.Solid, tool: brep.Solid) -> None:
        wanted = [n for n in dict.fromkeys(names) if n in self.spec.referenced]
        if not wanted:
            return
        found = brep.created_by(before, after, tool)
        for name in wanted:
            self.created[name] = self.created[name].merge(found) if name in self.created else found

    # -- body and hole

    def _body(self, feature: Feature) -> brep.Solid:
        a = feature.args
        if feature.op == "box":
            return brep.make_box(a["size"], a["at"])
        if feature.op == "cylinder":
            return brep.make_cylinder(a["diameter"], a["height"], a["at"], AXIS_VECTORS[a["axis"]])
        if feature.op == "sphere":
            return brep.make_sphere(a["diameter"], a["at"])
        if feature.op == "cone":
            return brep.make_cone(a["diameter"], a["top_diameter"], a["height"], a["at"], AXIS_VECTORS[a["axis"]])
        if feature.op == "import":
            return self._import(feature)
        plane = brep.SketchPlane(a["plane"], a["offset"])
        if feature.op == "extrude":
            return brep.make_prism(a["profile"], plane, a["distance"], a["taper"])
        return brep.make_revolution(a["profile"], plane, a["axis"], a["angle"])

    def _import(self, feature: Feature) -> brep.Solid:
        pointer = f"{feature.pointer}/file"
        if self.open_file is None:
            raise _bad(pointer, "this build has no way to read model files, so import is not available here", [])
        try:
            path, digest = self.open_file(feature.args["file"], pointer)
            solid = brep.import_solid(path, feature.args["at"], param=pointer)
        except ToolFailure as exc:
            details = {k: v for k, v in exc.details.items() if k != "field"}
            raise ToolFailure(exc.family, exc.message, {**details, "pointer": pointer}) from exc
        self.imports.append(ImportRecord(feature.name, feature.index, feature.args["file"], digest))
        return solid

    def _drill(self, source: Feature, owner: Feature, entries: Sequence[tuple[float, float, float]], axis: tuple[float, float, float], props: brep.MassProperties | None = None) -> tuple[brep.Solid, list[DeclaredHole]]:
        """The tool of ``source``'s hole at the given entry points and direction, and the holes it declares under ``owner``'s name."""
        a = source.args
        props = props or brep.mass_properties(self.solid)
        centre = [(lo + hi) / 2.0 for lo, hi in zip(props.bbox_min_mm, props.bbox_max_mm)]
        diagonal = math.dist(props.bbox_min_mm, props.bbox_max_mm)
        reach = diagonal + max(math.dist(entry, centre) for entry in entries) + 1.0
        tool = brep.make_hole_tool(entries, axis, a["diameter"], a["depth"], a["counterbore"], a["countersink"], reach)
        declared = [
            DeclaredHole(owner.name, owner.index, tuple(entry), axis, a["diameter"], a["depth"], a["counterbore"], a["countersink"], a["thread"] or "", a["fit"] or "")
            for entry in entries
        ]
        return tool, declared

    # -- the features

    def _add_body(self, feature: Feature) -> bool:
        tool = self._body(feature)
        self.tools[feature.index] = tool
        mode = feature.args["mode"]
        before = self.solid
        self.solid = tool if before is None else brep.combine(before, tool, mode)
        self._record_created([feature.name], before, self.solid, tool)
        return mode in ("cut", "intersect")

    def _add_hole(self, feature: Feature) -> bool:
        before = self.solid
        tool, declared = self._drill(feature, feature, feature.args["at"], AXIS_VECTORS[feature.args["axis"]])
        self.solid = brep.combine(before, tool, "cut")
        self.holes.extend(declared)
        self._record_created([feature.name], before, self.solid, tool)
        return True

    def _copy(self, feature: Feature, source: Feature, motions: Sequence[_Motion], label: str) -> None:
        """Apply the source feature again at each motion, all copies in one boolean.

        A copy is checked on its own against the part as it was: one that cuts or adds nothing (it falls off the part, or sits
        inside it) is left out, warned about, and declares no hole.
        """
        before = self.solid
        is_body = OPS[source.op].role == "body"
        mode = source.args["mode"] if is_body else "cut"
        props = None if is_body else brep.mass_properties(before)
        live: list[brep.Solid] = []
        idle: list[int] = []
        declared_live: list[DeclaredHole] = []
        for k, motion in enumerate(motions, start=1):
            if is_body:
                tool = motion.solid(self.tools[source.index])
                declared: list[DeclaredHole] = []
            else:
                entries = [motion.point(entry) for entry in source.args["at"]]
                tool, declared = self._drill(source, feature, entries, motion.vector(AXIS_VECTORS[source.args["axis"]]), props)
            if _changes(before, tool, mode):
                live.append(tool)
                declared_live.extend(declared)
            else:
                idle.append(k)
        if live:
            joined = brep.join_tools(live)
            self.solid = brep.combine(before, joined, mode)
            self.holes.extend(declared_live)
            self._record_created([feature.name, source.name], before, self.solid, joined)
        if idle:
            shown = ", ".join(map(str, idle[:10])) + (" ..." if len(idle) > 10 else "")
            self.warnings.append(
                f"feature {feature.index} '{feature.name}' ({feature.op}): {label} {shown} changed nothing "
                f"({len(idle)} of {len(motions)}){', so no hole is declared for them' if not is_body else ''}: check that the copies fall on the part"
            )

    def _pattern(self, feature: Feature) -> None:
        source = self._source(feature, feature.args["of"])
        self._copy(feature, source, _pattern_motions(feature.args), "copy")

    def _mirror(self, feature: Feature) -> None:
        a = feature.args
        plane = brep.SketchPlane(a["plane"], a["offset"])
        motion = _Motion(plane=plane)
        if a["of"] is not None:
            self._copy(feature, self._source(feature, a["of"]), [motion], "the reflection")
            return
        before = self.solid
        tool = brep.mirror(before, plane)
        self.solid = brep.combine(before, tool, "add")
        if abs(_volume(self.solid) - _volume(before)) <= 1e-9 * max(_volume(before), 1.0):
            self.warnings.append(
                f"feature {feature.index} '{feature.name}' (mirror) added no material: the part already lies symmetrically about that plane or the reflection falls inside it"
            )
        have = {(_snap(h.entry_mm, 9), _snap(h.axis, 12), h.diameter_mm) for h in self.holes}
        for hole in list(self.holes):
            entry, axis = motion.point(hole.entry_mm), motion.vector(hole.axis)
            if (entry, axis, hole.diameter_mm) not in have:
                have.add((entry, axis, hole.diameter_mm))
                self.holes.append(
                    DeclaredHole(feature.name, feature.index, entry, axis, hole.diameter_mm, hole.depth_mm, hole.counterbore, hole.countersink, hole.thread, hole.fit)
                )
        self._record_created([feature.name], before, self.solid, tool)

    # -- the loop

    def run(self) -> BuiltPart:
        for feature in self.spec.features:
            before = None if self.solid is None else _volume(self.solid)
            edges_used: int | None = None
            removes = False
            try:
                role = OPS[feature.op].role
                if role == "body":
                    removes = self._add_body(feature)
                elif feature.op == "hole":
                    removes = self._add_hole(feature)
                elif feature.op == "pattern":
                    self._pattern(feature)
                elif feature.op == "mirror":
                    self._mirror(feature)
                elif feature.op == "shell":
                    if self.holes:
                        self.warnings.append(
                            f"feature {feature.index} '{feature.name}' (shell): holes declared before a shell keep their declared size and position, "
                            "but the kernel offsets their walls with the part; drill after the shell when the hole size matters"
                        )
                    self.solid, edges_used = brep.shell(self.solid, feature.args["faces"], feature.args["thickness"], self.created)
                elif feature.op == "fillet":
                    self.solid, edges_used = brep.fillet(self.solid, feature.args["edges"], feature.args["radius"], self.created)
                else:
                    self.solid, edges_used = brep.chamfer(self.solid, feature.args["edges"], feature.args["distance"], self.created)
            except ToolFailure as exc:
                if exc.family.startswith("geometry.") or "field" in exc.details:
                    raise _located(exc, feature) from exc
                raise
            after = _volume(self.solid)
            if removes and before is not None and abs(after - before) <= 1e-9 * max(before, 1.0):
                self.warnings.append(f"feature {feature.index} '{feature.name}' ({feature.op}) removed no material: check its position and direction against the part")
            self.records.append(FeatureRecord(feature.index, feature.name, feature.op, _clean(after), edges_used))
        problems = brep.validity(self.solid)
        if self.solid.solids != 1 or problems:
            raise ToolFailure(
                "geometry.invalid_result",
                f"the result is {self.solid.solids} solids"
                + (f" and fails the validity check ({'; '.join(problems)})" if problems else "")
                + "; a part must be exactly one valid solid, so join loose bodies or remove them",
                {"solids": self.solid.solids, "problems": list(problems)},
            )
        if self.imports:
            self.warnings.append("holes and other features inside imported geometry are not declared: features.json lists only the holes the spec drills")
        return BuiltPart(solid=self.solid, records=tuple(self.records), holes=tuple(self.holes), warnings=tuple(self.warnings), imports=tuple(self.imports))


def _located(exc: ToolFailure, feature: Feature) -> ToolFailure:
    details = {k: v for k, v in exc.details.items() if k != "field"}
    pointer = f"{feature.pointer}/{exc.details['field']}" if "field" in exc.details else feature.pointer
    details = {**details, "feature_index": feature.index, "feature_name": feature.name, "op": feature.op, "pointer": pointer}
    return ToolFailure(exc.family, f"feature {feature.index} '{feature.name}' ({feature.op}): {exc.message}", details)


def build(spec: PartSpec, *, open_file: OpenFile | None = None) -> BuiltPart:
    """Run the features in order.  ``geometry.build_failed`` and ``geometry.empty_selection`` name the feature;
    the final shape must be exactly one valid solid (``geometry.invalid_result``).  ``open_file`` opens the files that
    ``import`` features name (the tool passes ``JobHandle.input_file``); without it a spec with an import is refused."""
    return _Builder(spec, open_file).run()


def features_document(spec: PartSpec, built: BuiltPart, step_sha256: str, *, part_name: str) -> dict[str, Any]:
    """The declared intent of the part (holes with their diameter, depth, counterbore, thread and fit), bound to a STEP file by its hash."""
    return {
        "schema": "eng-toolkit/features/1",
        "part": part_name,
        "unit": "mm",
        "step_sha256": step_sha256,
        "spec_sha256": spec.sha256,
        "params": dict(spec.params),
        "holes": [hole.to_dict() for hole in built.holes],
        "imports": [item.to_dict() for item in built.imports],
    }


__all__ = [
    "EXAMPLE_SPEC",
    "OPS",
    "BuiltPart",
    "DeclaredHole",
    "ImportRecord",
    "Feature",
    "FeatureRecord",
    "OpDef",
    "PartSpec",
    "Resolver",
    "build",
    "check_expression",
    "features_document",
    "grammar_text",
    "parse_spec",
]
