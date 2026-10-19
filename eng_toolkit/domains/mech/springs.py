"""``mech_spring_compression``: round-wire helical compression spring (rate, Wahl factor, shear stress, solid height).

Formulas (textbook closed forms, Shigley / Wahl; no tables)
* spring index ``C = D / d``
* rate ``k = G d^4 / (8 D^3 Na)``
* Wahl factor ``Kw = (4C - 1)/(4C - 4) + 0.615 / C``
* corrected shear stress ``tau = Kw * 8 F D / (pi d^3)``
* total coils from active coils by end type, solid height ``Hs``:
  squared and ground ``Nt = Na + 2``, ``Hs = Nt d``; squared ``Nt = Na + 2``, ``Hs = (Nt + 1) d``;
  plain and ground ``Nt = Na + 1``, ``Hs = Nt d``; plain ``Nt = Na``, ``Hs = (Nt + 1) d``
* buckling: a spring is unconditionally stable when ``L0 < 2.63 D / alpha`` (alpha = 0.5 both ends on flat parallel plates,
  1.0 for one end free to tilt), the usual textbook criterion
"""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.mech._common import (
    QuantityIn,
    UnitSystemIn,
    check_range,
    choice,
    is_blank,
    optional_quantity,
    out,
    plain_number,
    positive_quantity,
)
from eng_toolkit.domains.mech._common import unit_system as units_for
from eng_toolkit.domains.mech._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, ureg

# end type -> (inactive coils added to Na to get the total, solid height in wire diameters = Nt + extra)
_END_TYPES: dict[str, tuple[float, float]] = {
    "squared_ground": (2.0, 0.0),
    "squared": (2.0, 1.0),
    "plain_ground": (1.0, 0.0),
    "plain": (0.0, 1.0),
}


def wahl_factor(c: float) -> float:
    return (4.0 * c - 1.0) / (4.0 * c - 4.0) + 0.615 / c


