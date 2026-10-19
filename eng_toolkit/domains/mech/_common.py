"""Input checks shared by the ``mech`` tools.

Error families are the existing ones: ``input.invalid`` for a value that is not allowed at all (zero, negative, an unknown
choice, a plain number that is a definition-level mistake such as an efficiency above 1), ``input.out_of_range`` for a value
that parses and is positive but lies outside anything physical or the range the formulas are meant for, and ``units.*`` from
``parse_quantity``.  Warnings (in the tools) cover values that are legal but outside the usual validity range of a formula.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import pint
from pint import Quantity
from pydantic import Field

from eng_toolkit.contract import QuantityOut
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, to_quantity_out

# Tool parameters are plain JSON strings (no unions, no nested objects) so that small local models can fill them reliably.
QuantityIn = Annotated[
    str,
    Field(
        description=(
            "Number followed by a unit, as one string, e.g. '100 mm', '20 kN', '210 GPa'. "
            "A bare number without a unit is rejected."
        )
    ),
]

UnitSystemIn = Annotated[
    str,
    Field(description="Unit system of the results: 'metric' (kN, N*m, mm, MPa) or 'imperial' (lbf, lbf*ft, inch, ksi)"),
]

_UNIT_SYSTEMS: dict[str, dict[str, str]] = {
    "metric": {
        "force": "kN",
        "moment": "kN*m",
        "torque": "N*m",
        "length": "mm",
        "stress": "MPa",
        "area": "mm**2",
        "rate": "N/mm",
        "small_force": "N",
    },
    "imperial": {
        "force": "lbf",
        "moment": "inch*lbf",
        "torque": "foot*lbf",
        "length": "inch",
        "stress": "ksi",
        "area": "inch**2",
        "rate": "lbf/inch",
        "small_force": "lbf",
    },
}


def unit_system(name: Any) -> dict[str, str]:
    key = str(name).strip().lower() if isinstance(name, str) else ""
    if key not in _UNIT_SYSTEMS:
        raise UnitsError(
            "input.invalid",
            f"'unit_system' must be one of {sorted(_UNIT_SYSTEMS)}, got {name!r}",
            {"param": "unit_system", "received": str(name), "allowed": sorted(_UNIT_SYSTEMS)},
        )
    return _UNIT_SYSTEMS[key]


def choice(value: Any, *, name: str, allowed: tuple[str, ...]) -> str:
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_") if isinstance(value, str) else ""
    if key not in allowed:
        raise UnitsError(
            "input.invalid",
            f"'{name}' must be one of {list(allowed)}, got {value!r}",
            {"param": name, "received": str(value), "allowed": list(allowed)},
        )
    return key


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
    check_range(float(qty.to(unit).magnitude), name=name, unit=unit, lo=lo, hi=hi, received=f"{qty:~P}")
    return qty


def positive_quantity(raw: Any, *, name: str, dimension: str, unit: str, lo: float, hi: float) -> Quantity:
    """Parse ``raw`` as a quantity of ``dimension``; it must be > 0 and within ``[lo, hi]`` ``unit``."""
    qty = require_positive(parse_quantity(raw, name=name, dimension=dimension), name=name)
    return bounded(qty, name=name, unit=unit, lo=lo, hi=hi)


def optional_quantity(raw: Any, *, name: str, dimension: str, unit: str, lo: float, hi: float) -> Quantity | None:
    return None if is_blank(raw) else positive_quantity(raw, name=name, dimension=dimension, unit=unit, lo=lo, hi=hi)


def plain_number(value: Any, *, name: str, lo: float, hi: float, lo_open: bool = False, hint: str = "") -> float:
    """A dimensionless parameter given as a JSON number; outside ``[lo, hi]`` is ``input.invalid`` (a definition, not plausibility)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitsError("input.invalid", f"'{name}' must be a plain number, got {value!r}", {"param": name, "received": repr(value)})
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


def out(qty: Quantity, unit: str) -> QuantityOut:
    """``to_quantity_out`` for the fixed unit of a unit system (never a user-supplied string)."""
    try:
        return to_quantity_out(qty, unit)
    except pint.errors.PintError as exc:  # pragma: no cover - the unit systems above are fixed and valid
        raise UnitsError("units.parse_error", f"cannot express result in {unit!r} ({exc})", {"unit": unit}) from exc


__all__ = [
    "QuantityIn",
    "UnitSystemIn",
    "bounded",
    "check_range",
    "choice",
    "is_blank",
    "optional_quantity",
    "out",
    "plain_number",
    "positive_quantity",
    "revolutions_per_second",
    "unit_system",
]
