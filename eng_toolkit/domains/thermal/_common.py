"""Input checks and output helpers shared by the ``thermal`` tools.

Error families are the existing ones (same convention as ``calc`` and ``mech``): ``input.invalid`` for a value that is not
allowed at all (zero, negative, an unknown choice, a temperature at or below absolute zero, a plain number that is a
definition-level mistake), ``input.out_of_range`` for a value that parses and is positive but lies outside anything physical
or the range the formulas are meant for, and ``units.*`` from ``parse_quantity``.  Warnings (in the tools) cover values that are
legal but outside the stated validity range of a correlation.

Temperatures need care.  An *absolute* temperature (``20 degC``, ``300 K``, ``68 degF``) goes through :func:`temperature` and
comes back in kelvin; a ``delta_degC`` there is refused, because ``20 delta_degC`` is a difference and would silently be read
as 20 K.  A unit that *contains* a temperature in a rate (``W/(m*degC)``, ``J/(kg*degF)``) is a per-degree quantity, never an
offset temperature: pint would otherwise convert ``0.04 W/m/degC`` to ``0.04/274.15 W/m/K``, so :func:`rate_quantity` reads it
as ``delta_degC`` and says so.  No tool takes a temperature difference as an input; they take two absolute temperatures.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Annotated, Any

import pint
from pint import Quantity
from pydantic import Field

from eng_toolkit.contract import QuantityOut
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, ureg

# Tool parameters are plain JSON strings (no unions, no nested objects) so that small local models can fill them reliably.
QuantityIn = Annotated[
    str,
    Field(description="Number followed by a unit, as one string, e.g. '100 mm', '0.04 W/(m*K)', '80 degC'. A bare number is rejected."),
]
TemperatureIn = Annotated[
    str,
    Field(description="ABSOLUTE temperature with unit, e.g. '80 degC', '353.15 K', '176 degF'. Never a difference (delta_degC is refused)."),
]
UnitSystemIn = Annotated[str, Field(description="Unit system of the results: 'metric' (degC, W, m, W/m2/K) or 'imperial' (degF, Btu/h, ft, Btu/h/ft2/degF)")]

# Dimensionalities (pint) of the rate-type quantities.
DIM_K = "[length] * [mass] / [time] ** 3 / [temperature]"  # thermal conductivity W/(m K)
DIM_H = "[mass] / [time] ** 3 / [temperature]"  # heat transfer coefficient W/(m2 K)
DIM_UA = "[length] ** 2 * [mass] / [time] ** 3 / [temperature]"  # W/K
DIM_CP = "[length] ** 2 / [time] ** 2 / [temperature]"  # J/(kg K)
DIM_RA = "[temperature] * [time] ** 3 / [mass]"  # areal thermal resistance m2 K/W
DIM_BETA = "1 / [temperature]"  # volumetric expansion coefficient 1/K
DIM_POWER = "[length] ** 2 * [mass] / [time] ** 3"

SIGMA = 5.670374419e-8  # Stefan-Boltzmann constant, W/(m2 K4) (CODATA 2018, exact since the 2019 SI)
GRAVITY = 9.80665  # m/s2

# Output unit systems: key -> unit expressed in the system.  SI values are converted with pint.
_UNIT_SYSTEMS: dict[str, dict[str, str]] = {
    "metric": {
        "temperature": "degC", "dtemp": "K", "power": "W", "length": "m", "small_length": "mm", "area": "m**2",
        "h": "W/m**2/K", "ua": "W/K", "resistance": "K/W", "k": "W/m/K", "mass_flow": "kg/s", "energy": "J",
        "flux": "W/m**2", "power_per_length": "W/m", "areal_resistance": "m**2*K/W", "energy_per_area": "J/m**2",
        "energy_per_length": "J/m", "time": "s",
    },
    "imperial": {
        "temperature": "degF", "dtemp": "delta_degF", "power": "Btu/hour", "length": "foot", "small_length": "inch",
        "area": "foot**2", "h": "Btu/hour/foot**2/delta_degF", "ua": "Btu/hour/delta_degF", "resistance": "delta_degF*hour/Btu",
        "k": "Btu/hour/foot/delta_degF", "mass_flow": "lb/hour", "energy": "Btu", "flux": "Btu/hour/foot**2",
        "power_per_length": "Btu/hour/foot", "areal_resistance": "foot**2*hour*delta_degF/Btu",
        "energy_per_area": "Btu/foot**2", "energy_per_length": "Btu/foot", "time": "s",
    },
}

# SI unit of each key (what the formulas produce).
_SI: dict[str, str] = {
    "temperature": "K", "dtemp": "K", "power": "W", "length": "m", "small_length": "m", "area": "m**2", "h": "W/m**2/K",
    "ua": "W/K", "resistance": "K/W", "k": "W/m/K", "mass_flow": "kg/s", "energy": "J", "flux": "W/m**2",
    "power_per_length": "W/m", "areal_resistance": "m**2*K/W", "energy_per_area": "J/m**2", "energy_per_length": "J/m",
    "time": "s",
}

T_MIN_K, T_MAX_K = 1.0, 1.0e4


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
    """Reject ``magnitude`` outside ``[lo, hi]`` (both in ``unit``) with ``input.out_of_range``; NaN and infinity fail too."""
    if not lo <= magnitude <= hi:
        raise UnitsError(
            "input.out_of_range",
            f"'{name}' = {received} is outside the supported range {lo:g} .. {hi:g} {unit}",
            {"param": name, "received": received, "min": lo, "max": hi, "unit": unit},
        )


# ----------------------------------------------------------------------------------------------- temperatures and rates

_strict: pint.UnitRegistry | None = None
_CELSIUS = re.compile(r"(?<![A-Za-z_])(?:deg_?C|degree_?Celsius|celsius|°\s*C|℃)(?![A-Za-z_])", re.IGNORECASE)
_FAHRENHEIT = re.compile(r"(?<![A-Za-z_])(?:deg_?F|degree_?Fahrenheit|fahrenheit|°\s*F|℉)(?![A-Za-z_])", re.IGNORECASE)
_TEMP_HINT = re.compile(r"deg|°|celsius|fahrenheit|℃|℉", re.IGNORECASE)


def _as_text(raw: Any) -> Any:
    """A ``{"value": .., "unit": ..}`` mapping (or its JSON string) as ``'<value> <unit>'``; anything else unchanged."""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, Mapping) and isinstance(raw.get("unit"), str) and isinstance(raw.get("value"), (int, float)) and not isinstance(raw.get("value"), bool):
        return f"{float(raw['value'])!r} {raw['unit']}"
    return raw


def rate_quantity(raw: Any, *, name: str, dimension: str, notes: list[str] | None = None) -> Quantity:
    """Parse a quantity whose unit contains a temperature *per* something (``W/(m*K)``, ``J/(kg*degC)``).

    ``degC`` and ``degF`` inside such a unit are read as differences (``delta_degC`` / ``delta_degF``) and ``notes`` receives a
    sentence saying so; without this pint treats them as offset temperatures and returns a value wrong by a factor of ~270.
    """
    text = _as_text(raw)
    if isinstance(text, str) and _TEMP_HINT.search(text):
        global _strict
        if _strict is None:
            _strict = pint.UnitRegistry(autoconvert_offset_to_baseunit=False)
        try:
            _strict.parse_expression(text.strip())
        except pint.errors.OffsetUnitCalculusError:
            fixed = _FAHRENHEIT.sub("delta_degF", _CELSIUS.sub("delta_degC", text.strip()))
            try:
                _strict.parse_expression(fixed)
            except pint.errors.PintError as exc:
                raise UnitsError(
                    "units.parse_error",
                    f"'{name}': {raw!r} mixes an offset temperature unit into a rate; write K, delta_degC or delta_degF ({exc})",
                    {"param": name, "received": str(raw)},
                ) from exc
            if notes is not None:
                notes.append(f"'{name}' = {raw!r}: degC/degF inside a rate was read as a temperature difference ({fixed!r})")
            text = fixed
        except pint.errors.PintError:
            pass  # let parse_quantity report the real problem
    return parse_quantity(text, name=name, dimension=dimension)


def temperature(raw: Any, *, name: str) -> float:
    """An absolute temperature in kelvin.  ``delta_*`` units are refused; at or below 0 K is ``input.invalid``."""
    qty = parse_quantity(_as_text(raw), name=name, dimension="temperature")
    if "delta_" in str(qty.units):
        raise UnitsError(
            "input.invalid",
            f"'{name}' = {raw!r} is a temperature difference; this parameter is an absolute temperature (use degC, K or degF)",
            {"param": name, "received": str(raw)},
        )
    try:
        kelvin = float(qty.to("kelvin").magnitude)
    except (OverflowError, pint.errors.PintError) as exc:  # pragma: no cover - parse_quantity already checked the dimension
        raise UnitsError("units.parse_error", f"'{name}': cannot convert {raw!r} to kelvin ({exc})", {"param": name}) from exc
    if math.isfinite(kelvin) and kelvin <= 0.0:
        raise UnitsError(
            "input.invalid",
            f"'{name}' = {raw!r} is at or below absolute zero ({kelvin:.4g} K)",
            {"param": name, "received": str(raw), "kelvin": kelvin},
        )
    check_range(kelvin, name=name, unit="K", lo=T_MIN_K, hi=T_MAX_K, received=f"{qty:~P}")
    return kelvin


def positive_quantity(
    raw: Any, *, name: str, dimension: str, unit: str, lo: float, hi: float, notes: list[str] | None = None, allow_zero: bool = False
) -> float:
    """Parse ``raw`` as a quantity of ``dimension`` and return its magnitude in ``unit`` (SI), > 0 (or >= 0) and in ``[lo, hi]``.

    Rate-type dimensions (those with a temperature in the unit) go through :func:`rate_quantity`.
    """
    has_temperature = "temperature" in dimension
    qty = rate_quantity(raw, name=name, dimension=dimension, notes=notes) if has_temperature and dimension != "temperature" else parse_quantity(
        _as_text(raw), name=name, dimension=dimension
    )
    if float(qty.magnitude) < 0 or (float(qty.magnitude) == 0 and not allow_zero):
        raise UnitsError("input.invalid", f"'{name}' must be {'>= 0' if allow_zero else '> 0'}, got {qty:~P}", {"param": name, "received": str(raw)})
    try:
        value = float(qty.to(unit).magnitude)
    except OverflowError:  # pragma: no cover
        value = math.inf
    check_range(value, name=name, unit=unit, lo=lo, hi=hi, received=f"{qty:~P}")
    return value


def optional_quantity(raw: Any, *, name: str, dimension: str, unit: str, lo: float, hi: float, notes: list[str] | None = None) -> float | None:
    return None if is_blank(raw) else positive_quantity(raw, name=name, dimension=dimension, unit=unit, lo=lo, hi=hi, notes=notes)


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


def whole_number(value: Any, *, name: str, lo: int, hi: int) -> int:
    number = plain_number(value, name=name, lo=lo, hi=hi)
    if number != int(number):
        raise UnitsError("input.invalid", f"'{name}' must be a whole number, got {number:g}", {"param": name, "received": number})
    return int(number)


def split_list(raw: Any, *, name: str) -> list[Any]:
    """A list given as text ``'a; b; c'`` (also newline-separated) or as a JSON array; every item is returned as given."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if not isinstance(raw, str):
        raise UnitsError("input.invalid", f"'{name}' must be a text list or a JSON array, got {type(raw).__name__}", {"param": name})
    text = raw.strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnitsError("input.invalid", f"'{name}': invalid JSON ({exc.msg})", {"param": name, "received": raw}) from exc
        if not isinstance(items, list):
            raise UnitsError("input.invalid", f"'{name}': expected a JSON array", {"param": name, "received": raw})
        return items
    parts = [p.strip() for p in re.split(r"[;\n]", text)]
    return [p for p in parts if p]


