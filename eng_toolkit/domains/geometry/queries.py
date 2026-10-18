"""The ``geometry_measure`` query grammar: text in, parsed queries out.

Pure text parsing: no OCP and no pint.  A point keeps its coordinates as the text the caller wrote, so
the unit gatekeeper judges them (a bare number is ``units.bare_number`` there, not a syntax error here).

    distance A B | angle A B | diameter A | radius A | area A | thickness P(x, y, z) <axis>

``A`` and ``B`` are ``F<n>`` (a face), ``H<n>`` (a hole) or ``P(x, y, z)`` (a point with units); ``<axis>`` is one of
``+x -x +y -y +z -z``.  Queries are separated by ``;``; verbs and letters are case-insensitive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eng_toolkit.errors import ToolFailure

MAX_QUERIES = 20
AXES: tuple[str, ...] = ("+x", "-x", "+y", "-y", "+z", "-z")
ARITY: dict[str, int] = {"distance": 2, "angle": 2, "diameter": 1, "radius": 1, "area": 1}
VERBS: tuple[str, ...] = (*ARITY, "thickness")
GRAMMAR = (
    "distance A B | angle A B | diameter A | radius A | area A | thickness P(x, y, z) <axis>, "
    "where A and B are F<n> (face), H<n> (hole) or P(x, y, z) (point with units)"
)

_ENTITY = re.compile(
    r"(?:(?P<face>[Ff](?P<f>\d+))|(?P<hole>[Hh](?P<h>\d+))"
    r"|[Pp]\(\s*(?P<x>[^(),]+?)\s*,\s*(?P<y>[^(),]+?)\s*,\s*(?P<z>[^(),]+?)\s*\))(?=\s|$)"
)
_AXIS = re.compile(r"[+-][xyzXYZ]")


@dataclass(frozen=True)
class FaceId:
    number: int


@dataclass(frozen=True)
class HoleId:
    number: int


@dataclass(frozen=True)
class PointText:
    x: str
    y: str
    z: str


Ref = FaceId | HoleId | PointText


@dataclass(frozen=True)
class Query:
    index: int  # 0-based position among the queries; the result calls it q<index + 1>
    verb: str
    refs: tuple[Ref, ...]
    axis: str | None
    text: str


def _invalid(index: int, text: str, why: str, **details) -> ToolFailure:
    return ToolFailure(
        "input.invalid",
        f"q{index + 1}: cannot parse {text!r}: {why}. Grammar: {GRAMMAR}",
        {"param": "queries", "query_index": index, "query": text, **details},
    )


def _entities(rest: str) -> tuple[list[Ref], str]:
    """The leading whitespace-separated entities of ``rest`` and whatever follows them."""
    found: list[Ref] = []
    position = 0
    while True:
        while position < len(rest) and rest[position].isspace():
            position += 1
        match = _ENTITY.match(rest, position)
        if not match:
            return found, rest[position:].strip()
        if match["face"]:
            found.append(FaceId(int(match["f"])))
        elif match["hole"]:
            found.append(HoleId(int(match["h"])))
        else:
            found.append(PointText(match["x"], match["y"], match["z"]))
        position = match.end()


def _parse_one(index: int, text: str) -> Query:
    head, *tail = text.split(None, 1)
    verb = head.lower()
    if verb not in VERBS:
        raise _invalid(index, text, f"unknown verb {head!r}", allowed_verbs=list(VERBS))
    refs, leftover = _entities(tail[0] if tail else "")
    if verb == "thickness":
        if len(refs) != 1 or not isinstance(refs[0], PointText):
            raise _invalid(index, text, "thickness takes one point P(x, y, z) and an axis")
        if not _AXIS.fullmatch(leftover):
            raise _invalid(index, text, f"the axis must be one of {list(AXES)}, got {leftover!r}", allowed_axes=list(AXES))
        return Query(index, verb, tuple(refs), leftover.lower(), text)
    if leftover or len(refs) != ARITY[verb]:
        raise _invalid(index, text, f"{verb} takes {ARITY[verb]} entit{'y' if ARITY[verb] == 1 else 'ies'}")
    return Query(index, verb, tuple(refs), None, text)


def parse_queries(text: str) -> list[Query]:
    """Parse ``;``-separated queries.  Empty segments are skipped; more than ``MAX_QUERIES`` is ``input.invalid``."""
    segments = [part.strip() for part in text.split(";") if part.strip()]
    if len(segments) > MAX_QUERIES:
        raise ToolFailure(
            "input.invalid",
            f"queries has {len(segments)} entries; the maximum is {MAX_QUERIES}",
            {"param": "queries", "limit": MAX_QUERIES, "received": len(segments)},
        )
    return [_parse_one(index, segment) for index, segment in enumerate(segments)]


__all__ = [
    "ARITY",
    "AXES",
    "MAX_QUERIES",
    "VERBS",
    "FaceId",
    "HoleId",
    "PointText",
    "Query",
    "Ref",
    "parse_queries",
]
