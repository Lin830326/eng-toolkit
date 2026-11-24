"""Strict quantity/record boundaries and finite results; calculations use SI."""

from __future__ import annotations

import csv
import io
import math
import re
from contextvars import ContextVar
from typing import Annotated, Any

import numpy as np
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut
from eng_toolkit.domains.structures3d._server import ENGINE
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

System = Annotated[str, Field(description="Output units: metric (m, N, Pa) or imperial (inch, lbf, psi). CSV columns explicitly name their units.")]
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
    "foundation": ("pressure", "N/m^2", 1e-9, 1e16),
    "rigidity": ("[mass]*[length]^3/[time]^2", "N*m^2", 1e-12, 1e25),
}
OUTPUTS = {
    "length": ("m", "inch"), "area": ("m^2", "inch^2"),
    "inertia": ("m^4", "inch^4"), "modulus": ("m^3", "inch^3"),
    "force": ("N", "lbf"), "moment": ("N*m", "lbf*inch"),
    "stress": ("Pa", "psi"), "stress2": ("Pa^2", "psi^2"),
    "stress3": ("Pa^3", "psi^3"), "line_load": ("N/m", "lbf/inch"),
    "angle": ("rad", "deg"), "frequency": ("Hz", "Hz"),
    "omega": ("rad/s", "rad/s"), "number": ("dimensionless", "dimensionless"),
    "plate_rigidity": ("N*m", "lbf*inch"),
    "plate_moment": ("N", "lbf"),
}
MAX_TEXT = 60000
# Bounded tokens and a single numeric prefix avoid expression evaluation and nested syntax.
_QUANTITY = re.compile(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)", re.ASCII)
_UNIT_FACTOR = re.compile(r"[A-Za-zµμ°%_]+(?:\^[+-]?[0-9]{1,2})?", re.ASCII)
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}", re.ASCII)


# ---------------------------------------------------------------- record grammar shown in every refusal
# A refusal inside a record names the record kind, the number of the record (and its line when the text has several lines), what was received
# (clipped) and the grammar with an example.  ``records`` remembers the rows of this call; ``invalid`` finds the row from the JSON pointer.
_TRACE: ContextVar[dict | None] = ContextVar("structures_record_trace", default=None)
GRAMMAR: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "*": {
        "nodes": ("id,x,y,z", ("a,0 m,0 m,0 m",)),
        "members": (
            "id,start,end,E,G,A,Iy,Iz,J[,roll | ,vx,vy,vz]",
            ("e,a,b,200 GPa,80 GPa,1e-3 m^2,1e-6 m^4,2e-6 m^4,1e-6 m^4",),
        ),
        "orientations": ("member,roll,angle | member,vector,vx,vy,vz", ("e,roll,30 deg", "e,vector,0,1,0")),
        "shear_properties": ("member,Asy,Asz", ("e,800 mm^2,600 mm^2",)),
        "releases": ("member,start|end,rx+ry+rz (any of the three, joined by +)", ("e,start,ry+rz",)),
        "supports": (
            "node,fixed | node,pinned | node,roller_x|roller_y|roller_z | node,ux+uy+uz+rx+ry+rz (any subset) | "
            "node,spring,axis,k[,rest] | node,settlement,axis,value",
            ("a,fixed", "a,ux+rz", "c,spring,ux,1 kN/m,0 m", "d,settlement,uy,-5 mm"),
        ),
        "loads": ("node,Fx,Fy,Fz,Mx,My,Mz (all six, every field with a unit)", ("b,1 kN,0 N,0 N,0 N*m,0 N*m,0 N*m",)),
        "member_loads": (
            "member,uniform,local|global,qx,qy,qz | member,point,local|global,a,Fx,Fy,Fz (a is the distance from the start node)",
            ("e,uniform,local,0 N/m,1 kN/m,0 N/m", "e,point,global,1 m,0 N,1 kN,0 N"),
        ),
        "thermal": ("member,alpha,dT", ("e,12e-6 1/K,20 K",)),
        "member_masses": ("member,mass_per_length", ("e,12 kg/m",)),
        "nodal_masses": ("node,m,J | node,mx,my,mz,Jx,Jy,Jz", ("b,10 kg,0 kg*m^2",)),
    },
    "foundation": {"point_loads": ("x,force,moment", ("5 m,20 kN,0 N*m",))},
    "plate": {"point_loads": ("x,y,P", ("0.4 m,0.6 m,100 N",))},
}


