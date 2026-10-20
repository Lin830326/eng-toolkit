"""``thermal_fin``: straight rectangular and circular pin fins of uniform cross-section, and arrays on a base.

Formulas (textbook 1-D fin equation, theta = T - T_ambient, theta_b at the base; Incropera / Cengel)
* m = sqrt(h P / (k Ac)),  M = sqrt(h P k Ac) theta_b;  rectangular P = 2 (w + t), Ac = w t;  pin P = pi D, Ac = pi D^2 / 4
* infinitely long:           q = M
* adiabatic tip:             q = M tanh(m L)
* convective tip:            q = M (sinh mL + (h/mk) cosh mL) / (cosh mL + (h/mk) sinh mL)
* prescribed tip temperature: q = M (cosh mL - theta_L/theta_b) / sinh mL
* efficiency  eta_f = q / (h A_f theta_b)  (A_f = P L, plus Ac for the convective tip; adiabatic tip: tanh(mL)/(mL)),
  effectiveness  eps_f = q / (h Ac theta_b)
* corrected length: a convective tip behaves like an adiabatic tip on L_c = L + Ac/P (approximation, reported for comparison)
* array of N fins on a base with unfinned area A_b:  A_t = N A_f + A_b,  eta_o = 1 - (N A_f / A_t)(1 - eta_f),  q_t = eta_o h A_t theta_b
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.thermal._common import (
    DIM_H,
    DIM_K,
    QuantityIn,
    TemperatureIn,
    UnitSystemIn,
    check_data,
    choice,
    is_blank,
    out,
    plain,
    positive_quantity,
    temperature,
    whole_number,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

FIN_TYPES = ("rectangular", "pin")
TIPS = ("adiabatic", "convective", "prescribed_temperature", "infinite")


def _csch(x: float) -> float:
    return 2.0 * math.exp(-x) / -math.expm1(-2.0 * x)


def fin_heat_rate(tip: str, m: float, length: float, k: float, h: float, p: float, ac: float, theta_b: float, theta_tip: float | None = None) -> float:
    """Heat rate leaving the base into a uniform fin (positive for theta_b > 0); overflow-free for any mL."""
    big_m = math.sqrt(h * p * k * ac) * theta_b
    x = m * length
    if tip == "infinite":
        return big_m
    if tip == "adiabatic":
        return big_m * math.tanh(x)
    if tip == "convective":
        beta = h / (m * k)
        t = math.tanh(x)
        return big_m * (t + beta) / (1.0 + beta * t)
    assert theta_tip is not None
    return big_m * (1.0 / math.tanh(x) - (theta_tip / theta_b) * _csch(x))


@server.tool(
    name="fin",
    description=(
        "Straight rectangular fin or circular pin fin of uniform cross-section (1-D fin equation) with four tip conditions: "
        "adiabatic, convective, prescribed_temperature, infinite. Returns fin heat rate, fin efficiency, effectiveness, m, "
        "mL and the Biot check. With 'fin_count' and 'base_unfinned_area' it also gives the overall surface efficiency and the "
        "heat rate of an array of identical fins on a base. Temperatures are absolute (base and ambient); the sign of the "
        "heat rate is positive when the base is hotter."
    ),
)
def fin(
    fin_type: Annotated[str, Field(description="'rectangular' (straight, needs thickness and width) or 'pin' (round, needs diameter)")],
    length: Annotated[QuantityIn, Field(description="Fin length from base to tip, e.g. '30 mm'")],
    conductivity: Annotated[QuantityIn, Field(description="Fin material conductivity, e.g. '200 W/(m*K)'")],
    h: Annotated[QuantityIn, Field(description="Convection coefficient on the fin surface, e.g. '25 W/(m^2*K)'")],
    base_temperature: Annotated[TemperatureIn, Field(description="Temperature at the fin base, e.g. '100 degC'")],
    ambient_temperature: Annotated[TemperatureIn, Field(description="Fluid temperature, e.g. '25 degC' (must differ from the base)")],
    tip: Annotated[str, Field(description="'adiabatic' (default), 'convective', 'prescribed_temperature' or 'infinite' (infinitely long fin)")] = "adiabatic",
    thickness: Annotated[str, Field(description="Rectangular fin only: thickness, e.g. '3 mm'")] = "",
    width: Annotated[str, Field(description="Rectangular fin only: width (depth into the page), e.g. '100 mm'")] = "",
    diameter: Annotated[str, Field(description="Pin fin only: diameter, e.g. '5 mm'")] = "",
    tip_temperature: Annotated[str, Field(description="Only for tip='prescribed_temperature': the tip temperature, e.g. '60 degC'")] = "",
    fin_count: Annotated[int, Field(description="Optional array: number of identical fins on the base (0 = single fin only)")] = 0,
    base_unfinned_area: Annotated[str, Field(description="Array only: base area exposed between the fins, e.g. '0.002 m^2'")] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "fin_type": fin_type,
        "length": length,
        "conductivity": conductivity,
        "h": h,
        "base_temperature": base_temperature,
        "ambient_temperature": ambient_temperature,
        "tip": tip,
        "thickness": thickness,
        "width": width,
        "diameter": diameter,
        "tip_temperature": tip_temperature,
        "fin_count": fin_count,
        "base_unfinned_area": base_unfinned_area,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        kind = choice(fin_type, name="fin_type", allowed=FIN_TYPES)
        tip_kind = choice(tip, name="tip", allowed=TIPS)
        notes: list[str] = []
        warnings: list[str] = []
        big_l = positive_quantity(length, name="length", dimension="length", unit="m", lo=1e-4, hi=10.0)
        k = positive_quantity(conductivity, name="conductivity", dimension=DIM_K, unit="W/m/K", lo=1e-3, hi=1e4, notes=notes)
        h_ = positive_quantity(h, name="h", dimension=DIM_H, unit="W/m**2/K", lo=1e-2, hi=1e7, notes=notes)
        t_b = temperature(base_temperature, name="base_temperature")
        t_a = temperature(ambient_temperature, name="ambient_temperature")
        if t_b == t_a:
            raise UnitsError("input.invalid", "base and ambient temperatures are equal: no heat flows", {"param": "base_temperature|ambient_temperature"})
        theta_b = t_b - t_a
        if kind == "rectangular":
            if is_blank(thickness) or is_blank(width):
                raise UnitsError("input.missing", "a rectangular fin needs 'thickness' and 'width'", {"param": "thickness|width"})
            th = positive_quantity(thickness, name="thickness", dimension="length", unit="m", lo=1e-5, hi=0.1)
            w = positive_quantity(width, name="width", dimension="length", unit="m", lo=1e-4, hi=10.0)
            perimeter, ac = 2.0 * (w + th), w * th
            char = th / 2.0
            if not is_blank(diameter):
                warnings.append("'diameter' is not used for a rectangular fin and was ignored")
            if w < 5.0 * th:
                warnings.append(f"width {w:.4g} m is less than 5 x thickness: the 'straight fin' perimeter 2(w+t) includes the edges; a narrow fin is closer to a pin")
        else:
            if is_blank(diameter):
                raise UnitsError("input.missing", "a pin fin needs 'diameter'", {"param": "diameter"})
            d = positive_quantity(diameter, name="diameter", dimension="length", unit="m", lo=1e-5, hi=0.1)
            perimeter, ac = math.pi * d, math.pi * d * d / 4.0
            char = d / 4.0
            if not (is_blank(thickness) and is_blank(width)):
                warnings.append("'thickness' and 'width' are not used for a pin fin and were ignored")
        m = math.sqrt(h_ * perimeter / (k * ac))
        ml = m * big_l
        theta_tip = None
        if tip_kind == "prescribed_temperature":
            if is_blank(tip_temperature):
                raise UnitsError("input.missing", "tip='prescribed_temperature' needs 'tip_temperature'", {"param": "tip_temperature"})
            theta_tip = temperature(tip_temperature, name="tip_temperature") - t_a
            ratio = theta_tip / theta_b
            if not 0.0 <= ratio <= 1.0:
                warnings.append(f"the tip temperature is not between ambient and base (theta_L/theta_b = {ratio:.3g}): unusual for a passive fin")
        elif not is_blank(tip_temperature):
            warnings.append("'tip_temperature' is only used with tip='prescribed_temperature' and was ignored")

        q = fin_heat_rate(tip_kind, m, big_l, k, h_, perimeter, ac, theta_b, theta_tip)
        a_f = perimeter * big_l + (ac if tip_kind == "convective" else 0.0)
        q_max = h_ * a_f * theta_b
        eta = q / q_max
        eps_f = q / (h_ * ac * theta_b)
        bi = h_ * char / k

        if bi > 0.1:
            warnings.append(f"fin Biot number h*(Ac/P)/k = {bi:.3g} is above 0.1: the 1-D (uniform across the section) assumption is poor")
        if tip_kind == "infinite" and ml < 2.65:
            warnings.append(f"mL = {ml:.3g} is below 2.65: a fin this short is not effectively infinite (the infinite-fin heat rate overestimates by {1.0 / math.tanh(ml) - 1.0:.1%} against an adiabatic tip)")
        if eps_f < 2.0:
            warnings.append(f"fin effectiveness {eps_f:.3g} is below 2: the fin is hardly worth adding (a fin is justified for effectiveness well above 2)")

        values: dict[str, QuantityOut] = {
            "heat_rate": out(q, "power", units, label="heat_rate"),
            "efficiency": plain(eta, label="efficiency"),
            "effectiveness": plain(eps_f, label="effectiveness"),
            "m": QuantityOut(quantity=m, unit="1 / meter"),
            "ml": plain(ml, label="ml"),
            "biot_fin": plain(bi, label="biot_fin"),
            "fin_surface_area": out(a_f, "area", units, label="fin_surface_area"),
            "cross_section_area": out(ac, "area", units, label="cross_section_area"),
        }
        data: dict[str, Any] = {
            "tip": tip_kind,
            "fin_type": kind,
            "perimeter_m": perimeter,
            "formula": {
                "infinite": "q = sqrt(h P k Ac) theta_b",
                "adiabatic": "q = sqrt(h P k Ac) theta_b tanh(mL)",
                "convective": "q = M (sinh mL + (h/mk) cosh mL)/(cosh mL + (h/mk) sinh mL)",
                "prescribed_temperature": "q = M (cosh mL - theta_L/theta_b)/sinh mL",
            }[tip_kind],
            "efficiency_definition": "q / (h A_f theta_b) with A_f = P L (+ Ac for the convective tip)",
            "assumptions": [
                "steady, 1-D conduction along the fin, constant k and h, uniform fin and base temperature, no radiation, no contact resistance at the base",
                "uniform cross-section; for the pin and the rectangular fin the sides are exposed to the same h",
            ],
        }
        if tip_kind == "adiabatic":
            data["efficiency_check_tanh_ml_over_ml"] = math.tanh(ml) / ml
        if tip_kind == "convective":
            lc = big_l + ac / perimeter
            q_corr = fin_heat_rate("adiabatic", m, lc, k, h_, perimeter, ac, theta_b)
            data["corrected_length_m"] = lc
            data["corrected_length_heat_rate_W"] = q_corr
            data["corrected_length_relative_difference"] = abs(q_corr - q) / abs(q)
        # Array
        if fin_count != 0 or not is_blank(base_unfinned_area):  # a negative count must reach whole_number and be refused, not become 'no array'
            n = whole_number(fin_count, name="fin_count", lo=1, hi=1_000_000)
            a_b = positive_quantity(base_unfinned_area, name="base_unfinned_area", dimension="area", unit="m**2", lo=0.0, hi=1e9, allow_zero=True) if not is_blank(base_unfinned_area) else 0.0
            a_t = n * a_f + a_b
            eta_o = 1.0 - (n * a_f / a_t) * (1.0 - eta)
            q_array = eta_o * h_ * a_t * theta_b
            q_sum = n * q + h_ * a_b * theta_b
            values["array_overall_surface_efficiency"] = plain(eta_o, label="array_overall_surface_efficiency")
            values["array_heat_rate"] = out(q_array, "power", units, label="array_heat_rate")
            values["array_total_area"] = out(a_t, "area", units, label="array_total_area")
            data["array"] = {
                "formula": "A_t = N A_f + A_b ; eta_o = 1 - (N A_f/A_t)(1 - eta_f) ; q = eta_o h A_t theta_b",
                "heat_rate_check_N_q_plus_h_Ab_theta_W": q_sum,
                "relative_difference": abs(q_sum - q_array) / abs(q_array),
                "note": "base temperature is assumed uniform and equal at fin roots and the exposed base; no base contact resistance",
            }
        return Computed(values=values, primary="heat_rate", warnings=warnings + notes, data=check_data(data))

    return run_tool(
        tool_name="thermal_fin",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "One-dimensional fin of uniform cross-section with constant k and h; tapered/annular fins, base contact "
            "resistance, radiation and the variation of h along the fin are not modelled.",
        ),
    )


__all__ = ["fin", "fin_heat_rate"]
