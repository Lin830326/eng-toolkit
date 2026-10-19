"""True position (ISO 1101) of measured features against nominal coordinates, with the MMC / LMC bonus tolerance.

Pure arithmetic in one length unit.  For a feature at nominal ``n`` measured at ``m`` the deviation is ``d = m - n`` and the
position error is the diameter of the smallest circle (2-D) or sphere (3-D) around the nominal point that holds the measured
point: ``2 * |d|``.  The feature is in position when that diameter does not exceed the stated tolerance plus the bonus.

Bonus tolerance (ISO 2692): with a maximum material condition modifier the position tolerance grows by how far the feature has
left its maximum material size, with a least material condition modifier by how far it has left its least material size.
For a hole the maximum material size is the *smallest* hole (the most material is left) and the least material size the largest;
for a shaft it is the other way round.  The bonus is never negative: a feature beyond its material limit is out of size and
gets no bonus (reported as a warning, since it fails its size tolerance).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from eng_toolkit.errors import ToolFailure

MODIFIERS = ("none", "M", "L")
FEATURE_KINDS = ("hole", "shaft")
# A deviation that lands exactly on the limit when typed with a few digits ("0.03, 0.04" against 0.1) can come out one or two
# ulps over in floating point; this slack keeps such a feature in position.  It is 1e-9 of the allowed zone plus four spacings
# of the largest coordinate (the rounding of the coordinates themselves), far below any measurement resolution.
RELATIVE_SLACK = 1e-9
ULPS = 4


@dataclass(frozen=True)
class PositionResult:
    deviation: np.ndarray  # (n, d) measured - nominal
    distance: np.ndarray  # (n,) |deviation|
    error: np.ndarray  # (n,) 2 * distance, the zone diameter the feature needs
    bonus: np.ndarray  # (n,) bonus tolerance granted by the size
    allowed: np.ndarray  # (n,) tolerance + bonus
    margin: np.ndarray  # (n,) allowed - error
    within: np.ndarray  # (n,) bool
    beyond_limit: np.ndarray  # (n,) bool: size is beyond the material limit that sets the bonus (out of size)
    outside_size_limits: np.ndarray  # (n,) bool: size is outside [min, max] when both limits were given


def bonus_for(
    sizes: np.ndarray, *, modifier: str, kind: str, mmc: float | None, lmc: float | None
) -> tuple[np.ndarray, np.ndarray]:
    """Bonus tolerance for each actual size, and which sizes lie beyond the material limit that sets the bonus."""
    if modifier == "none":
        return np.zeros(len(sizes)), np.zeros(len(sizes), dtype=bool)
    if modifier == "M":
        raw = sizes - mmc if kind == "hole" else mmc - sizes
    else:
        raw = lmc - sizes if kind == "hole" else sizes - lmc
    return np.maximum(raw, 0.0), raw < 0.0


def evaluate(
    nominal: np.ndarray,
    measured: np.ndarray,
    tolerance: float,
    *,
    modifier: str = "none",
    kind: str = "hole",
    sizes: np.ndarray | None = None,
    mmc: float | None = None,
    lmc: float | None = None,
) -> PositionResult:
    """Position of every row of ``measured`` against the same row of ``nominal`` (all in one length unit)."""
    if nominal.shape != measured.shape:
        raise ToolFailure(
            "input.invalid",
            f"nominal has shape {nominal.shape} but measured has shape {measured.shape}; give one measured point per nominal point",
            {"nominal_shape": list(nominal.shape), "measured_shape": list(measured.shape)},
        )
    deviation = measured - nominal
    distance = np.linalg.norm(deviation, axis=1)
    error = 2.0 * distance
    count = len(nominal)
    if modifier == "none":
        bonus, beyond = np.zeros(count), np.zeros(count, dtype=bool)
    else:
        bonus, beyond = bonus_for(sizes, modifier=modifier, kind=kind, mmc=mmc, lmc=lmc)
    outside = np.zeros(count, dtype=bool)
    if sizes is not None and mmc is not None and lmc is not None:
        lo, hi = (mmc, lmc) if kind == "hole" else (lmc, mmc)
        outside = (sizes < lo) | (sizes > hi)
    allowed = tolerance + bonus
    margin = allowed - error
    rounding = ULPS * float(np.spacing(max(np.abs(nominal).max(), np.abs(measured).max())))
    within = error <= allowed * (1.0 + RELATIVE_SLACK) + rounding
    return PositionResult(deviation, distance, error, bonus, allowed, margin, within, beyond, outside)


__all__ = ["FEATURE_KINDS", "MODIFIERS", "PositionResult", "RELATIVE_SLACK", "bonus_for", "evaluate"]