# ----------------------------------------------------------------------------------------------- outputs


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise UnitsError(
            "input.out_of_range",
            f"the result '{label}' is not a finite number for these inputs (overflow or division by zero); the inputs are outside what the formula supports",
            {"result": label},
        )
    return value


def out(value: float, key: str, system: dict[str, str], *, label: str) -> QuantityOut:
    """``value`` in the SI unit of ``key``, expressed in the caller's unit system.  Never returns NaN or infinity."""
    _finite(float(value), label)
    target = system[key]
    si = _SI[key]
    try:
        converted = ureg.Quantity(float(value), si).to(target)
    except pint.errors.PintError as exc:  # pragma: no cover - the unit systems above are fixed and valid
        raise UnitsError("units.parse_error", f"cannot express {label} in {target!r} ({exc})", {"unit": target}) from exc
    return QuantityOut(quantity=_finite(float(converted.magnitude), label), unit=str(converted.units))


def plain(value: float, *, label: str) -> QuantityOut:
    return QuantityOut(quantity=_finite(float(value), label), unit="dimensionless")


def check_data(data: Any, path: str = "data") -> Any:
    """Return ``data`` after checking that no float in it is NaN or infinite (MCP clients reject such JSON)."""
    if isinstance(data, float):
        _finite(data, path)
    elif isinstance(data, Mapping):
        for key, item in data.items():
            check_data(item, f"{path}.{key}")
    elif isinstance(data, (list, tuple)):
        for index, item in enumerate(data):
            check_data(item, f"{path}[{index}]")
    return data


__all__ = [
    "DIM_BETA", "DIM_CP", "DIM_H", "DIM_K", "DIM_POWER", "DIM_RA", "DIM_UA", "GRAVITY", "QuantityIn", "SIGMA",
    "TemperatureIn", "UnitSystemIn", "check_data", "check_range", "choice", "is_blank", "optional_quantity", "out",
    "plain", "plain_number", "positive_quantity", "rate_quantity", "split_list", "temperature", "unit_system", "whole_number",
]
