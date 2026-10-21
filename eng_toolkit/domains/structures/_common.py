"""Strict quantity/record boundaries and finite results; calculations use SI."""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.structures._server import ENGINE
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

System = Annotated[str, Field(description="Output units: metric (m, N, Pa) or imperial (inch, lbf, psi). CSV columns explicitly name their units.")]
Nodes2 = Annotated[str, Field(description="Required planar nodes 'id,x,y' with lengths, e.g. 'a,0 m,0 m;b,2 m,0 m'; semicolon/newline separators, <=100 nodes.")]
FrameMembers = Annotated[str, Field(description="Required 'id,start,end,E,A,I[,release]', e.g. 'e,a,b,200 GPa,1000 mm^2,1e6 mm^4'; release none/start/end/both, <=200.")]
FrameSupports = Annotated[str, Field(description="Joint restraints or springs, e.g. 'a,fixed;b,roller_y'; '' omits. Types fixed/pinned/roller_x/roller_y/spring; see docs for units.")]
NodalLoads = Annotated[str, Field(description="Joint forces and CCW moment 'node,Fx,Fy,Mz', e.g. 'b,0 N,-1 kN,0 N*m'; '' omits, <=400 records; repeated loads add.")]
MemberLoads = Annotated[str, Field(description="Member uniform/linear/point loads in local/global axes, e.g. 'e,uniform,local,0 N/m,-1 kN/m'; '' omits; see docs for grammar.")]

# SI units and plausible upper bounds; small nonzero inputs below the lower bound are refused.
SPECS = {
    "length": ("length", "m", 1e-9, 1e4),
    "area": ("area", "m^2", 1e-14, 1e4),
    "inertia": ("[length]^4", "m^4", 1e-24, 1e8),
    "stress": ("pressure", "Pa", 1e-6, 1e13),
    "force": ("force", "N", 1e-12, 1e12),
    "moment": ("torque", "N*m", 1e-12, 1e15),
    "line_load": ("[mass]/[time]^2", "N/m", 1e-12, 1e12),
    "spring": ("[mass]/[time]^2", "N/m", 1e-9, 1e16),
    "rotary_spring": ("torque", "N*m/rad", 1e-9, 1e18),
    "density": ("density", "kg/m^3", 1e-9, 1e6),
    "mass_length": ("[mass]/[length]", "kg/m", 1e-12, 1e9),
    "mass": ("[mass]", "kg", 1e-12, 1e12),
    "mass_inertia": ("[mass]*[length]^2", "kg*m^2", 1e-18, 1e16),
    "angle": ("dimensionless", "rad", 1e-14, 2*math.pi),
    "strain": ("dimensionless", "dimensionless", 1e-15, 1),
    "temperature": ("temperature", "K", 1e-12, 1e4),
    "expansion": ("1/[temperature]", "1/K", 1e-15, .01),
    "curvature": ("1/[length]", "1/m", 1e-15, 1e3),
    "thermal_gradient": ("[temperature]/[length]", "K/m", 1e-12, 1e8),
}
OUTPUTS = {
    "length": ("m", "inch"), "area": ("m^2", "inch^2"),
    "inertia": ("m^4", "inch^4"), "modulus": ("m^3", "inch^3"),
    "force": ("N", "lbf"), "moment": ("N*m", "lbf*inch"),
    "stress": ("Pa", "psi"), "stress2": ("Pa^2", "psi^2"),
    "stress3": ("Pa^3", "psi^3"), "line_load": ("N/m", "lbf/inch"),
    "angle": ("rad", "deg"), "frequency": ("Hz", "Hz"),
    "omega": ("rad/s", "rad/s"), "number": ("dimensionless", "dimensionless"),
}
MAX_TEXT = 60000
# Bounded tokens and a single numeric prefix avoid expression evaluation and nested syntax.
_QUANTITY = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)", re.ASCII)
_UNIT_FACTOR = re.compile(r"[A-Za-zµμ°%_]+(?:\^[+-]?[0-9]{1,2})?", re.ASCII)
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}", re.ASCII)


def invalid(param: str, message: str, family: str = "input.invalid") -> None:
    raise UnitsError(family, message, {"param": "/" + param.lstrip("/")})


def choice(raw: str, param: str, choices: tuple[str, ...]) -> str:
    if raw not in choices:
        invalid(param, f"expected one of {choices}")
    return raw


