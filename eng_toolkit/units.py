"""Unit gatekeeper.

Every physical input crosses this module before it reaches an engine.

* Inputs are parsed with pint.  Accepted forms: a string like ``"100 mm"`` or
  ``"210 bar"``, or a mapping ``{"value": 100, "unit": "mm"}``.
* Bare numbers (``100``, ``"100"``) are **rejected** with error family
  ``units.bare_number`` -- a number without a unit is not an engineering quantity.
* Dimensionally wrong inputs (e.g. a length where a pressure is required) are
  rejected with ``units.dimension_mismatch``.
* Unparseable strings raise ``units.parse_error``.

All errors are :class:`UnitsError`, which carries the structured ``family`` /
``message`` / ``details`` triple so tools can convert them straight into the
return contract.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping

import pint
from pint import Quantity, UnitRegistry

from eng_toolkit.errors import ToolFailure

# One shared registry.  ``autoconvert_offset_to_baseunit`` lets "20 degC" convert cleanly.
ureg = UnitRegistry(autoconvert_offset_to_baseunit=True)
Q_ = ureg.Quantity

# Dimension names -> pint dimensionality strings.
DIMENSIONS: dict[str, str] = {
    "length": "[length]",
    "area": "[length] ** 2",
    "volume": "[length] ** 3",
    "pressure": "[mass] / [length] / [time] ** 2",
    "force": "[length] * [mass] / [time] ** 2",
    "velocity": "[length] / [time]",
    "volumetric_flow": "[length] ** 3 / [time]",
    "mass_flow": "[mass] / [time]",
    "density": "[mass] / [length] ** 3",
    "dynamic_viscosity": "[mass] / [length] / [time]",
    "kinematic_viscosity": "[length] ** 2 / [time]",
    "temperature": "[temperature]",
    "time": "[time]",
    "power": "[length] ** 2 * [mass] / [time] ** 3",
    "torque": "[length] ** 2 * [mass] / [time] ** 2",
    "dimensionless": "dimensionless",
}


class UnitsError(ToolFailure):
    """Structured unit error; maps 1:1 onto :class:`eng_toolkit.contract.ToolError`."""


def _is_bare_number(raw: Any) -> bool:
    if isinstance(raw, bool):
        return False
    if isinstance(raw, Real):
        return True
    if isinstance(raw, str):
        try:
            float(raw.strip())
            return True
        except ValueError:
            return False
    return False


def parse_quantity(raw: Any, *, name: str, dimension: str | None = None) -> Quantity:
    """Parse ``raw`` into a pint Quantity, enforcing a unit and (optionally) a dimension.

    Parameters
    ----------
    raw:
        ``"100 mm"`` | ``{"value": 100, "unit": "mm"}`` | an existing pint Quantity.
    name:
        Parameter name, echoed in error details.
    dimension:
        One of :data:`DIMENSIONS` keys (``"pressure"``) or a raw pint dimensionality
        string.  ``None`` skips the dimension check.
    """
    if raw is None:
        raise UnitsError("input.missing", f"'{name}' is required", {"param": name})

    if isinstance(raw, Quantity):
        qty = raw
    elif _is_bare_number(raw):
        raise UnitsError(
            "units.bare_number",
            f"'{name}' was given as a bare number ({raw!r}); every quantity needs a unit, "
            f"e.g. '{raw} mm' or {{'value': {raw}, 'unit': 'mm'}}",
            {"param": name, "received": raw},
        )
    elif isinstance(raw, Mapping):
        if "value" not in raw or "unit" not in raw:
            raise UnitsError(
                "units.parse_error",
                f"'{name}' mapping must have 'value' and 'unit' keys",
                {"param": name, "received": dict(raw)},
            )
        value, unit = raw["value"], raw["unit"]
        if not isinstance(value, Real) or isinstance(value, bool):
            raise UnitsError(
                "units.parse_error",
                f"'{name}'.value must be a number, got {value!r}",
                {"param": name, "received": dict(raw)},
            )
        if not isinstance(unit, str) or not unit.strip():
            raise UnitsError(
                "units.bare_number",
                f"'{name}' has an empty unit; every quantity needs a unit",
                {"param": name, "received": dict(raw)},
            )
        try:
            qty = Q_(float(value), unit)
        except (pint.errors.UndefinedUnitError, pint.errors.DefinitionSyntaxError, ValueError) as exc:
            raise UnitsError(
                "units.parse_error",
                f"'{name}': unknown unit {unit!r} ({exc})",
                {"param": name, "received": dict(raw)},
            ) from exc
    elif isinstance(raw, str) and raw.strip().startswith("{"):
        # A JSON-encoded {"value": .., "unit": ".."} arriving through a string-only schema.
        import json

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnitsError(
                "units.parse_error",
                f"'{name}': {raw!r} looks like JSON but is not valid ({exc.msg})",
                {"param": name, "received": raw},
            ) from exc
        return parse_quantity(decoded, name=name, dimension=dimension)
    elif isinstance(raw, str):
        text = raw.strip()
        if not any(ch.isdigit() for ch in text):
            raise UnitsError(
                "units.parse_error",
                f"'{name}': {raw!r} has no numeric part; expected '<number> <unit>'",
                {"param": name, "received": raw},
            )
        try:
            parsed = ureg.parse_expression(text)
        except Exception as exc:  # pint raises several error types here
            raise UnitsError(
                "units.parse_error",
                f"'{name}': could not parse {raw!r} as a quantity ({exc})",
                {"param": name, "received": raw},
            ) from exc
        if not isinstance(parsed, Quantity):
            # e.g. "mm" alone parses to a Unit
            raise UnitsError(
                "units.parse_error",
                f"'{name}': {raw!r} is not '<number> <unit>'",
                {"param": name, "received": raw},
            )
        qty = parsed
        if qty.dimensionless and dimension not in (None, "dimensionless"):
            raise UnitsError(
                "units.bare_number",
                f"'{name}' ({raw!r}) has no unit; every quantity needs a unit",
                {"param": name, "received": raw},
            )
    else:
        raise UnitsError(
            "units.parse_error",
            f"'{name}': unsupported input type {type(raw).__name__}",
            {"param": name, "received": repr(raw)},
        )

    magnitude = float(qty.magnitude)
    if not math.isfinite(magnitude):
        raise UnitsError("input.invalid", f"'{name}' is not finite ({magnitude})", {"param": name})

    if dimension is not None:
        expected = DIMENSIONS.get(dimension, dimension)
        if not qty.check(expected):
            raise UnitsError(
                "units.dimension_mismatch",
                f"'{name}' must be a {dimension} ({expected}); got {qty:~P} with dimensionality "
                f"{qty.dimensionality}",
                {
                    "param": name,
                    "expected_dimension": dimension,
                    "expected_dimensionality": str(ureg.get_dimensionality(expected)),
                    "received": str(raw),
                    "received_dimensionality": str(qty.dimensionality),
                },
            )
    return qty


def require_positive(qty: Quantity, *, name: str) -> Quantity:
    """Reject zero or negative magnitudes (dimension-agnostic)."""
    if float(qty.magnitude) <= 0:
        raise UnitsError(
            "input.invalid",
            f"'{name}' must be > 0, got {qty:~P}",
            {"param": name, "received": str(qty)},
        )
    return qty


def to_quantity_out(qty: Quantity, unit: str | None = None, uncertainty: Quantity | None = None):
    """Convert a pint Quantity into the contract's :class:`QuantityOut`.

    ``unit`` selects the output unit (defaults to the quantity's own unit);
    ``uncertainty`` is converted to that same unit.
    """
    from eng_toolkit.contract import QuantityOut  # local import: contract must not import pint

    out = qty.to(unit) if unit else qty
    unc = None
    if uncertainty is not None:
        unc = float(uncertainty.to(out.units).magnitude)
    return QuantityOut(quantity=float(out.magnitude), unit=str(out.units), uncertainty=unc)


__all__ = [
    "DIMENSIONS",
    "Q_",
    "UnitsError",
    "parse_quantity",
    "require_positive",
    "to_quantity_out",
    "ureg",
]