@server.tool(
    name="spring_compression",
    description=(
        "Round-wire helical compression spring: spring rate k = G*d^4/(8*D^3*Na), spring index, Wahl factor, "
        "solid height and total coils from the end type. With a 'load' or a 'deflection' it also returns the other, "
        "the Wahl-corrected shear stress and (with 'allowable_shear') the utilization; with 'free_length' it "
        "returns the stroke to solid and warns about buckling. Static loading only."
    ),
)
def spring_compression(
    wire_diameter: Annotated[QuantityIn, Field(description="Wire diameter d with unit, e.g. '2 mm'")],
    mean_diameter: Annotated[QuantityIn, Field(description="Mean coil diameter D (outer minus wire) with unit, e.g. '16 mm'")],
    active_coils: Annotated[float, Field(description="Number of active coils Na (may be fractional), e.g. 8")],
    shear_modulus: Annotated[
        QuantityIn, Field(description="Shear modulus G of the wire, e.g. '79.3 GPa' (typical carbon / music-wire steel)")
    ] = "79.3 GPa",
    end_type: Annotated[
        str, Field(description="'squared_ground' (default), 'squared', 'plain_ground' or 'plain' ends; sets total coils and solid height")
    ] = "squared_ground",
    free_length: Annotated[
        str, Field(description="Optional free length L0, e.g. '50 mm'. Gives the stroke to solid, the solid load and the buckling check.")
    ] = "",
    load: Annotated[
        str, Field(description="Optional axial load F, e.g. '100 N'. Gives deflection and stress. Give this OR deflection.")
    ] = "",
    deflection: Annotated[
        str, Field(description="Optional compression y, e.g. '10 mm'. Gives load and stress. Give this OR load.")
    ] = "",
    allowable_shear: Annotated[
        str, Field(description="Optional allowable shear stress, e.g. '800 MPa' (from the wire material data). Gives the utilization.")
    ] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "wire_diameter": wire_diameter,
        "mean_diameter": mean_diameter,
        "active_coils": active_coils,
        "shear_modulus": shear_modulus,
        "end_type": end_type,
        "free_length": free_length,
        "load": load,
        "deflection": deflection,
        "allowable_shear": allowable_shear,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        kind = choice(end_type, name="end_type", allowed=tuple(_END_TYPES))
        d = positive_quantity(wire_diameter, name="wire_diameter", dimension="length", unit="mm", lo=0.05, hi=50.0)
        big_d = positive_quantity(mean_diameter, name="mean_diameter", dimension="length", unit="mm", lo=0.3, hi=500.0)
        na = plain_number(active_coils, name="active_coils", lo=0.0, hi=500.0, lo_open=True)
        g = positive_quantity(shear_modulus, name="shear_modulus", dimension="pressure", unit="GPa", lo=1.0, hi=1000.0)
        d_mm, big_d_mm, g_mpa = (float(x.to(u).magnitude) for x, u in ((d, "mm"), (big_d, "mm"), (g, "MPa")))
        c = big_d_mm / d_mm
        check_range(c, name="spring index D/d", unit="", lo=1.5, hi=50.0, received=f"{c:.3g}")
        if na < 0.5:
            raise UnitsError("input.out_of_range", f"'active_coils' = {na:g} is below 0.5", {"param": "active_coils", "min": 0.5})
        have_load, have_defl = not is_blank(load), not is_blank(deflection)
        if have_load and have_defl:
            raise UnitsError("input.invalid", "give 'load' or 'deflection', not both", {"param": "load|deflection"})
        l0 = optional_quantity(free_length, name="free_length", dimension="length", unit="mm", lo=0.1, hi=5000.0)

        extra_inactive, extra_solid = _END_TYPES[kind]
        nt = na + extra_inactive
        solid_mm = (nt + extra_solid) * d_mm
        k = g_mpa * d_mm**4 / (8.0 * big_d_mm**3 * na)  # N/mm
        kw = wahl_factor(c)
        stress_per_n = kw * 8.0 * big_d_mm / (math.pi * d_mm**3)  # MPa per N

        stroke_mm = None
        if l0 is not None:
            l0_mm = float(l0.to("mm").magnitude)
            stroke_mm = l0_mm - solid_mm
            if stroke_mm <= 0:
                raise UnitsError(
                    "input.invalid",
                    f"free length {l0:~P} is not longer than the solid height {solid_mm:.4g} mm: the spring has no stroke",
                    {"param": "free_length", "solid_height_mm": solid_mm},
                )

        values: dict[str, QuantityOut] = {
            "spring_rate": out(ureg.Quantity(k, "N/mm"), units["rate"]),
            "spring_index": QuantityOut(quantity=c, unit="dimensionless"),
            "wahl_factor": QuantityOut(quantity=kw, unit="dimensionless"),
            "solid_height": out(ureg.Quantity(solid_mm, "mm"), units["length"]),
            "total_coils": QuantityOut(quantity=nt, unit="dimensionless"),
        }
        warnings: list[str] = []
        data = {
            "formula": "k = G d^4 / (8 D^3 Na) ; Kw = (4C-1)/(4C-4) + 0.615/C ; tau = Kw 8 F D / (pi d^3)",
            "end_type": kind,
            "uncorrected_stress_factor_static": 1.0 + 0.5 / c,
        }
        f_n = y_mm = None
        if have_load:
            f_q = positive_quantity(load, name="load", dimension="force", unit="N", lo=1e-6, hi=1e7)
            f_n = float(f_q.to("N").magnitude)
            y_mm = f_n / k
        elif have_defl:
            y_q = positive_quantity(deflection, name="deflection", dimension="length", unit="mm", lo=1e-6, hi=5000.0)
            y_mm = float(y_q.to("mm").magnitude)
            f_n = k * y_mm
        if f_n is not None and y_mm is not None:
            if stroke_mm is not None and y_mm > stroke_mm:
                raise UnitsError(
                    "input.out_of_range",
                    f"deflection {y_mm:.4g} mm is more than the stroke to solid ({stroke_mm:.4g} mm = free length - solid height)",
                    {"param": "load|deflection", "deflection_mm": y_mm, "stroke_to_solid_mm": stroke_mm},
                )
            tau = stress_per_n * f_n
            values["load"] = out(ureg.Quantity(f_n, "N"), units["small_force"])
            values["deflection"] = out(ureg.Quantity(y_mm, "mm"), units["length"])
            values["shear_stress"] = out(ureg.Quantity(tau, "MPa"), units["stress"])
            if l0 is not None:
                values["length_under_load"] = out(ureg.Quantity(l0_mm - y_mm, "mm"), units["length"])
        if stroke_mm is not None:
            f_solid = k * stroke_mm
            values["stroke_to_solid"] = out(ureg.Quantity(stroke_mm, "mm"), units["length"])
            values["solid_load"] = out(ureg.Quantity(f_solid, "N"), units["small_force"])
            values["solid_shear_stress"] = out(ureg.Quantity(stress_per_n * f_solid, "MPa"), units["stress"])
            slender = l0_mm / big_d_mm
            if slender > 2.63 / 0.5:
                warnings.append(
                    f"L0/D = {slender:.2f} is above 5.26: the spring can buckle even with both ends on flat parallel "
                    "plates; guide it (rod or bore) or shorten it"
                )
            elif slender > 2.63:
                warnings.append(
                    f"L0/D = {slender:.2f} is above 2.63: stable only if both ends are held flat and parallel "
                    "(alpha = 0.5); it can buckle if an end can tilt; check the critical deflection"
                )
        allow = optional_quantity(allowable_shear, name="allowable_shear", dimension="pressure", unit="MPa", lo=10.0, hi=5000.0)
        if allow is not None:
            allow_mpa = float(allow.to("MPa").magnitude)
            if f_n is not None:
                util = stress_per_n * f_n / allow_mpa
                values["utilization"] = QuantityOut(quantity=util, unit="dimensionless")
                if util > 1.0:
                    warnings.append(f"shear stress is {util:.2f} x the allowable at the working load")
            if stroke_mm is not None:
                util_solid = stress_per_n * k * stroke_mm / allow_mpa
                values["utilization_at_solid"] = QuantityOut(quantity=util_solid, unit="dimensionless")
                if util_solid > 1.0:
                    warnings.append(
                        f"at solid height the shear stress is {util_solid:.2f} x the allowable: the spring would take a "
                        "permanent set if compressed solid (many designs allow a higher value at solid; check the material data)"
                    )
        if not 4.0 <= c <= 12.0:
            warnings.append(
                f"spring index C = {c:.2f} is outside the usual 4-12: below 4 the wire is hard to coil and the Wahl "
                "correction is large, above 12 the spring tangles and is hard to hold to tolerance"
            )
        if na < 3.0:
            warnings.append(f"only {na:g} active coils: the rate formula is less accurate for very few coils (end effects)")
        return Computed(values=values, primary="spring_rate", warnings=warnings, data=data)

    return run_tool(
        tool_name="mech_spring_compression",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Static loading of a round-wire helical compression spring only: fatigue life, set removal, surge, "
            "temperature and the ends' contribution to the rate are not covered. The solid height is a nominal value "
            "(manufacturing tolerance adds to it).",
        ),
    )


__all__ = ["spring_compression", "wahl_factor"]
