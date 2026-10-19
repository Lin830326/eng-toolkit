"""Input checks shared by the ``calc`` tools: hard magnitude bounds and plain-number (dimensionless) parameters.

Error families are the existing ones: ``input.invalid`` for a value that is not allowed at all (zero, negative, the wrong
kind), ``input.out_of_range`` for a number that parses and is positive but is outside anything physical or any range the
formulas are meant for (a 10 km orifice, a 5 000 000 bar pump).  ``units.*`` families come from ``parse_quantity``.
"""

from __future__ import annotations

import math
from typing import Any

from pint import Quantity

from eng_toolkit.units import UnitsError, parse_quantity, require_positive


def is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def check_range(magnitude: float, *, name: str, unit: str, lo: float, hi: float, received: str) -> None:
    """Reject ``magnitude`` outside ``[lo, hi]`` (both in ``unit``) with ``input.out_of_range``."""
    if not lo <= magnitude <= hi:
        raise UnitsError(
            "input.out_of_range",
            f"'{name}' = {received} is outside the supported range {lo:g} .. {hi:g} {unit}",
            {"param": name, "received": received, "min": lo, "max": hi, "unit": unit},
        )


def bounded(qty: Quantity, *, name: str, unit: str, lo: float, hi: float) -> Quantity:
    """Reject a quantity whose magnitude in ``unit`` is outside ``[lo, hi]`` with ``input.out_of_range``."""
    check_range(float(qty.to(unit).magnitude), name=name, unit=unit, lo=lo, hi=hi, received=f"{qty:~P}")
    return qty


def revolutions_per_second(raw: Any, *, name: str, lo_rpm: float, hi_rpm: float) -> float:
    """A rotational speed (> 0, within ``[lo_rpm, hi_rpm]`` rpm) in revolutions per second.

    pint treats ``rpm`` as ``2*pi rad/min`` and ``Hz`` as ``1/s``, so ``1 Hz`` would convert to ``rpm`` as 9.55.  A unit
    that names revolutions or radians is converted by pint; a bare frequency (``Hz``, ``1/min``) already counts revolutions.
    """
    speed = require_positive(parse_quantity(raw, name=name, dimension="1 / [time]"), name=name)
    if "revolution" in str(speed.units) or "radian" in str(speed.units):
        rps = float(speed.to("rad/s").magnitude) / (2.0 * math.pi)
    else:
        rps = float(speed.to("1/s").magnitude)
    check_range(rps * 60.0, name=name, unit="rpm", lo=lo_rpm, hi=hi_rpm, received=f"{speed:~P}")
    return rps


def positive_quantity(raw: Any, *, name: str, dimension: str, unit: str, lo: float, hi: float) -> Quantity:
    """Parse ``raw`` as a quantity of ``dimension``; it must be > 0 and within ``[lo, hi]`` ``unit``."""
    qty = require_positive(parse_quantity(raw, name=name, dimension=dimension), name=name)
    return bounded(qty, name=name, unit=unit, lo=lo, hi=hi)


def plain_number(
    value: Any, *, name: str, lo: float, hi: float, lo_open: bool = False, hint: str = ""
) -> float:
    """A dimensionless parameter (efficiency, coefficient, exponent) given as a JSON number.

    ``lo_open`` makes the lower limit exclusive.  A string or bool is ``input.invalid``; a finite number outside the
    limits is ``input.invalid`` too, because for these parameters the limits are definitions (an efficiency above 1
    is a unit slip such as 85 for 0.85), not just plausibility bounds.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitsError(
            "input.invalid",
            f"'{name}' must be a plain number, got {value!r}",
            {"param": name, "received": repr(value)},
        )
    number = float(value)
    ok = math.isfinite(number) and (number > lo if lo_open else number >= lo) and number <= hi
    if not ok:
        bracket = "(" if lo_open else "["
        raise UnitsError(
            "input.invalid",
            f"'{name}' must be in {bracket}{lo:g}, {hi:g}], got {number:g}" + (f" ({hint})" if hint else ""),
            {"param": name, "received": number, "min": lo, "max": hi},
        )
    return number


__all__ = ["bounded", "check_range", "is_blank", "plain_number", "positive_quantity", "revolutions_per_second"]
