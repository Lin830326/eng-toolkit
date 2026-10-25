"""Bounded input parsing, finite output, regression and safeguarded numerics."""

from __future__ import annotations

from eng_toolkit import jsonsafe
import csv
import io
import json
import math
import re
from typing import Any

import numpy as np

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg
from ._server import ENGINE

MAX_TEXT = 60000
NUM = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)
QUANTITY = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)", re.ASCII)
UNIT_TOKEN = re.compile(r"[A-Za-z_%µ°]+|[*/^()]|[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", re.ASCII)
SPECS = {
    "stress": ("MPa", 1e-12, 1e7),
    "length": ("m", 1e-12, 1e4),
    "force": ("N", 1e-12, 1e12),
    "strain": ("dimensionless", 1e-15, 10),
    "cycles": ("count", 1e-12, 1e18),
    "K": ("MPa*m^0.5", 1e-12, 1e6),
    "time": ("hour", 1e-12, 1e12),
    "temperature": ("K", 1e-9, 5000),
    "temperature_scale": ("K", 1e-9, 1e7),
}


def invalid(param, message, family="input.invalid"):
    raise UnitsError(family, message, {"param": "/" + param.lstrip("/")})


def choice(raw, param, options):
    if raw not in options:
        invalid(param, f"expected one of {tuple(options)}")
    return raw


def number(raw, param, lo, hi):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        invalid(param, "expected a finite dimensionless number")
    x = float(raw)
    if not math.isfinite(x) or not lo <= x <= hi:
        invalid(param, f"supported range is {lo:g} .. {hi:g}", "input.out_of_range")
    return x


def integer(raw, param, lo, hi):
    x = number(raw, param, lo, hi)
    if x != int(x):
        invalid(param, "expected a whole number")
    return int(x)


def literal(text, param, lo=-1e100, hi=1e100):
    if not isinstance(text, str) or len(text) > 80 or not NUM.fullmatch(text):
        invalid(param, "expected a finite decimal literal")
    x = number(float(text), param, lo, hi)
    if x == 0 and any(c in "123456789" for c in text.lower().split("e")[0]):
        invalid(param, "nonzero literal underflows", "input.out_of_range")
    return x


def unit_syntax(text, param):
    """Only unit products/quotients and powers, at most three parentheses deep."""
    text = text.replace("**", "^")
    text = re.sub(r"sqrt\(([A-Za-z_]+)\)", r"(\1^0.5)", text)
    # Whitespace between names denotes a product; spaces by operators do not.
    text = re.sub(r"(?<=[A-Za-z_%µ°)])\s+(?=[A-Za-z_%µ°(])", "*", text)
    text = "".join(text.split())
    tokens = UNIT_TOKEN.findall(text)
    if not text or "".join(tokens) != text or len(tokens) > 55:
        invalid(param, "use named units, products, quotients and numeric powers only", "units.parse_error")
    pos = 0

    def product(depth):
        nonlocal pos
        if depth > 3:
            invalid(param, "unit parentheses exceed depth three", "units.parse_error")
        while True:
            if pos == len(tokens):
                invalid(param, "incomplete unit expression", "units.parse_error")
            token = tokens[pos]
            pos += 1
            if token == "(":
                product(depth + 1)
                if pos == len(tokens) or tokens[pos] != ")":
                    invalid(param, "unclosed unit parentheses", "units.parse_error")
                pos += 1
            elif not token[0].isalpha() and token not in ("%", "°", "1"):
                invalid(param, "expected a named unit", "units.parse_error")
            if pos < len(tokens) and tokens[pos] == "^":
                pos += 1
                if pos == len(tokens):
                    invalid(param, "missing unit power", "units.parse_error")
                literal(tokens[pos], param, -20, 20)
                pos += 1
            if pos == len(tokens) or tokens[pos] == ")":
                return
            if tokens[pos] not in ("*", "/"):
                invalid(param, "expected unit product or quotient", "units.parse_error")
            pos += 1

    product(0)
    if pos != len(tokens):
        invalid(param, "unexpected unit parentheses", "units.parse_error")
    return text


# Pint defines cycle (and cycles) as an alias of turn = 2*pi radian. A fatigue cycle is
# a pure count, so these spellings are rewritten to Pint's factor-one `count` unit.
COUNT_SPELLINGS = ("cycle", "cycles", "count", "dimensionless", "1")
CYCLE_WORD = re.compile(r"(?<![A-Za-z_%µ°])cycles?(?![A-Za-z_%µ°0-9])")
COUNT_UNIT_NAMES = {"count", "dimensionless"}
TEMPERATURE_SCALES = {"kelvin", "degree_Rankine"}


