"""Bounded quantity grammar, SI conversion and the standard tool result boundary."""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Any

import numpy as np

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.electric._server import ENGINE
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

_LITERAL = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)", re.ASCII)
_FACTOR = re.compile(r"[A-Za-z_µμ°%]+(?:\^[+-]?[0-9]{1,2})?", re.ASCII)
_POWER = {
    "real": {"W", "mW", "kW", "MW", "watt", "watts", "kilowatt", "megawatt", "hp", "horsepower"},
    "apparent": {"VA", "kVA", "MVA", "mVA"},
    "reactive": {"var", "kvar", "Mvar", "mvar"},
}
_POWER_UNITS = {"VA": "W", "kVA": "kW", "MVA": "MW", "mVA": "mW",
                "var": "W", "kvar": "kW", "Mvar": "MW", "mvar": "mW"}


def invalid(param: str, message: str, family: str = "input.invalid") -> None:
    raise UnitsError(family, message, {"param": "/" + param.lstrip("/")})


def choice(value, param, choices):
    if value not in choices:
        invalid(param, f"expected one of {choices}")
    return value


def number(value, param, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        invalid(param, "expected a finite dimensionless number")
    x = float(value)
    if not math.isfinite(x) or not lo <= x <= hi:
        invalid(param, f"supported range is {lo:g} .. {hi:g}", "input.out_of_range")
    return x


def count(value, param, lo, hi):
    x = number(value, param, lo, hi)
    if x != int(x):
        invalid(param, "expected an integer count")
    return int(x)


def scalar(raw, param, lo, hi):
    """Dimensionless literals in records, never Python expressions."""
    if not isinstance(raw, str) or len(raw) > 40:
        invalid(param, "dimensionless literal cap is 40 characters", "input.out_of_range")
    match = _LITERAL.fullmatch(raw.strip())
    if not match or match.group(2):
        invalid(param, "expected a dimensionless numeric literal")
    literal = match.group(1)
    value = float(literal)
    if value == 0 and any(c in "123456789" for c in literal.lower().split("e")[0]):
        invalid(param, "nonzero literal underflows double precision", "input.out_of_range")
    return number(value, param, lo, hi)


def q(raw, param, unit, *, zero=False, signed=False, optional=False, lo=1e-15, hi=1e15, power=None):
    if optional and raw == "":
        return None
    if not isinstance(raw, str):
        invalid(param, "physical quantities require strings with units", "units.bare_number")
    if not raw.strip():
        invalid(param, "required quantity is missing", "input.missing")
    if len(raw) > 120:
        invalid(param, "quantity cap is 120 characters", "input.out_of_range")
    match = _LITERAL.fullmatch(raw.strip())
    if not match:
        invalid(param, "expected a finite numeric literal and units", "units.parse_error")
    literal, units = match.groups()
    x0 = float(literal)
    if not math.isfinite(x0) or abs(x0) > 1e100 or (x0 == 0 and any(c in "123456789" for c in literal.lower().split("e")[0])):
        invalid(param, "literal magnitude exceeds numerical limits", "input.out_of_range")
    if not units:
        invalid(param, "every physical quantity needs units", "units.bare_number")
    if power:
        if units not in _POWER[power]:
            invalid(param, f"{power} power requires one of {sorted(_POWER[power])}; W, VA and var are not interchangeable", "units.dimension_mismatch")
        units = _POWER_UNITS.get(units, units)
    if len(units) > 64:
        invalid(param, "unit text cap is 64 characters", "input.out_of_range")
    units = re.sub(r"\s*([*/^])\s*", r"\1", units.replace("**", "^"))
    units = "*".join(units.split())
    factors = re.split(r"[*/]", units)
    for i, factor in enumerate(factors):
        if not _FACTOR.fullmatch(factor) and not (i == 0 and factor == "1" and "/" in units):
            invalid(param, "only named unit products/quotients and integer powers are accepted", "units.parse_error")
        if factor != "1":
            probe = parse_quantity({"value": 0, "unit": factor.split("^")[0]}, name=param)
            if probe.check("[temperature]") and probe.to("K").magnitude != 0:
                invalid(param, "temperature differences require K, delta_degC or delta_degF")
    target = _POWER_UNITS.get(unit, unit)
    qty = parse_quantity(literal + " " + units, name=param, dimension=str(ureg.Unit(target).dimensionality))
    x = float(qty.to(target).magnitude)
    if unit in ("rad", "rad/s") and not any(k in str(qty.units) for k in ("radian", "degree", "revolution", "rpm", "turn")):
        invalid(param, "angular quantities require rad, deg or rpm units", "units.dimension_mismatch")
    if unit == "Hz" and str(qty.units) not in ("hertz", "kilohertz", "megahertz", "millihertz"):
        invalid(param, "cyclic frequency requires Hz or its SI prefixes", "units.dimension_mismatch")
    if not math.isfinite(x) or abs(x) > hi or (x0 != 0 and abs(x) < lo):
        invalid(param, f"converted nonzero magnitude must be {lo:g} .. {hi:g} {unit}", "input.out_of_range")
    if (not signed and x < 0) or (not zero and x == 0):
        invalid(param, "quantity must be positive" if not zero else "quantity must be nonnegative")
    return x


def records(raw, param, cap=100, *, required=False):
    if not isinstance(raw, str):
        invalid(param, "expected record text")
    if len(raw) > 30000:
        invalid(param, "record text cap is 30000 characters", "input.out_of_range")
    if not raw.strip():
        if required:
            invalid(param, "records are required", "input.missing")
        return []
    parts = raw.replace("\r\n", "\n").replace("\n", ";").split(";")
    if len(parts) > cap:
        invalid(param, f"record cap is {cap}", "input.out_of_range")
    result = []
    for i, part in enumerate(parts):
        if len(part) > 1000:
            invalid(f"{param}/{i}", "record cap is 1000 characters", "input.out_of_range")
        if not part.strip():
            invalid(f"{param}/{i}", "empty record")
        result.append([s.strip() for s in part.split(",")])
    return result


def fields(row, allowed, param):
    if len(row) not in allowed or any(not item for item in row):
        invalid(param, f"expected {allowed} nonempty comma-separated fields")


def bisect(fn, low, high, param, *, rtol=1e-11, maxiter=100):
    a, b = float(low), float(high)
    fa, fb = fn(a), fn(b)
    if not math.isfinite(fa) or not math.isfinite(fb):
        invalid(param, "nonfinite solver bracket", "input.out_of_range")
    if fa == 0:
        return a
    if fb == 0:
        return b
    if (fa > 0) == (fb > 0):
        invalid(param, "no solution in the stated operating range", "electric.no_solution")
    for _ in range(min(maxiter, 160)):
        mid = a + (b-a)/2
        fm = fn(mid)
        if not math.isfinite(fm):
            invalid(param, "nonfinite solver residual", "input.out_of_range")
        if fm == 0 or b-a <= rtol * max(abs(a), abs(b), 1e-30):
            return mid
        if (fm > 0) == (fa > 0):
            a, fa = mid, fm
        else:
            b = mid
    invalid(param, "solver did not converge within its iteration cap", "electric.nonconvergence")


def finite(value: Any, path="result"):
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (not math.isfinite(value) or abs(value) > 1e100):
        invalid(path, "result exceeds finite numerical limits", "input.out_of_range")
    if isinstance(value, dict):
        return {k: finite(v, f"{path}/{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v, f"{path}/{i}") for i, v in enumerate(value)]
    return value


class Result:
    def __init__(self, formulas, assumptions, validity):
        self.values = {}
        self.data = {"formulas": formulas, "assumptions": assumptions, "validity": validity}
        self.warnings = []
        self.artifacts = []

    def add(self, name, value, unit="dimensionless"):
        self.values[name] = QuantityOut(quantity=finite(float(value)), unit=unit)

    def csv(self, handle, filename, headers, rows):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(finite(rows))
        self.artifacts.append(handle.write_text(filename, stream.getvalue(), fmt="csv"))

    def done(self, primary):
        return Computed(values=self.values, primary=primary, data=finite(self.data), warnings=self.warnings, artifacts=self.artifacts)


def run(name, inputs, compute):
    def guarded(handle):
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                return compute(handle)
        except UnitsError as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, details) from None
        except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
            invalid("inputs", f"unsupported numerical range or conditioning: {exc}", "input.out_of_range")
    return run_tool(tool_name="electric_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed textbook electrical estimate. Verify the model, ratings and operating limits; no compliance or protection check.",))
