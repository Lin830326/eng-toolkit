"""Strict unit/record boundaries and bounded scalar numerical methods."""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg
from ._server import ENGINE

G = 9.80665
Density = Annotated[str, Field(description="Fluid mass density, e.g. '1000 kg/m^3'; all modes; default is a nominal water input, not a property correlation.")]
Viscosity = Annotated[str, Field(description="Fluid dynamic viscosity, e.g. '1 mPa*s'; all modes; '' requires kinematic_viscosity instead.")]
Kinematic = Annotated[str, Field(description="Fluid kinematic viscosity, e.g. '1 cSt'; '' omits; supply only when dynamic_viscosity is ''.")]

# dimension, SI unit, minimum nonzero absolute value, maximum absolute value
SPECS = {
    "length": ("length", "m", 1e-9, 1e6),
    "area": ("area", "m^2", 1e-18, 1e8),
    "flow": ("volumetric_flow", "m^3/s", 1e-15, 1e6),
    "pressure": ("pressure", "Pa", 1e-9, 1e12),
    "modulus": ("pressure", "Pa", 1e3, 1e13),
    "density": ("density", "kg/m^3", 1e-6, 1e5),
    "viscosity": ("dynamic_viscosity", "Pa*s", 1e-12, 1e5),
    "kinematic": ("kinematic_viscosity", "m^2/s", 1e-15, 1e5),
    "velocity": ("velocity", "m/s", 1e-12, 1e5),
    "time": ("time", "s", 1e-9, 1e9),
    "temperature": ("temperature", "K", 1e-6, 1e5),
    "gas_constant": ("[length]^2/[time]^2/[temperature]", "J/kg/K", 1e-3, 1e5),
    "a": ("[time]/[length]^2", "s/m^2", 1e-12, 1e15),
    "b": ("[time]^2/[length]^5", "s^2/m^5", 1e-12, 1e20),
    "frequency": ("1/[time]", "1/s", 1e-9, 1e6),
    "angle": ("dimensionless", "rad", 1e-30, math.pi),
}
_LITERAL = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)", re.ASCII)
_UNIT = re.compile(r"[A-Za-zµμ°%_]+(?:\^[+-]?[0-9]{1,2})?", re.ASCII)
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}", re.ASCII)


def invalid(param, message, family="input.invalid"):
    raise UnitsError(family, message, {"param": "/" + param.lstrip("/")})


def choice(raw, name, choices):
    if raw not in choices:
        invalid(name, f"expected one of {choices}")
    return raw


def number(raw, name, lo=0., hi=1e6):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        invalid(name, "expected a finite dimensionless number")
    x = float(raw)
    if not math.isfinite(x) or not lo <= x <= hi:
        invalid(name, f"supported range is {lo:g} .. {hi:g}", "input.out_of_range")
    return x


def count(raw, name, lo, hi):
    x = number(raw, name, lo, hi)
    if x != int(x):
        invalid(name, "expected an integer count")
    return int(x)


def scalar(raw, name, lo=0., hi=1e6):
    """A dimensionless literal in a record, never an expression."""
    if len(raw) > 40 or not _LITERAL.fullmatch(raw) or _LITERAL.fullmatch(raw).group(2):
        invalid(name, "expected a dimensionless numeric literal")
    x = float(raw)
    if x == 0 and any(c in "123456789" for c in raw.lower().split("e")[0]):
        invalid(name, "nonzero literal underflows", "input.out_of_range")
    return number(x, name, lo, hi)


