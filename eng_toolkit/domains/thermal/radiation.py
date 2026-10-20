"""``thermal_radiation``: net grey-body radiation exchange between two diffuse-grey surfaces.

Formulas (textbook enclosure results, Stefan-Boltzmann constant sigma = 5.670374419e-8 W/(m2 K4))
* two infinite parallel plates:      q = A sigma (T1^4 - T2^4) / (1/e1 + 1/e2 - 1)
* N radiation shields between them (shield emissivity es on both faces):
                                     q = A sigma (T1^4 - T2^4) / (1/e1 + 1/e2 - 1 + N (2/es - 1))   (all emissivities equal: divided by N+1)
* long concentric cylinders / concentric spheres (inner surface 1):
                                     q = sigma A1 (T1^4 - T2^4) / (1/e1 + (A1/A2)(1/e2 - 1)),  A1/A2 = r1/r2  (cylinders), (r1/r2)^2 (spheres)
* small convex body in a large enclosure:  q = e1 sigma A1 (T1^4 - T2^4)   (A1/A2 -> 0, enclosure emissivity drops out)
* equivalent radiation coefficient referred to A1:  h_r = q / (A1 (T1 - T2)) = sigma e_eff (T1 + T2)(T1^2 + T2^2)
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.thermal._common import (
    SIGMA,
    QuantityIn,
    TemperatureIn,
    UnitSystemIn,
    check_data,
    choice,
    is_blank,
    out,
    plain,
    plain_number,
    positive_quantity,
    temperature,
    whole_number,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

CONFIGURATIONS = ("parallel_plates", "concentric_cylinders", "concentric_spheres", "small_body_in_enclosure")
MAX_SHIELDS = 50


@server.tool(
    name="radiation",
    description=(
        "Net radiation heat exchange between two diffuse grey surfaces at uniform absolute temperatures: 'parallel_plates' "
        "(infinite, area A), 'concentric_cylinders' (long; inner radius, outer radius, length), 'concentric_spheres', or "
        "'small_body_in_enclosure' (q = e sigma A (T1^4 - T2^4)). Parallel plates accept N radiation shields. Returns net "
        "heat rate (positive from surface 1 to surface 2), effective emissivity, the equivalent radiation coefficient "
        "h_r = q/(A1 (T1 - T2)), shield temperatures, and the assumptions."
    ),
)
def radiation(
    configuration: Annotated[str, Field(description="'parallel_plates', 'concentric_cylinders', 'concentric_spheres' or 'small_body_in_enclosure'")],
    temperature_1: Annotated[TemperatureIn, Field(description="Absolute temperature of surface 1 (the plate / the inner body), e.g. '600 K'")],
    temperature_2: Annotated[TemperatureIn, Field(description="Absolute temperature of surface 2 (the other plate / the enclosure), e.g. '300 K'")],
    emissivity_1: Annotated[float, Field(description="Emissivity of surface 1, 0 < e <= 1, e.g. 0.8")],
    emissivity_2: Annotated[float, Field(description="Emissivity of surface 2 (ignored for small_body_in_enclosure; give any value in (0, 1], e.g. 1), 0 < e <= 1")] = 1.0,
    area: Annotated[str, Field(description="parallel_plates: plate area; small_body_in_enclosure: area of the body (surface 1). e.g. '2 m^2'")] = "",
    inner_radius: Annotated[str, Field(description="concentric cylinders / spheres: radius of surface 1, e.g. '50 mm'")] = "",
    outer_radius: Annotated[str, Field(description="concentric cylinders / spheres: radius of surface 2 (> inner), e.g. '100 mm'")] = "",
    length: Annotated[str, Field(description="concentric cylinders: length, e.g. '2 m'")] = "",
    shields: Annotated[int, Field(description="parallel_plates only: number of radiation shields between the plates (default 0)")] = 0,
    shield_emissivity: Annotated[float, Field(description="Emissivity of each shield (both faces). 0 = same as emissivity_1 and emissivity_2 only if those are equal; otherwise give it. 0 < e <= 1")] = 0.0,
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "configuration": configuration, "temperature_1": temperature_1, "temperature_2": temperature_2,
        "emissivity_1": emissivity_1, "emissivity_2": emissivity_2, "area": area, "inner_radius": inner_radius,
        "outer_radius": outer_radius, "length": length, "shields": shields, "shield_emissivity": shield_emissivity,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        cfg = choice(configuration, name="configuration", allowed=CONFIGURATIONS)
        warnings: list[str] = []
        t1 = temperature(temperature_1, name="temperature_1")
        t2 = temperature(temperature_2, name="temperature_2")
        e1 = plain_number(emissivity_1, name="emissivity_1", lo=0.0, hi=1.0, lo_open=True, hint="emissivity is dimensionless, 0 < e <= 1")
        e2 = plain_number(emissivity_2, name="emissivity_2", lo=0.0, hi=1.0, lo_open=True, hint="emissivity is dimensionless, 0 < e <= 1")
        n = whole_number(shields, name="shields", lo=0, hi=MAX_SHIELDS)
        if n > 0 and cfg != "parallel_plates":
            raise UnitsError("input.invalid", "radiation shields are supported for 'parallel_plates' only", {"param": "shields"})
        if n > 0:
            if shield_emissivity == 0.0:
                if e1 != e2:
                    raise UnitsError("input.missing", "give 'shield_emissivity' (the plates have different emissivities)", {"param": "shield_emissivity"})
                es = e1
                warnings.append(f"shield_emissivity not given: taken equal to the plate emissivity {e1:g}")
            else:
                es = plain_number(shield_emissivity, name="shield_emissivity", lo=0.0, hi=1.0, lo_open=True, hint="0 < e <= 1")
        else:
            es = 1.0
        data: dict[str, Any] = {"configuration": cfg}
        if cfg in ("parallel_plates", "small_body_in_enclosure"):
            if is_blank(area):
                raise UnitsError("input.missing", f"'area' is required for {cfg}", {"param": "area"})
            a1 = positive_quantity(area, name="area", dimension="area", unit="m**2", lo=1e-8, hi=1e9)
        else:
            if is_blank(inner_radius) or is_blank(outer_radius):
                raise UnitsError("input.missing", f"'inner_radius' and 'outer_radius' are required for {cfg}", {"param": "inner_radius|outer_radius"})
            r1 = positive_quantity(inner_radius, name="inner_radius", dimension="length", unit="m", lo=1e-6, hi=1e3)
            r2 = positive_quantity(outer_radius, name="outer_radius", dimension="length", unit="m", lo=1e-6, hi=1e3)
            if not r2 > r1:
                raise UnitsError("input.invalid", "'outer_radius' must be larger than 'inner_radius'", {"param": "outer_radius"})
            if cfg == "concentric_cylinders":
                if is_blank(length):
                    raise UnitsError("input.missing", "'length' is required for concentric_cylinders", {"param": "length"})
                ln = positive_quantity(length, name="length", dimension="length", unit="m", lo=1e-6, hi=1e6)
                a1, ratio = 2.0 * math.pi * r1 * ln, r1 / r2
                if ln < 5.0 * r2:
                    warnings.append("the cylinders are shorter than 5 x outer radius: end effects are neglected by the long-cylinder formula")
            else:
                a1, ratio = 4.0 * math.pi * r1 * r1, (r1 / r2) ** 2
        # resistance denominator R = 1/e1 + (A1/A2)(1/e2 - 1) [+ shields]
        if cfg == "parallel_plates":
            denominator = 1.0 / e1 + 1.0 / e2 - 1.0 + n * (2.0 / es - 1.0)
            formula = "q = A sigma (T1^4 - T2^4) / (1/e1 + 1/e2 - 1 + N (2/es - 1))"
        elif cfg == "small_body_in_enclosure":
            denominator = 1.0 / e1
            formula = "q = e1 sigma A1 (T1^4 - T2^4)"
            if not math.isclose(e2, 1.0):
                warnings.append("emissivity_2 is not used: for a small body in a large enclosure the enclosure emissivity drops out")
        else:
            denominator = 1.0 / e1 + ratio * (1.0 / e2 - 1.0)
            formula = "q = sigma A1 (T1^4 - T2^4) / (1/e1 + (A1/A2)(1/e2 - 1))"
            data["area_ratio_A1_over_A2"] = ratio
        e_eff = 1.0 / denominator
        driving = t1**4 - t2**4
        q = SIGMA * a1 * e_eff * driving
        h_r = SIGMA * e_eff * (t1 + t2) * (t1 * t1 + t2 * t2)
        values: dict[str, QuantityOut] = {
            "heat_rate": out(q, "power", units, label="heat_rate"),
            "heat_flux_surface_1": out(q / a1, "flux", units, label="heat_flux_surface_1"),
            "effective_emissivity": plain(e_eff, label="effective_emissivity"),
            "radiation_coefficient": out(h_r, "h", units, label="radiation_coefficient"),
            "area_surface_1": out(a1, "area", units, label="area_surface_1"),
        }
        if n > 0:
            # shield k: T_k^4 = T1^4 - (q/(A sigma)) * (cumulative resistance up to shield k)
            cumulative = 1.0 / e1 + 1.0 / es - 1.0
            for k in range(1, n + 1):
                if k > 1:
                    cumulative += 2.0 / es - 1.0
                t4 = t1**4 - (q / (a1 * SIGMA)) * cumulative
                values[f"temperature_shield_{k}"] = out(t4**0.25, "temperature", units, label="shield temperature")
        data.update(
            formula=formula,
            sigma_W_per_m2_K4=SIGMA,
            sign="positive heat rate flows from surface 1 to surface 2",
            shields=n,
            assumptions=[
                "diffuse-grey surfaces (emissivity = absorptivity, independent of direction and wavelength), opaque",
                "uniform temperature and radiosity over each surface",
                "non-participating medium (vacuum or transparent gas); radiation only, no conduction or convection between the surfaces",
                {
                    "parallel_plates": "plates are infinite (edge effects neglected), view factor 1 between them",
                    "concentric_cylinders": "long concentric cylinders (end effects neglected), surface 1 inside surface 2",
                    "concentric_spheres": "concentric spheres, surface 1 inside surface 2",
                    "small_body_in_enclosure": "convex body much smaller than the enclosure (A1/A2 -> 0)",
                }[cfg],
            ],
        )
        return Computed(values=values, primary="heat_rate", warnings=warnings, data=check_data(data))

    return run_tool(
        tool_name="thermal_radiation",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Grey-body radiation between isothermal diffuse surfaces in a transparent medium; view factors are limited to the "
            "listed configurations (no view-factor integration), and wavelength-dependent emissivity is not modelled.",
        ),
    )


__all__ = ["radiation"]
