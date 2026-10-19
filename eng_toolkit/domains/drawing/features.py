"""The design intent file of ``geometry_make_part`` (``<name>.features.json``) as drawing_make_views reads it.

The file says what the model cannot: the fit of a hole (``H7``) and its thread (``M6x1-6H``) are only text in the
part spec, the geometry holds the nominal diameter.  The drawing takes every size from the geometry and only these two
words from the file, and only when the file is bound to the model by hash: ``step_sha256`` inside the file must equal the
sha256 of the model file that is being drawn.  Pure: no engine, no file access (the caller reads and hashes the bytes).

Everything in the file is data.  A string that is printed on the sheet is checked like the caller's own text
(printable, short); the file is never followed for anything else.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from eng_toolkit.brep import HoleInfo
from eng_toolkit.errors import ToolFailure

SCHEMA = "eng-toolkit/features/1"
MAX_BYTES = 1_000_000
MAX_HOLES = 1000
MAX_WORD = 24  # characters of a fit or a thread on the sheet
LENGTH_TOLERANCE_MM = 1e-3  # diameter and depth of a modelled hole against the declared one
LINE_TOLERANCE_MM = 1e-3  # distance between the centre line of a modelled hole and the declared one
PARALLEL = 1.0 - 1e-9  # |cos| of the angle between two axes that count as the same direction
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PARAM = "features_path"


@dataclass(frozen=True)
class IntentHole:
    feature: str
    entry_mm: tuple[float, float, float]
    axis: tuple[float, float, float]
    diameter_mm: float
    depth_mm: float | None  # None: through
    fit: str
    thread: str


@dataclass(frozen=True)
class Intent:
    part: str
    step_sha256: str
    holes: tuple[IntentHole, ...]


def _bad(pointer: str, message: str, **details: Any) -> ToolFailure:
    return ToolFailure("input.invalid", f"{PARAM}: {pointer or '/'}: {message}", {"param": PARAM, "pointer": pointer or "/", **details})


def _number(value: Any, pointer: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _bad(pointer, f"expected a finite number, got {value!r}")
    return float(value)


def _vector(value: Any, pointer: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise _bad(pointer, "expected a list of three numbers")
    return tuple(_number(v, f"{pointer}/{i}") for i, v in enumerate(value))  # type: ignore[return-value]


def _word(value: Any, pointer: str) -> str:
    if not isinstance(value, str):
        raise _bad(pointer, f"expected a string, got {value!r}")
    text = " ".join(value.split())
    if len(text) > MAX_WORD or not text.isprintable():
        raise _bad(pointer, f"at most {MAX_WORD} printable characters on one line")
    return text


def parse(raw: bytes) -> Intent:
    """The holes of a features file; ``input.invalid`` with a JSON pointer (``details.pointer``) for anything wrong."""
    if len(raw) > MAX_BYTES:
        raise ToolFailure("input.too_large", f"{PARAM}: the file is {len(raw)} bytes, over the {MAX_BYTES} byte limit", {"param": PARAM, "bytes": len(raw), "limit_bytes": MAX_BYTES})
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _bad("", f"not a JSON file ({type(exc).__name__})") from exc
    if not isinstance(document, dict):
        raise _bad("", "expected a JSON object")
    if document.get("schema") != SCHEMA:
        raise _bad("/schema", f"expected {SCHEMA!r}, got {document.get('schema')!r}; this is not a features file of geometry_make_part")
    digest = document.get("step_sha256")
    if not isinstance(digest, str) or not SHA256.match(digest):
        raise _bad("/step_sha256", "expected the 64 hex digits of the STEP file's sha256 (lower case)")
    holes_raw = document.get("holes")
    if not isinstance(holes_raw, list) or len(holes_raw) > MAX_HOLES:
        raise _bad("/holes", f"expected a list of at most {MAX_HOLES} holes")
    holes: list[IntentHole] = []
    for i, item in enumerate(holes_raw):
        at = f"/holes/{i}"
        if not isinstance(item, dict):
            raise _bad(at, "expected an object")
        depth = None if item.get("thru") is True else _number(item.get("depth_mm"), f"{at}/depth_mm")
        diameter = _number(item.get("diameter_mm"), f"{at}/diameter_mm")
        if diameter <= 0 or (depth is not None and depth <= 0):
            raise _bad(at, "diameter and depth must be positive")
        axis = _vector(item.get("axis"), f"{at}/axis")
        length = math.sqrt(sum(c * c for c in axis))
        if length < 1e-9:
            raise _bad(f"{at}/axis", "the axis must not be the zero vector")
        holes.append(
            IntentHole(
                feature=_word(item.get("feature", ""), f"{at}/feature"),
                entry_mm=_vector(item.get("entry_mm"), f"{at}/entry_mm"),
                axis=tuple(c / length for c in axis),  # type: ignore[arg-type]
                diameter_mm=diameter,
                depth_mm=depth,
                fit=_word(item.get("fit", ""), f"{at}/fit"),
                thread=_word(item.get("thread", ""), f"{at}/thread"),
            )
        )
    part = document.get("part")
    return Intent(part=part if isinstance(part, str) and part.isprintable() and len(part) <= 80 else "", step_sha256=digest, holes=tuple(holes))


# --------------------------------------------------------------------------- binding to the holes of the geometry


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _off_line(point: Sequence[float], origin: Sequence[float], axis: Sequence[float]) -> float:
    """Distance of ``point`` from the line through ``origin`` along the unit vector ``axis``."""
    d = [p - o for p, o in zip(point, origin)]
    along = _dot(d, axis)
    return math.sqrt(max(sum(c * c for c in d) - along * along, 0.0))


def _fits(declared: IntentHole, hole: HoleInfo) -> float | None:
    """How far the modelled hole is from the declared one (mm), or ``None`` when they are not the same hole."""
    if abs(declared.diameter_mm - hole.diameter_mm) > LENGTH_TOLERANCE_MM:
        return None
    cosine = _dot(declared.axis, hole.axis)
    if abs(cosine) < PARALLEL:
        return None
    if (declared.depth_mm is None) != hole.thru:
        return None
    if declared.depth_mm is not None:
        if abs(declared.depth_mm - hole.depth_mm) > LENGTH_TOLERANCE_MM * 10 or cosine < 0:
            return None  # a blind hole has a direction: the same line drilled from the other side is another hole
    if _off_line(hole.entry_mm, declared.entry_mm, declared.axis) > LINE_TOLERANCE_MM:
        return None
    return math.dist(declared.entry_mm, hole.entry_mm)


def bind(declared: Sequence[IntentHole], holes: Sequence[HoleInfo]) -> dict[str, IntentHole]:
    """Which declared hole each modelled hole is, by diameter, direction, thru or depth and centre line.

    One declared hole belongs to at most one modelled hole; when several fit (two holes on one line) the one whose entry
    point is nearest wins.  Modelled holes nobody declared are absent from the result.
    """
    pairs = sorted(
        ((distance, i, j) for i, d in enumerate(declared) for j, h in enumerate(holes) if (distance := _fits(d, h)) is not None),
        key=lambda t: (t[0], t[1], t[2]),
    )
    used_declared: set[int] = set()
    bound: dict[str, IntentHole] = {}
    for _, i, j in pairs:
        if i in used_declared or holes[j].id in bound:
            continue
        used_declared.add(i)
        bound[holes[j].id] = declared[i]
    return bound


__all__ = ["MAX_BYTES", "SCHEMA", "Intent", "IntentHole", "bind", "parse"]