def q(raw, name, kind, *, zero=False, signed=False, optional=False, absolute_temperature=False):
    if optional and raw == "":
        return None
    if not isinstance(raw, str):
        invalid(name, "physical quantities require strings with units", "units.bare_number")
    if not raw.strip():
        invalid(name, "required quantity is missing", "input.missing")
    if len(raw) > 120:
        invalid(name, "quantity token cap is 120 characters", "input.out_of_range")
    match = _LITERAL.fullmatch(raw.strip())
    if not match:
        invalid(name, "expected a finite numeric literal with units", "units.parse_error")
    literal, units = match.groups()
    value = float(literal)
    if not math.isfinite(value) or abs(value) > 1e100 or (value == 0 and any(c in "123456789" for c in literal.lower().split("e")[0])):
        invalid(name, "numeric literal exceeds supported magnitudes", "input.out_of_range")
    if not units:
        invalid(name, "physical quantities need units", "units.bare_number")
    units = re.sub(r"\s*([*/^])\s*", r"\1", units.replace("**", "^"))
    units = "*".join(units.split())
    # Local input abbreviation; do not modify the shared Pint registry.
    if units == "gpm":
        units = "gallon/minute"
    if len(units) > 64:
        invalid(name, "unit syntax cap is 64 characters", "units.parse_error")
    factors = re.split(r"[*/]", units)
    if any(not _UNIT.fullmatch(f) and not (i == 0 and f == "1" and "/" in units) for i, f in enumerate(factors)):
        invalid(name, "only named units, products, quotients and integer powers are permitted", "units.parse_error")
    dim, unit, lower, upper = SPECS[kind]
    qty = parse_quantity({"value": value, "unit": units}, name=name, dimension=dim)
    constituents = set(qty.units._units)
    # Pint rewrites offset factors in composites to intervals, and cancels ratios.
    # Parse each grammar-approved token as well, before either transformation.
    for factor in factors:
        if factor != "1":
            probe = parse_quantity({"value": 0, "unit": factor.split("^")[0]}, name=name)
            constituents.update(probe.units._units)
    plain_temperature = absolute_temperature and len(factors) == 1 and "^" not in units
    for constituent in constituents:
        parsed_unit = ureg.Unit(constituent)
        if (parsed_unit.is_compatible_with("K")
                and ureg.Quantity(0, parsed_unit).to("K").magnitude != 0
                and not plain_temperature):
            invalid(name, "offset temperature units are allowed only as a plain absolute temperature")
    x = float(qty.to(unit).magnitude)
    if kind == "angle" and not any(s in str(qty.units) for s in ("radian", "degree", "revolution")):
        invalid(name, "angles require deg or rad", "units.dimension_mismatch")
    if not math.isfinite(x) or abs(x) > upper or (value != 0 and abs(x) < lower):
        invalid(name, f"nonzero SI magnitude must be {lower:g} .. {upper:g} {unit}", "input.out_of_range")
    if not signed and (x < 0 or (x == 0 and not zero)):
        invalid(name, "quantity must be nonnegative" if zero else "quantity must be positive")
    return x


def fluid(density, dynamic_viscosity, kinematic_viscosity):
    rho = q(density, "density", "density")
    if bool(dynamic_viscosity) == bool(kinematic_viscosity):
        invalid("dynamic_viscosity", "supply exactly one of dynamic_viscosity and kinematic_viscosity")
    mu = q(dynamic_viscosity, "dynamic_viscosity", "viscosity") if dynamic_viscosity else rho*q(kinematic_viscosity, "kinematic_viscosity", "kinematic")
    if not 1e-12 <= mu <= 1e5:
        invalid("kinematic_viscosity", "derived dynamic viscosity exceeds 1e-12 .. 1e5 Pa*s", "input.out_of_range")
    return rho, mu


def records(text, name, cap, *, required=False):
    if not isinstance(text, str):
        invalid(name, "expected record text")
    if len(text) > 60000:
        invalid(name, "text cap is 60000 characters", "input.out_of_range")
    if not text.strip():
        if required:
            invalid(name, "at least one record is required", "input.missing")
        return []
    rows = text.replace("\r\n", "\n").replace("\n", ";").split(";")
    if len(rows) > cap:
        invalid(name, f"record cap is {cap}", "input.out_of_range")
    out = []
    for i, row in enumerate(rows):
        if len(row) > 1000:
            invalid(f"{name}/{i}", "record cap is 1000 characters", "input.out_of_range")
        parts = [x.strip() for x in row.split(",")]
        if any(not x for x in parts):
            invalid(f"{name}/{i}", "empty fields or records are not permitted")
        out.append(parts)
    return out


def fields(row, n, path):
    if len(row) not in n:
        invalid(path, f"expected field count in {n}")


def identifier(raw, path):
    if not _ID.fullmatch(raw):
        invalid(path, "ids must be 1..32 ASCII letters, digits, underscores or hyphens")
    return raw


