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
from typing import Any, Callable, Literal, Mapping, Sequence

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


def _parse_selector(rs: Resolver, raw: Any, pointer: str) -> brep.EdgeSelector:
    try:
        return brep.parse_edge_selector(raw)
    except ToolFailure as exc:
        raise _bad(pointer, exc.message, brep.EDGE_TERMS) from exc


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
}
PROFILE_TYPES = {"rect": brep.Rect2, "circle": brep.Circle2, "polygon": brep.Polygon2, "slot": brep.Slot2}
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
    return PROFILE_TYPES[shape](**values)


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
    "selector": Kind("SEL", _parse_selector, lambda s: " & ".join(s.terms)),
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
            "mode": Field("mode", "add", '"add"'),
        },
        "extrude a 2D profile along the plane normal; a negative distance reverses it",
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
    lines.append(
        f"Edge selectors join terms with &: {terms}. |x = straight edges along x; >x = edges in the +x face of the part's bounding "
        "box; convex and concave = outside and inside corners (a cylinder seam is neither)."
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
    size = len(text.encode("utf-8"))
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
    return PartSpec(features=features, params=params, canonical=_canonical(features, params))


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
class BuiltPart:
    solid: brep.Solid
    records: tuple[FeatureRecord, ...]
    holes: tuple[DeclaredHole, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _volume(solid: brep.Solid) -> float:
    volume = brep.mass_properties(solid).volume_mm3
    return 0.0 if volume is None else volume


def _body(feature: Feature):
    a = feature.args
    if feature.op == "box":
        return brep.make_box(a["size"], a["at"])
    if feature.op == "cylinder":
        return brep.make_cylinder(a["diameter"], a["height"], a["at"], AXIS_VECTORS[a["axis"]])
    if feature.op == "sphere":
        return brep.make_sphere(a["diameter"], a["at"])
    if feature.op == "cone":
        return brep.make_cone(a["diameter"], a["top_diameter"], a["height"], a["at"], AXIS_VECTORS[a["axis"]])
    plane = brep.SketchPlane(a["plane"], a["offset"])
    if feature.op == "extrude":
        return brep.make_prism(a["profile"], plane, a["distance"])
    return brep.make_revolution(a["profile"], plane, a["axis"], a["angle"])


def _drill(feature: Feature, solid: brep.Solid) -> tuple[brep.Solid, list[DeclaredHole]]:
    a = feature.args
    props = brep.mass_properties(solid)
    centre = [(lo + hi) / 2.0 for lo, hi in zip(props.bbox_min_mm, props.bbox_max_mm)]
    diagonal = math.dist(props.bbox_min_mm, props.bbox_max_mm)
    reach = diagonal + max(math.dist(entry, centre) for entry in a["at"]) + 1.0
    axis = AXIS_VECTORS[a["axis"]]
    tool = brep.make_hole_tool(a["at"], axis, a["diameter"], a["depth"], a["counterbore"], a["countersink"], reach)
    declared = [
        DeclaredHole(feature.name, feature.index, entry, axis, a["diameter"], a["depth"], a["counterbore"], a["countersink"], a["thread"] or "", a["fit"] or "")
        for entry in a["at"]
    ]
    return brep.combine(solid, tool, "cut"), declared


def _located(exc: ToolFailure, feature: Feature) -> ToolFailure:
    details = {**exc.details, "feature_index": feature.index, "feature_name": feature.name, "op": feature.op, "pointer": feature.pointer}
    return ToolFailure(exc.family, f"feature {feature.index} '{feature.name}' ({feature.op}): {exc.message}", details)


def build(spec: PartSpec) -> BuiltPart:
    """Run the features in order.  ``geometry.build_failed`` and ``geometry.empty_selection`` name the feature;
    the final shape must be exactly one valid solid (``geometry.invalid_result``)."""
    solid: brep.Solid | None = None
    records: list[FeatureRecord] = []
    holes: list[DeclaredHole] = []
    warnings: list[str] = []
    for feature in spec.features:
        before = None if solid is None else _volume(solid)
        edges_used: int | None = None
        removes = False
        try:
            if OPS[feature.op].role == "body":
                body = _body(feature)
                mode = feature.args["mode"]
                solid = body if solid is None else brep.combine(solid, body, mode)
                removes = mode in ("cut", "intersect")
            elif feature.op == "hole":
                solid, declared = _drill(feature, solid)
                holes.extend(declared)
                removes = True
            elif feature.op == "fillet":
                solid, edges_used = brep.fillet(solid, feature.args["edges"], feature.args["radius"])
            else:
                solid, edges_used = brep.chamfer(solid, feature.args["edges"], feature.args["distance"])
        except ToolFailure as exc:
            if exc.family.startswith("geometry."):
                raise _located(exc, feature) from exc
            raise
        after = _volume(solid)
        if removes and before is not None and abs(after - before) <= 1e-9 * max(before, 1.0):
            warnings.append(f"feature {feature.index} '{feature.name}' ({feature.op}) removed no material: check its position and direction against the part")
        records.append(FeatureRecord(feature.index, feature.name, feature.op, _clean(after), edges_used))
    problems = brep.validity(solid)
    if solid.solids != 1 or problems:
        raise ToolFailure(
            "geometry.invalid_result",
            f"the result is {solid.solids} solids"
            + (f" and fails the validity check ({'; '.join(problems)})" if problems else "")
            + "; a part must be exactly one valid solid, so join loose bodies or remove them",
            {"solids": solid.solids, "problems": list(problems)},
        )
    return BuiltPart(solid=solid, records=tuple(records), holes=tuple(holes), warnings=tuple(warnings))


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
    }


__all__ = [
    "EXAMPLE_SPEC",
    "OPS",
    "BuiltPart",
    "DeclaredHole",
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