def _trace() -> dict:
    trace = _TRACE.get()
    if trace is None:
        trace = {"tool": None, "rows": {}}
        _TRACE.set(trace)
    return trace


def _clip(text: str, width: int = 80) -> str:
    text = re.sub(r"[^ -~]", "?", text)
    return text if len(text) <= width else text[: width - 3] + "..."


def record_note(path: str) -> str:
    """The context appended to a refusal at JSON pointer ``path``: record kind and number, what was received, the grammar and an example."""
    trace = _trace()
    segments = path.strip("/").split("/")
    for take in (1, 2):
        key = "/".join(segments[:take])
        if key in trace["rows"] and len(segments) > take and segments[take].isdigit() and int(segments[take]) < len(trace["rows"][key]):
            index = int(segments[take])
            raw, line = trace["rows"][key][index]
            break
    else:
        return ""
    where = f"{key} record {index + 1}" + (f" (line {line})" if line is not None else "")
    grammar = GRAMMAR.get(trace["tool"] or "", {}).get(segments[0]) or GRAMMAR["*"].get(segments[0])
    if grammar is None:
        return f" [{where}: received '{_clip(raw.strip())}']"
    forms, examples = grammar
    return f" [{where}: received '{_clip(raw.strip())}'; expected {forms}; e.g. " + " or ".join(f"'{e}'" for e in examples) + "]"


def invalid(param: str, message: str, family: str = "input.invalid") -> None:
    raise UnitsError(family, message + record_note(param), {"param": "/" + param.lstrip("/")})


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
    entries, current, line, start = [], [], 1, 1
    for ch in text.replace("\r\n", "\n") + ";":
        if ch in ";\n":
            entries.append(("".join(current), start))
            current, start = [], line + (ch == "\n")
        else:
            current.append(ch)
        line += ch == "\n"
    multiline = "\n" in text
    _trace()["rows"][param] = [(part, number if multiline else None) for part, number in entries]
    if len(entries) > cap:
        invalid(param, f"record cap is {cap}", "input.out_of_range")
    for i, (part, _) in enumerate(entries):
        if len(part) > 1000:
            invalid(f"{param}/{i}", "record exceeds 1000 characters", "input.out_of_range")
        if not part.strip():
            invalid(f"{param}/{i}", "empty record (blank line or doubled or trailing ';')")
    return [[v.strip() for v in part.split(",")] for part, _ in entries]


def fields(row: list[str], allowed: tuple[int, ...], path: str) -> None:
    if len(row) not in allowed:
        invalid(path, f"expected {' or '.join(map(str, allowed))} comma-separated fields but got {len(row)}")
    for k, value in enumerate(row):
        if not value:
            invalid(path, f"field {k + 1} is empty")


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
        _TRACE.set({"tool": name, "rows": {}})
        try:
            with np.errstate(over="raise", divide="raise", invalid="raise"):
                return compute(handle)
        except UnitsError as exc:
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, details) from None
        except (ArithmeticError, np.linalg.LinAlgError, ValueError) as exc:
            invalid("inputs", f"numerical range or conditioning is unsupported: {exc}", "input.out_of_range")
    return run_tool(tool_name="structures3d_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("numpy", "pint"),
                    standing_warnings=("Unreviewed textbook structural estimate. Verify units, assumptions, stability and applicability; not a design-code check.",))