def bisect(fn, lo, hi, name, *, rtol=2e-12, atol=1e-14):
    """Bisection with geometric midpoints for wide positive brackets; 160 steps."""
    flo, fhi = fn(lo), fn(hi)
    if not math.isfinite(flo) or not math.isfinite(fhi):
        invalid(name, "nonfinite root bracket", "input.out_of_range")
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        invalid(name, "no root in the supported bracket")
    for _ in range(160):
        mid = math.sqrt(lo)*math.sqrt(hi) if lo > 0 and hi/lo > 100 else lo+(hi-lo)/2
        fm = fn(mid)
        if not math.isfinite(fm):
            invalid(name, "nonfinite root residual", "input.out_of_range")
        if fm == 0 or hi-lo <= atol+rtol*max(abs(lo), abs(hi)):
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    invalid(name, "root did not converge within 160 iterations", "input.nonconvergence")


def maximum(fn, lo, hi, name):
    """Golden-section search of a documented unimodal function."""
    ratio = (math.sqrt(5)-1)/2
    c, d = hi-ratio*(hi-lo), lo+ratio*(hi-lo)
    fc, fd = fn(c), fn(d)
    for _ in range(120):
        if hi-lo < 2e-13*max(1., abs(lo), abs(hi)):
            return (lo+hi)/2
        if fc > fd:
            hi, d, fd = d, c, fc
            c = hi-ratio*(hi-lo)
            fc = fn(c)
        else:
            lo, c, fc = c, d, fd
            d = lo+ratio*(hi-lo)
            fd = fn(d)
    invalid(name, "maximum search did not converge", "input.nonconvergence")


def friction(re, relative_roughness):
    """Darcy factor: laminar, smoothstep blend, Haaland-seeded Colebrook."""
    if re <= 0 or re > 1e12 or not 0 <= relative_roughness <= .05:
        invalid("pipes", "Darcy validity requires 0<Re<=1e12 and 0<=roughness/D<=0.05", "input.out_of_range")
    if re < 2000:
        return 64/re
    rt = max(re, 2000.)
    x = -1.8*math.log10((relative_roughness/3.7)**1.11+6.9/rt)
    for _ in range(60):
        term = relative_roughness/3.7+2.51*x/rt
        f = x+2*math.log10(term)
        step = f/(1+2/math.log(10)*2.51/rt/term)
        x -= step
        if abs(step) < 1e-12*abs(x) and abs(f) < 1e-10:
            turbulent = 1/x**2
            if re >= 4000:
                return turbulent
            t = (re-2000)/2000
            w = t*t*(3-2*t)
            return (1-w)*64/re+w*turbulent
    invalid("pipes", "Colebrook did not converge within 60 iterations", "input.nonconvergence")


def pipe_loss(flow, length, diameter, roughness, minor, rho, mu):
    area = math.pi*diameter**2/4
    velocity = flow/area
    re = rho*abs(velocity)*diameter/mu
    if re == 0:
        return 0., 0., 0., None
    factor = friction(re, roughness/diameter)
    return (factor*length/diameter+minor)*velocity*abs(velocity)/(2*G), velocity, re, factor


def finite(obj, path="result"):
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (not math.isfinite(obj) or abs(obj) > 1e100):
        invalid(path, "result exceeds finite supported magnitudes", "input.out_of_range")
    if isinstance(obj, dict):
        return {k: finite(v, f"{path}/{k}") for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v, f"{path}/{i}") for i, v in enumerate(obj)]
    return obj


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
        self.values[name] = QuantityOut(quantity=finite(float(value), name), unit=unit)

    def done(self, primary):
        return Computed(values=self.values, primary=primary, data=finite(self.data), warnings=self.warnings, artifacts=self.artifacts)


def run(name, inputs, compute):
    def guarded(handle):
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                return compute(handle)
        except UnitsError as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, details) from None
        except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
            invalid("inputs", f"unsupported numerical range or conditioning: {exc}", "input.out_of_range")
    return run_tool(tool_name="fluids_"+name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed textbook fluid estimate. Verify properties, assumptions and applicability before engineering use.",))
