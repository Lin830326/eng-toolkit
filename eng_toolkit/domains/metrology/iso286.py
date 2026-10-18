"""ISO 286 limits and fits for nominal sizes 3 to 500 mm.

Pure Python: no MCP, no units library.  Sizes are millimetres, deviations micrometres.

Data
----
``data/iso286.json`` holds the tabulated IT values and the *shaft* fundamental deviations.
Hole deviations are derived with the standard's own rules:

* general rule  (A..H): EI = -es of the shaft letter with the same name
* special rule  (K..N up to IT8, P..ZC up to IT7): ES = -ei + delta, delta = IT(n) - IT(n-1)
  of the same size range (0 for sizes up to 3 mm)
* J is tabulated; JS / js are symmetric (+/- IT/2, exactly).

Size ranges are lower-exclusive and upper-inclusive: 18 mm belongs to 10-18, 3 mm to 0-3.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from eng_toolkit.units import UnitsError

SIZE_MIN_MM = 3.0
SIZE_MAX_MM = 500.0

HOLE_LETTERS = (
    "A", "B", "C", "CD", "D", "E", "EF", "F", "FG", "G", "H", "J", "JS", "K", "M", "N",
    "P", "R", "S", "T", "U", "V", "X", "Y", "Z", "ZA", "ZB", "ZC",
)  # fmt: skip
SHAFT_LETTERS = tuple(letter.lower() for letter in HOLE_LETTERS)

_CLEARANCE_LETTERS = frozenset({"a", "b", "c", "cd", "d", "e", "ef", "f", "fg", "g"})
_DESIGNATION = re.compile(r"^([A-Za-z]{1,2})(\d{1,2})$")
_EXAMPLE = "'H7/g6' (hole in upper case first, shaft in lower case second)"

# Letters whose table cells were additionally checked cell-for-cell against the isofits package
# (sizes above 3 mm up to 400 mm).  Everything else was checked against the ISO 286-1 formulas
# only (within ~4 %), and results for it carry a note.
_EXACT_CHECKED_SHAFT = frozenset("a d e f g h j js k m n p r".split())
_EXACT_CHECKED_HOLE = frozenset("E F G H J JS K M N P R".split())
_EXACT_CHECK_RANGE_MM = (3.0, 400.0)


@dataclass(frozen=True)
class Zone:
    """Limits of one tolerance class (hole or shaft) in micrometres."""

    kind: str  # "hole" | "shaft"
    letter: str
    grade: str  # "01", "0", "1".."18"
    upper_um: float  # ES / es
    lower_um: float  # EI / ei
    tolerance_um: float  # IT value of the grade in this size range
    notes: tuple[str, ...] = ()

    @property
    def designation(self) -> str:
        return f"{self.letter}{self.grade}"


@dataclass(frozen=True)
class FitLimits:
    size_mm: float
    size_range_mm: tuple[int, int]
    """Main size range, used for the IT values."""
    deviation_range_mm: tuple[int, int]
    """Finer size range, used for the fundamental deviations (equals or lies inside the main range)."""
    hole: Zone
    shaft: Zone
    min_clearance_um: float  # hole_min - shaft_max (negative: interference)
    max_clearance_um: float  # hole_max - shaft_min (negative: interference)
    fit_type: str  # "clearance" | "transition" | "interference"


# --------------------------------------------------------------------------- tables


@cache
def tables() -> dict[str, Any]:
    """The parsed ``iso286.json`` (cached; treat as read-only)."""
    with resources.files("eng_toolkit.domains.metrology").joinpath("data/iso286.json").open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def _check_size(size_mm: float) -> None:
    if not (SIZE_MIN_MM <= size_mm <= SIZE_MAX_MM):
        raise UnitsError(
            "input.out_of_range",
            f"nominal size {size_mm:g} mm is outside the supported ISO 286 range "
            f"{SIZE_MIN_MM:g} to {SIZE_MAX_MM:g} mm",
            {"param": "nominal", "received_mm": size_mm, "supported_range_mm": [3, 500]},
        )


def _range_index(size_mm: float) -> int:
    for i, (over, upto) in enumerate(tables()["size_ranges_mm"]):
        if over < size_mm <= upto:
            return i
    raise AssertionError(f"size {size_mm} not covered")  # unreachable after _check_size


def _grade_index(grade: str) -> int:
    return tables()["it_grades"].index(grade)


def _grade_number(grade: str) -> int:
    """Numeric IT index for rule thresholds; IT01 -> -1, IT0 -> 0."""
    return -1 if grade == "01" else int(grade)


def _it(range_idx: int, grade: str) -> float:
    t = tables()
    return float(t["it_um"][t["it_range_of_size_range"][range_idx]][_grade_index(grade)])


def it_tolerance_um(size_mm: float, grade: str) -> float:
    """Standard tolerance IT``grade`` ("01", "0", "1".."18") in micrometres for a nominal size."""
    _check_size(size_mm)
    if grade not in tables()["it_grades"]:
        raise UnitsError("input.unknown_fit_code", f"unknown IT grade {grade!r}", {"grade": grade})
    return _it(_range_index(size_mm), grade)


# --------------------------------------------------------------------------- parsing


def _unknown(code: str, reason: str) -> UnitsError:
    return UnitsError(
        "input.unknown_fit_code",
        f"{code!r} is not a valid ISO 286 designation: {reason}. Expected {_EXAMPLE}",
        {"param": "fit", "received": code},
    )


def parse_designation(text: str, *, expect: str | None = None) -> tuple[str, str, str]:
    """``'H7'`` -> ``('hole', 'H', '7')``; ``'g6'`` -> ``('shaft', 'g', '6')``.

    ``expect`` ("hole" or "shaft") rejects the wrong case with an explanatory message.
    """
    m = _DESIGNATION.match(text.strip())
    if not m:
        raise _unknown(text, "no letter(s) followed by an IT grade")
    letters, grade = m.groups()
    if letters.isupper():
        kind = "hole"
        if letters not in HOLE_LETTERS:
            raise _unknown(text, f"no hole position letter {letters!r}")
    elif letters.islower():
        kind = "shaft"
        if letters not in SHAFT_LETTERS:
            raise _unknown(text, f"no shaft position letter {letters!r}")
    else:
        raise _unknown(text, "letters must be all upper case (hole) or all lower case (shaft)")
    if expect and kind != expect:
        article = "upper" if expect == "hole" else "lower"
        raise _unknown(text, f"the {expect} position letter must be {article} case")
    if grade not in tables()["it_grades"]:
        raise _unknown(text, f"IT{grade} does not exist (grades 01, 0, 1 to 18)")
    return kind, letters, grade


def parse_fit_code(code: str) -> tuple[str, str]:
    """``'H7/g6'`` -> ``('H7', 'g6')``; whitespace around the parts is ignored."""
    parts = [p.strip() for p in str(code).split("/")]
    if len(parts) != 2 or not all(parts):
        raise _unknown(str(code), "need exactly one '/' with a hole and a shaft class")
    return parts[0], parts[1]


# --------------------------------------------------------------------------- zone rules


def _undefined(kind: str, letter: str, grade: str, why: str) -> UnitsError:
    return UnitsError(
        "input.undefined_class",
        f"ISO 286 defines no {kind} class {letter}{grade} here: {why}",
        {"param": "fit", "class": f"{letter}{grade}", "kind": kind},
    )


def _js_half(it: float) -> float:
    """js / JS: +/- IT/2 exactly.  (ISO 286-1 permits rounding odd IT7..IT11 down; we do not.)"""
    return it / 2


def _shaft_es_table(letter: str, r: int, grade: str) -> float | None:
    v = tables()["shaft_upper_deviation_um"][letter][r]
    return None if v is None else float(v)


def _shaft_ei(letter: str, r: int, grade: str, kind: str) -> float:
    """Lower deviation ei of a lower-case letter k..zc (also used inverted for holes)."""
    t = tables()
    n = _grade_number(grade)
    if letter == "k":
        return float(t["shaft_k_lower_deviation_um_it4_to_it7"][r]) if 4 <= n <= 7 else 0.0
    v = t["shaft_lower_deviation_um"][letter][r]
    if v is None:
        lo, hi = t["size_ranges_mm"][r]
        raise _undefined(kind, letter, grade, f"position {letter!r} does not exist for sizes {lo} to {hi} mm")
    return float(v)


def _shaft_zone(letter: str, grade: str, r: int) -> tuple[float, float]:
    it = _it(r, grade)
    if letter == "h":
        return 0.0, -it
    if letter == "js":
        half = _js_half(it)
        return half, -half
    if letter in _CLEARANCE_LETTERS:
        es = _shaft_es_table(letter, r, grade)
        if es is None:
            lo, hi = tables()["size_ranges_mm"][r]
            raise _undefined("shaft", letter, grade, f"position {letter!r} exists only up to 10 mm (got {lo} to {hi} mm)")
        return es, es - it
    if letter == "j":
        if grade not in ("5", "6", "7"):
            raise _undefined("shaft", letter, grade, "position 'j' is defined for IT5 to IT7 only")
        ei = float(tables()["shaft_j_lower_deviation_um"][grade][r])
        return ei + it, ei
    ei = _shaft_ei(letter, r, grade, "shaft")
    return ei + it, ei


def _delta(r: int, grade: str) -> float:
    lo, _ = tables()["size_ranges_mm"][r]
    if lo == 0:  # sizes up to 3 mm: delta is 0 in the standard
        return 0.0
    return _it(r, grade) - _it(r, str(int(grade) - 1))


def _hole_zone(letter: str, grade: str, r: int) -> tuple[float, float]:
    """Return (ES, EI) of a hole class."""
    it = _it(r, grade)
    n = _grade_number(grade)
    low = letter.lower()
    if letter == "H":
        return it, 0.0
    if letter == "JS":
        half = _js_half(it)
        return half, -half
    if low in _CLEARANCE_LETTERS:
        es = _shaft_es_table(low, r, grade)
        if es is None:
            lo, hi = tables()["size_ranges_mm"][r]
            raise _undefined("hole", letter, grade, f"position {letter!r} exists only up to 10 mm (got {lo} to {hi} mm)")
        return -es + it, -es
    if letter == "J":
        if grade not in ("6", "7", "8"):
            raise _undefined("hole", letter, grade, "position 'J' is defined for IT6 to IT8 only")
        es = float(tables()["hole_j_upper_deviation_um"][grade][r])
        return es, es - it
    # K..ZC: ES = -ei (+ delta)
    if n < 3:
        raise _undefined("hole", letter, grade, f"position {letter!r} is defined from IT3 upwards")
    # K..N use the tabulated k/m/n value up to IT8 even where the shaft k is 0 (IT8 and above).
    ei = _shaft_ei(low, r, "7" if letter == "K" and n <= 8 else grade, "hole")
    delta_applies = (letter in ("K", "M", "N") and n <= 8) or (letter not in ("K", "M", "N") and n <= 7)
    es = -ei + (_delta(r, grade) if delta_applies else 0.0)
    lo, _ = tables()["size_ranges_mm"][r]
    if letter == "N" and n > 8 and lo >= 3:
        es = 0.0  # N above IT8: ES = 0 for sizes over 3 mm
    return es, es - it


# --------------------------------------------------------------------------- public API


def _notes(kind: str, letter: str, grade: str, size_mm: float) -> tuple[str, ...]:
    notes = []
    exact = _EXACT_CHECKED_HOLE if kind == "hole" else _EXACT_CHECKED_SHAFT
    lo, hi = _EXACT_CHECK_RANGE_MM
    if letter.lower() not in ("h", "js") and (letter not in exact or not (lo < size_mm <= hi)):
        notes.append(
            f"{letter}{grade}: fundamental deviation transcribed from the ISO 286-1 table and checked "
            "against the standard's formulas only (about +/-4 %), not cell-for-cell against a second "
            "table source; verify against the standard before relying on it"
        )
    if kind == "hole" and letter in ("M", "N") and _grade_number(grade) > 8:
        notes.append(
            f"{letter}{grade}: rule for {letter} above IT8 (N: ES = 0 over 3 mm; M: ES = -ei) "
            "is not cross-checked by an independent source"
        )
    return tuple(notes)


def zone_deviations_um(size_mm: float, designation: str) -> Zone:
    """Limits of one class such as ``'H7'`` or ``'g6'`` for a nominal size."""
    kind, letter, grade = parse_designation(designation)
    _check_size(size_mm)
    r = _range_index(size_mm)
    if kind == "hole":
        upper, lower = _hole_zone(letter, grade, r)
    else:
        upper, lower = _shaft_zone(letter, grade, r)
    return Zone(
        kind=kind,
        letter=letter,
        grade=grade,
        upper_um=round(upper, 4),
        lower_um=round(lower, 4),
        tolerance_um=_it(r, grade),
        notes=_notes(kind, letter, grade, size_mm),
    )


def fit_limits(size_mm: float, code: str) -> FitLimits:
    """Limits, clearances and fit type of a hole/shaft pair such as ``'H7/g6'``."""
    hole_text, shaft_text = parse_fit_code(code)
    parse_designation(hole_text, expect="hole")
    parse_designation(shaft_text, expect="shaft")
    _check_size(size_mm)
    hole = zone_deviations_um(size_mm, hole_text)
    shaft = zone_deviations_um(size_mm, shaft_text)
    min_c = hole.lower_um - shaft.upper_um
    max_c = hole.upper_um - shaft.lower_um
    # ISO 286-1 3.x: clearance if EI >= es, interference if ES <= ei, otherwise transition.
    if hole.lower_um >= shaft.upper_um:
        fit_type = "clearance"
    elif hole.upper_um <= shaft.lower_um:
        fit_type = "interference"
    else:
        fit_type = "transition"
    r = _range_index(size_mm)
    t = tables()
    over, upto = t["size_ranges_mm"][r]
    main_over, main_upto = t["it_ranges_mm"][t["it_range_of_size_range"][r]]
    return FitLimits(
        size_mm=size_mm,
        size_range_mm=(main_over, main_upto),
        deviation_range_mm=(over, upto),
        hole=hole,
        shaft=shaft,
        min_clearance_um=round(min_c, 4),
        max_clearance_um=round(max_c, 4),
        fit_type=fit_type,
    )


__all__ = [
    "HOLE_LETTERS",
    "SHAFT_LETTERS",
    "SIZE_MAX_MM",
    "SIZE_MIN_MM",
    "FitLimits",
    "Zone",
    "fit_limits",
    "it_tolerance_um",
    "parse_designation",
    "parse_fit_code",
    "tables",
    "zone_deviations_um",
]