def _angular(units):
    """True when any factor is an angle (radian-based in Pint), e.g. rad, deg, turn, revolution."""
    return any("radian" in ureg.Quantity(1, name).to_base_units().units._units for name in units._units)


def count_units(units, param):
    """Validate a count unit; counts always have factor one and never Pint's turn semantics."""
    if units in COUNT_SPELLINGS:
        return
    qty = parse_quantity({"value": 1.0, "unit": units}, name=param)
    if _angular(qty.units):
        invalid(param, "angular units such as rad, deg, turn or revolution are not cycle counts", "units.dimension_mismatch")
    invalid(param, f"cycle counts use one of {COUNT_SPELLINGS} as the unit", "units.dimension_mismatch")


def composite_count(units, target, param):
    """Rewrite cycle -> count in a composite unit; accept count power 0 or the target's power."""
    units, target = CYCLE_WORD.sub("count", units), CYCLE_WORD.sub("count", target)
    qty = parse_quantity({"value": 1.0, "unit": units}, name=param)
    if _angular(qty.units):
        invalid(param, "angular units, including prefixed cycle units, are not cycle counts", "units.dimension_mismatch")
    power = qty.units._units.get("count", 0)
    expected = ureg.Unit(target)._units.get("count", 0)
    if power not in (0, expected):
        invalid(param, f"per-cycle coefficient units must contain cycle to the power {expected:g} or omit it", "units.dimension_mismatch")
    return units, target


def q(raw, param, kind, *, signed=False, zero=False, optional=False, target=None):
    if optional and raw == "":
        return None
    if not isinstance(raw, str):
        invalid(param, "physical quantities need a string with units", "units.bare_number")
    if not raw.strip():
        invalid(param, "required quantity is missing", "input.missing")
    if len(raw) > 180:
        invalid(param, "quantity cap is 180 characters", "input.out_of_range")
    match = QUANTITY.fullmatch(raw.strip())
    if not match:
        invalid(param, "expected a finite decimal literal and units", "units.parse_error")
    value, units = match.groups()
    val = literal(value, param)
    if not units:
        invalid(param, "every physical quantity needs units", "units.bare_number")
    units = unit_syntax(units, param)
    dest, lower, upper = SPECS[kind] if target is None else (target, 1e-100, 1e100)
    if kind == "cycles" and target is None:
        count_units(units, param)
        units = "count"
    elif target is not None:
        units, dest = composite_count(units, dest, param)
    qty = parse_quantity({"value": val, "unit": units}, name=param)
    if qty.dimensionality != ureg.Unit(dest).dimensionality:
        invalid(param, f"quantity dimensions must match {dest}", "units.dimension_mismatch")
    if kind == "temperature_scale":
        # Scale quantities with a temperature dimension (Larson-Miller P): offsets are meaningless.
        if str(qty.units) not in TEMPERATURE_SCALES:
            invalid(param, "temperature-scaled parameters accept K or degR only; degC/degF offsets are meaningless", "units.dimension_mismatch")
    if kind == "temperature":
        # Absolute temperatures, including Pint's aliases for offset units.
        if len(qty.units._units) != 1 or next(iter(qty.units._units.values())) != 1:
            invalid(param, "absolute temperature requires a single temperature unit")
        if "delta_" in str(qty.units):
            invalid(param, "absolute temperature cannot use delta units")
    x = float(qty.to(dest).magnitude)
    if not math.isfinite(x) or abs(x) > upper or (val != 0 and abs(x) < lower and kind != "temperature"):
        invalid(param, f"supported nonzero range is {lower:g} .. {upper:g} {dest}", "input.out_of_range")
    if not signed and (x < 0 or (x == 0 and not zero)):
        invalid(param, "quantity must be nonnegative" if zero else "quantity must be positive")
    if kind == "temperature" and x < lower:
        invalid(param, "absolute temperature must be above zero kelvin", "input.out_of_range")
    return x


def records(text, param, cap=2000, required=True):
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        invalid(param, f"record text cap is {MAX_TEXT} characters", "input.out_of_range")
    if not text.strip():
        if required:
            invalid(param, "records are required", "input.missing")
        return []
    parts = text.replace("\r\n", "\n").replace("\n", ";").split(";")
    if len(parts) > cap:
        invalid(param, f"record cap is {cap}", "input.out_of_range")
    rows = []
    for i, part in enumerate(parts):
        if not part.strip() or len(part) > 600:
            invalid(f"{param}/{i}", "empty or overlong record; cap 600 characters")
        rows.append([s.strip() for s in part.split(",")])
    return rows


