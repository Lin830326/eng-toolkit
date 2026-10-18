"""Reverse lookup of ISO 286 fits: which standard fits keep the clearance inside a window.

Pure module: floats in millimetres and micrometres, no MCP, no pint.  Every limit comes from
:func:`eng_toolkit.domains.metrology.iso286.fit_limits`, so ``iso286`` stays the only owner of
the ISO 286 numbers; this module only chooses which classes to try, applies the window and ranks.
"""

from __future__ import annotations

from dataclasses import dataclass

from eng_toolkit.domains.metrology import iso286
from eng_toolkit.units import UnitsError

BASES = ("hole", "shaft")
FIT_SET_NAMES = ("preferred", "common")
MAX_RESULTS = 50
NEAREST_COUNT = 3

# ISO 286 / ANSI B4.2 preferred fits, loose clearance to heavy interference.
PREFERRED: dict[str, tuple[str, ...]] = {
    "hole": (
        "H11/c11", "H9/d9", "H8/f7", "H7/g6", "H7/h6",
        "H7/k6", "H7/n6", "H7/p6", "H7/s6", "H7/u6",
    ),
    "shaft": (
        "C11/h11", "D9/h9", "F8/h7", "G7/h6", "H7/h6",
        "K7/h6", "N7/h6", "P7/h6", "S7/h6", "U7/h6",
    ),
}  # fmt: skip

_SHAFT_LETTERS = ("c", "d", "e", "f", "g", "h", "js", "k", "m", "n", "p", "r", "s", "t", "u")
_HOLE_LETTERS = tuple(letter.upper() for letter in _SHAFT_LETTERS)
_GRADE_MAX = 11


def _hole_basis_common() -> tuple[str, ...]:
    """Holes H6..H11, each shaft letter at the hole's grade or one finer."""
    return tuple(
        f"H{hole}/{letter}{shaft}"
        for hole in range(6, _GRADE_MAX + 1)
        for letter in _SHAFT_LETTERS
        for shaft in (hole, hole - 1)
    )


def _shaft_basis_common() -> tuple[str, ...]:
    """Shafts h5..h11, each hole letter at the shaft's grade or one coarser."""
    return tuple(
        f"{letter}{hole}/h{shaft}"
        for shaft in range(5, _GRADE_MAX + 1)
        for letter in _HOLE_LETTERS
        for hole in (shaft, shaft + 1)
        if hole <= _GRADE_MAX
    )


FIT_SETS: dict[str, dict[str, tuple[str, ...]]] = {
    "hole": {"preferred": PREFERRED["hole"], "common": _hole_basis_common()},
    "shaft": {"preferred": PREFERRED["shaft"], "common": _shaft_basis_common()},
}


@dataclass(frozen=True)
class Candidate:
    fit: str
    preferred: bool
    limits: iso286.FitLimits
    violation_um: float
    """How far the fit's clearance range lies outside the window; 0 means it qualifies."""

    @property
    def qualifies(self) -> bool:
        return self.violation_um == 0

    @property
    def tolerance_sum_um(self) -> float:
        return self.limits.hole.tolerance_um + self.limits.shaft.tolerance_um

    @property
    def centre_um(self) -> float:
        return (self.limits.min_clearance_um + self.limits.max_clearance_um) / 2


@dataclass(frozen=True)
class SearchResult:
    size_range_mm: tuple[int, int]
    searched: int
    skipped_undefined: int
    candidates: tuple[Candidate, ...]
    """Every qualifying fit, best first."""
    nearest: tuple[Candidate, ...]
    """The closest misses, only when nothing qualifies."""


def _violation_um(limits: iso286.FitLimits, window_min_um: float, window_max_um: float) -> float:
    below = max(0.0, window_min_um - limits.min_clearance_um)
    above = max(0.0, limits.max_clearance_um - window_max_um)
    return round(below + above, 6)


def search_fits(
    size_mm: float, window_min_um: float, window_max_um: float, *, basis: str, fit_set: str
) -> SearchResult:
    """Fits of ``fit_set`` on ``basis`` whose clearance range lies inside the window.

    Clearance is hole minus shaft (interference negative), as in ``iso286.fit_limits``.  Ranking,
    best first: larger hole + shaft tolerance (cheaper to make), then fit centre nearest the window
    centre, then generation order.  The tolerance sum is a cost proxy, not a suitability judgement.
    """
    in_preferred = frozenset(PREFERRED[basis])
    evaluated: list[Candidate] = []
    skipped = 0
    for code in FIT_SETS[basis][fit_set]:
        try:
            limits = iso286.fit_limits(size_mm, code)
        except UnitsError as exc:
            if exc.family != "input.undefined_class":
                raise
            skipped += 1
            continue
        evaluated.append(
            Candidate(code, code in in_preferred, limits, _violation_um(limits, window_min_um, window_max_um))
        )

    window_centre = (window_min_um + window_max_um) / 2
    # Rounded so that float noise cannot reorder fits whose centres are equally far away.
    ranked = sorted(
        (c for c in evaluated if c.qualifies),
        key=lambda c: (-c.tolerance_sum_um, round(abs(c.centre_um - window_centre), 6)),
    )
    nearest = () if ranked else tuple(sorted(evaluated, key=lambda c: c.violation_um)[:NEAREST_COUNT])
    return SearchResult(
        # H7/h6 is in every set and defined at every size, so something was evaluated.
        size_range_mm=evaluated[0].limits.size_range_mm,
        searched=len(evaluated),
        skipped_undefined=skipped,
        candidates=tuple(ranked),
        nearest=nearest,
    )


__all__ = [
    "BASES",
    "FIT_SETS",
    "FIT_SET_NAMES",
    "MAX_RESULTS",
    "NEAREST_COUNT",
    "PREFERRED",
    "Candidate",
    "SearchResult",
    "search_fits",
]
