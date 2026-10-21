"""Bounded unit inputs and finite outputs. Internally use N, mm, MPa, seconds."""

from __future__ import annotations

import math
from typing import Annotated, Any, Callable

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.machine._server import ENGINE
from eng_toolkit.runner import Computed, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, ureg

Q = Annotated[str, Field(description="Physical quantity with unit, e.g. '25 mm', '100 N*m', '200 MPa'; optional inputs use ''.")]
Factor = Annotated[float, Field(description="Finite dimensionless coefficient; zero selects the documented automatic value where supported.")]
Count = Annotated[int, Field(description="Positive whole-number count, at most 100000.")]
System = Annotated[str, Field(description="Output units: 'metric' (mm, N, MPa) or 'imperial' (inch, lbf, ksi).")]

# dimension, working unit, absolute upper bound. Nonzero positive inputs also have a lower bound.
SPECS = {
    "length": ("length", "mm", 1e7), "force": ("force", "N", 1e12),
    "moment": ("torque", "N*mm", 1e15), "stress": ("pressure", "MPa", 1e7),
    "power": ("power", "W", 1e12), "speed": ("velocity", "m/s", 1e5),
    "mass_length": ("[mass]/[length]", "kg/m", 1e6),
    "angle": ("dimensionless", "rad", 2 * math.pi),
    "pitch": ("1/[length]", "1/mm", 1e6),
}
OUTPUTS = {
    "length": ("mm", "inch"), "area": ("mm**2", "inch**2"),
    "line_inertia": ("mm**3", "inch**3"), "inertia": ("mm**4", "inch**4"),
    "force": ("N", "lbf"), "moment": ("N*mm", "lbf*ft"),
    "stress": ("MPa", "ksi"), "power": ("W", "hp"), "speed": ("m/s", "ft/s"),
    "angle": ("rad", "deg"), "angular_speed": ("rad/s", "rpm"),
    "number": ("dimensionless", "dimensionless"),
}


def invalid(param: str, message: str, family: str = "input.invalid") -> None:
    raise UnitsError(family, message, {"param": "/" + param.lstrip("/")})


def number(value: Any, name: str, lo: float = 0, hi: float = 100, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        invalid(name, f"{name} must be a dimensionless number")
    x = float(value)
    if not math.isfinite(x) or not lo <= x <= hi or (positive and x == 0):
        invalid(name, f"{name} must be {'>' if positive else '>='} {lo} and <= {hi}", "input.out_of_range" if abs(x) > hi else "input.invalid")
    return x


def count(value: Any, name: str, lo: int = 1, hi: int = 100000) -> int:
    x = number(value, name, lo, hi)
    if x != int(x):
        invalid(name, f"{name} must be a whole number")
    return int(x)


def choice(value: str, name: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        invalid(name, f"{name} must be one of {allowed}")
    return value


def q(raw: str, name: str, kind: str, *, optional: bool = False, signed: bool = False, zero: bool = False) -> float | None:
    if optional and raw == "":
        return None
    dim, unit, limit = SPECS[kind]
    qty = parse_quantity(raw, name=name, dimension=dim)
    x = float(qty.to(unit).magnitude)
    if kind == "angle" and not any(s in str(qty.units) for s in ("radian", "degree", "revolution")):
        invalid(name, "angles require an angular unit such as deg or rad", "units.dimension_mismatch")
    if not math.isfinite(x) or abs(x) > limit or (0 < abs(x) < 1e-9):
        invalid(name, f"{name} is outside the supported magnitude range 1e-9 .. {limit:g} {unit}", "input.out_of_range")
    if (not signed and x < 0) or (x == 0 and not (zero or signed)):
        invalid(name, f"{name} must be {'nonnegative' if zero else 'positive'}")
    return x


def rpm(raw: str, name: str = "speed") -> float:
    qty = parse_quantity(raw, name=name, dimension="1/[time]")
    if any(s in str(qty.units) for s in ("revolution", "radian", "degree", "turn", "arcminute", "arcsecond", "grade")):
        omega = float(qty.to("rad/s").magnitude)
    else:
        omega = float(qty.to("1/s").magnitude) * 2 * math.pi
    if not 1e-9 <= omega <= 1e7:
        invalid(name, "rotational speed must be positive and <= 1e7 rad/s", "input.out_of_range")
    return omega


def paired(a: Any, b: Any, name: str) -> None:
    if (a is None) != (b is None):
        invalid(name, "these inputs must be supplied together")


def finite(obj: Any, path: str = "/result") -> None:
    if isinstance(obj, float) and (not math.isfinite(obj) or abs(obj) > 1e100):
        invalid(path, "result exceeds the finite supported range", "input.out_of_range")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            finite(v, path + "/" + k)
    elif isinstance(obj, (tuple, list)):
        for i, v in enumerate(obj):
            finite(v, path + "/" + str(i))


def safe_details(obj: Any) -> Any:
    """Preserve diagnostic input values without nonfinite JSON numbers, including nested JSON quantities."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, dict):
        return {key: safe_details(value) for key, value in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [safe_details(value) for value in obj]
    return obj


class Result:
    def __init__(self, system: str, formulas: dict[str, str], assumptions: str, validity: str):
        choice(system, "unit_system", ("metric", "imperial"))
        self.imperial = system == "imperial"
        self.values: dict[str, QuantityOut] = {}
        self.warnings: list[str] = []
        self.data: dict[str, Any] = {"formulas": formulas, "assumptions": assumptions, "validity": validity}

    def add(self, name: str, value: float, kind: str = "number") -> None:
        finite(float(value), "/" + name)
        source, target = OUTPUTS[kind]
        converted = ureg.Quantity(value, source).to(target if self.imperial else source)
        self.values[name] = QuantityOut(quantity=float(converted.magnitude), unit=str(converted.units))

    def safety(self, name: str, strength: float | None, stress: float) -> None:
        if strength is not None:
            if stress > 0:
                self.add(name, strength / stress)
            else:
                self.data[name] = None
                self.data[name + "_reason"] = "zero demand; unbounded safety factor omitted"

    def done(self, primary: str) -> Computed:
        finite(self.data)
        finite({k: v.quantity for k, v in self.values.items()})
        return Computed(values=self.values, primary=primary, warnings=self.warnings, data=self.data)


def run(name: str, inputs: dict, compute: Callable[[], Computed]) -> ToolResult:
    def guarded(_):
        try:
            return compute()
        except UnitsError as exc:
            # Shared units errors use plain parameter names. This domain exposes JSON pointers.
            details = dict(exc.details)
            details["param"] = "/" + str(details.get("param", "inputs")).lstrip("/")
            raise UnitsError(exc.family, exc.message, safe_details(details)) from exc
        except (OverflowError, ZeroDivisionError, ValueError) as exc:
            invalid("inputs", f"unsupported formula range: {exc}", "input.out_of_range")
    return run_tool(tool_name="machine_" + name, engine=ENGINE, version=__version__, inputs=inputs,
                    compute=guarded, engines=("pint",), standing_warnings=("Unreviewed textbook estimate; see assumptions and validity in data.",))