def fields(row, counts, param):
    if len(row) not in counts or any(not x for x in row):
        invalid(param, f"expected {counts} nonempty fields")


def json_numbers(text, param, cap=1000000):
    if not isinstance(text, str) or len(text) > 24000000:
        invalid(param, "JSON text cap is 24,000,000 characters", "input.out_of_range")
    # Flat lists only. Reject nesting before the JSON parser sees it.
    stripped = text.strip()
    if not stripped.startswith("[") or not stripped.endswith("]") or any(c in stripped[1:-1] for c in "[]{}\""):
        invalid(param, "expected a flat JSON number list")
    if stripped.count(",") >= cap:
        invalid(param, f"point cap is {cap}", "input.out_of_range")
    try:
        values = jsonsafe.loads(stripped, parse_float=lambda s: literal(s, param), parse_constant=lambda s: invalid(param, "nonfinite JSON", "input.out_of_range"))
    except ToolFailure:
        raise
    except (ValueError, RecursionError):
        invalid(param, "invalid JSON number list")
    if not isinstance(values, list) or len(values) > cap:
        invalid(param, f"point cap is {cap}", "input.out_of_range")
    return [number(x, f"{param}/{i}", -1e100, 1e100) for i, x in enumerate(values)]


def finite(obj, path="result"):
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (not math.isfinite(obj) or abs(obj) > 1e100):
        invalid(path, "calculated result exceeds supported finite range", "input.out_of_range")
    if isinstance(obj, dict):
        return {k: finite(v, f"{path}/{k}") for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [finite(v, f"{path}/{i}") for i, v in enumerate(obj)]
    return obj


def positive_exp(log_value, param):
    if not -230 <= log_value <= 230:
        invalid(param, "calculated nonzero result exceeds 1e-100..1e100", "input.out_of_range")
    return math.exp(log_value)


def root(fn, lo, hi, param, *, rtol=2e-11, max_iter=160):
    """Bisection on a caller-chosen linear or log coordinate; never return an unverified iterate."""
    flo, fhi = fn(lo), fn(hi)
    if not math.isfinite(flo) or not math.isfinite(fhi):
        invalid(param, "nonfinite root bracket", "input.out_of_range")
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        invalid(param, "root is not bracketed within the stated validity range", "input.out_of_range")
    for _ in range(max_iter):
        mid = lo + (hi - lo) / 2
        fm = fn(mid)
        if not math.isfinite(fm):
            invalid(param, "nonfinite root residual", "input.out_of_range")
        if fm == 0 or hi - lo <= rtol * max(abs(mid), 1e-12):
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    invalid(param, "root solver did not converge", "numerical.no_convergence")


def regression(x, y, param):
    """Centered least squares; scatter is residual in log10(response)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 2 or np.ptp(x) < 1e-8:
        invalid(param, "at least two distinct independent values are required")
    dx, dy = x - x.mean(), y - y.mean()
    slope = float(np.dot(dx, dy) / np.dot(dx, dx))
    intercept = float(y.mean() - slope * x.mean())
    residual = dy - slope * dx
    ss = float(np.dot(residual, residual))
    total = float(np.dot(dy, dy))
    se = math.sqrt(ss / (len(x) - 2)) if len(x) > 2 else None
    return {"slope": slope, "intercept": intercept, "r_squared": 1 - ss / total if total > 0 else None,
            "standard_error_log10": se, "scatter_factor_2se": 10 ** (2 * se) if se is not None else None,
            "scatter_definition": "multiplicative +/-2 residual standard errors; descriptive, not a confidence or tolerance bound",
            "sample_count": len(x)}


def csv_text(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(finite(rows))
    return stream.getvalue()


class Result:
    def __init__(self, formulas, assumptions, validity):
        self.values = {}
        self.data = {"formulas": formulas, "assumptions": assumptions, "validity": validity}
        self.warnings = []
        self.artifacts = []

    def add(self, name, value, unit="dimensionless"):
        self.values[name] = QuantityOut(quantity=finite(float(value)), unit=unit)

    def done(self, primary):
        return Computed(values=self.values, primary=primary, data=finite(self.data), warnings=self.warnings, artifacts=self.artifacts)


def run(name, inputs, compute):
    def guarded(handle):
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                return compute(handle)
        except ToolFailure as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, details) from None
        except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
            invalid("inputs", f"unsupported numerical range: {exc}", "input.out_of_range")
    return run_tool(tool_name="fatigue_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed textbook fatigue estimate. Verify material data, units and applicability; not a component certification.",))
