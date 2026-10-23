"""Numeric boundaries shared by the geometry entry points."""

import math

from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import parse_quantity, require_positive, ureg

MAX_DENSITY_KG_M3 = 1e6
MAX_MASS_KG = 1e18


def out_of_range(param: str) -> ToolFailure:
    return ToolFailure("input.out_of_range", f"{param}: value is outside the supported numeric range", {"param": f"/{param}"})


def output_scale(raw: str):
    try:
        unit = parse_quantity(f"1 {raw}", name="output_unit", dimension="length").units
        scale = float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)
        # Leaves ample range for bounded model coordinates and their cubes.
        if not math.isfinite(scale) or not 1e-12 <= scale <= 1e12:
            raise out_of_range("output_unit")
        return unit, str(unit), scale
    except ArithmeticError:
        raise out_of_range("output_unit") from None


def density_kg_m3(raw: str) -> float | None:
    if not raw.strip():
        return None
    try:
        quantity = require_positive(parse_quantity(raw, name="density", dimension="density"), name="density")
        density = float(quantity.to("kilogram / meter ** 3").magnitude)
    except ArithmeticError:
        raise out_of_range("density") from None
    if not math.isfinite(density) or not 0 < density <= MAX_DENSITY_KG_M3:
        raise out_of_range("density")
    return density


def mass_kg(density: float, volume_mm3: float) -> float:
    mass = density * (volume_mm3 * 1e-9)
    if not math.isfinite(mass) or not 0 <= mass <= MAX_MASS_KG:
        raise out_of_range("density")
    return float(f"{mass:.12g}")