def number(raw: Any, param: str, lo: float, hi: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        invalid(param, "expected a finite dimensionless number")
    x = float(raw)
    if not math.isfinite(x) or not lo <= x <= hi:
        invalid(param, f"supported range is {lo:g} .. {hi:g}", "input.out_of_range")
    return x


def count(raw: Any, param: str, lo: int, hi: int) -> int:
    x = number(raw, param, lo, hi)
    if int(x) != x:
        invalid(param, "expected a whole number")
    return int(x)


def q(raw: str, param: str, kind: str, *, signed: bool = False, zero: bool = False, optional: bool = False) -> float | None:
    if optional and raw == "":
        return None
    if not isinstance(raw, str):
        invalid(param, "physical quantities require a string with units", "units.bare_number")
    if not raw.strip():
        invalid(param, "required quantity is missing", "input.missing")
    if len(raw) > 120:
        invalid(param, "quantity tokens are limited to 120 characters", "input.out_of_range")
    match = _QUANTITY.fullmatch(raw.strip())
    if not match:
        invalid(param, "expected a finite numeric literal followed by units", "units.parse_error")
    magnitude, unit_text = match.groups()
    if not math.isfinite(float(magnitude)) or abs(float(magnitude)) > 1e100:
        invalid(param, "numeric magnitude exceeds the supported range", "input.out_of_range")
    if float(magnitude) == 0 and any(c in "123456789" for c in magnitude.lower().split("e")[0]):
        invalid(param, "nonzero numeric literal underflows double precision", "input.out_of_range")
    if not unit_text:
        invalid(param, "every physical quantity needs units", "units.bare_number")
    if len(unit_text) > 64:
        invalid(param, "unit syntax permits unit products, quotients and numeric powers only", "units.parse_error")
    unit_literal = unit_text.replace("**", "^")
    # Whitespace between named units means multiplication; spaces around operators do not.
    unit_literal = re.sub(r"\s*([*/^])\s*", r"\1", unit_literal)
    unit_literal = "*".join(unit_literal.split())
    factors = re.split(r"[*/]", unit_literal)
    for index, factor in enumerate(factors):
        if not _UNIT_FACTOR.fullmatch(factor) and not (index == 0 and factor == "1" and "/" in unit_literal):
            invalid(param, "only named units with integer powers -99..99 are accepted; no arithmetic or function calls", "units.parse_error")
    dim, unit, lower, upper = SPECS[kind]
    if kind in ("temperature", "thermal_gradient", "expansion"):
        # Pint changes offset units in products to delta units. Inspect each
        # parsed factor before that rewrite, including aliases and powers.
        for factor in factors:
            if factor == "1":
                continue
            probe = parse_quantity({"value": 0, "unit": factor.split("^")[0]}, name=param)
            if probe.check("[temperature]") and probe.to("K").magnitude != 0:
                invalid(param, "temperature differences require K, delta_degC or delta_degF; use these units in gradients and expansion coefficients too")
    qty = parse_quantity(magnitude + " " + unit_literal, name=param, dimension=dim)
    x = float(qty.to(unit).magnitude)
    if kind == "angle" and not any(s in str(qty.units) for s in ("radian", "degree", "revolution")):
        invalid(param, "angles require deg or rad", "units.dimension_mismatch")
    if not math.isfinite(x) or abs(x) > upper or (float(magnitude) != 0 and abs(x) < lower):
        invalid(param, f"supported nonzero magnitudes are {lower:g} .. {upper:g} {unit}", "input.out_of_range")
    if not signed and (x < 0 or (x == 0 and not zero)):
        invalid(param, "quantity must be nonnegative" if zero else "quantity must be positive")
    return x


def records(text: str, param: str, cap: int, *, required: bool = False) -> list[list[str]]:
    if not isinstance(text, str):
        invalid(param, "expected record text")
    if len(text) > MAX_TEXT:
        invalid(param, f"text cap is {MAX_TEXT} characters", "input.out_of_range")
    if not text.strip():
        if required:
            invalid(param, "at least one record is required", "input.missing")
        return []
    parts = text.replace("\r\n", "\n").replace("\n", ";").split(";")
    if len(parts) > cap:
        invalid(param, f"record cap is {cap}", "input.out_of_range")
    result = []
    for i, part in enumerate(parts):
        if len(part) > 1000 or not part.strip():
            invalid(f"{param}/{i}", "empty or overlong record (maximum 1000 characters)")
        result.append([v.strip() for v in part.split(",")])
    return result


def fields(row: list[str], allowed: tuple[int, ...], path: str) -> None:
    if len(row) not in allowed or any(not x for x in row):
        invalid(path, f"expected {allowed} nonempty comma-separated fields")


def identifier(raw: str, path: str) -> str:
    if not _ID.fullmatch(raw):
        invalid(path, "ids use 1..32 ASCII letters, digits, underscores or hyphens")
    return raw


def finite(obj: Any, path: str = "result") -> Any:
    if isinstance(obj, np.ndarray):
        obj = obj.tolist()
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (not math.isfinite(obj) or abs(obj) > 1e100):
        invalid(path, "result is outside the finite supported range", "input.out_of_range")
    if isinstance(obj, dict):
        return {k: finite(v, f"{path}/{k}") for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v, f"{path}/{i}") for i, v in enumerate(obj)]
    return obj


def csv_text(header: list[str], rows: list[list]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(finite(rows))
    return stream.getvalue()


class Result:
    def __init__(self, system: str, formulas: dict[str, str], assumptions: str, validity: str):
        choice(system, "unit_system", ("metric", "imperial"))
        self.imperial = system == "imperial"
        self.values = {}
        self.data = {"formulas": formulas, "assumptions": assumptions, "validity": validity}
        self.warnings = []
        self.artifacts = []

    def quantity(self, value: float, kind: str = "number") -> dict:
        source, target = OUTPUTS[kind]
        converted = ureg.Quantity(finite(float(value)), source).to(target if self.imperial else source)
        return {"quantity": finite(float(converted.magnitude)), "unit": str(converted.units)}

    def add(self, name: str, value: float, kind: str = "number") -> None:
        self.values[name] = QuantityOut(**self.quantity(value, kind))

    def safety(self, name: str, strength: float | None, demand: float) -> None:
        if strength is not None:
            if demand > 0:
                self.add(name, strength/demand)
            else:
                self.data[name] = None
                self.data[name + "_reason"] = "Zero demand; unbounded factor omitted."

    def done(self, primary: str) -> Computed:
        return Computed(values=self.values, primary=primary, data=finite(self.data), warnings=self.warnings, artifacts=self.artifacts)


def run(name: str, inputs: dict, compute):
    def guarded(handle):
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                return compute(handle)
        except UnitsError as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, details) from None
        except (ArithmeticError, np.linalg.LinAlgError, ValueError) as exc:
            invalid("inputs", f"numerical range or conditioning is unsupported: {exc}", "input.out_of_range")
    return run_tool(tool_name="structures_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed textbook structural estimate. Verify units, assumptions, stability and applicability; not a design-code check.",))
