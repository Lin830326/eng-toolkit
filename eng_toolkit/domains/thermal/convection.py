"""``thermal_convection``: Nusselt number and heat transfer coefficient from the standard textbook correlations.

There are no built-in property tables: conductivity, density, viscosity and cp (or Pr) are inputs, evaluated by the caller at the
film / bulk temperature the correlation asks for.

Correlations (author names as in the textbooks; the formulas are the published closed forms)
internal flow, D the diameter
* laminar, fully developed:  Nu = 3.66 (constant wall temperature), Nu = 4.36 (constant heat flux)
* Dittus-Boelter:  Nu = 0.023 Re^0.8 Pr^n,  n = 0.4 (fluid heated), 0.3 (fluid cooled)
* Gnielinski:  Nu = (f/8)(Re - 1000) Pr / (1 + 12.7 sqrt(f/8)(Pr^(2/3) - 1)),  Petukhov f = (0.790 ln Re - 1.64)^-2
external flow, L the plate length or the diameter
* flat plate, laminar, average:  Nu_L = 0.664 Re^(1/2) Pr^(1/3)
* flat plate, mixed (transition at Re = 5e5), average:  Nu_L = (0.037 Re^(4/5) - 871) Pr^(1/3)
* Churchill-Bernstein, cylinder in cross flow:
  Nu = 0.3 + 0.62 Re^(1/2) Pr^(1/3) / [1 + (0.4/Pr)^(2/3)]^(1/4) * [1 + (Re/282000)^(5/8)]^(4/5)
* Whitaker, sphere:  Nu = 2 + (0.4 Re^(1/2) + 0.06 Re^(2/3)) Pr^0.4 (mu/mu_s)^(1/4)
natural convection, Ra = g beta dT L^3 rho^2 Pr / mu^2
* Churchill-Chu, vertical plate (all Ra):  Nu = {0.825 + 0.387 Ra^(1/6) / [1 + (0.492/Pr)^(9/16)]^(8/27)}^2
* Churchill-Chu, vertical plate (laminar, Ra < 1e9):  Nu = 0.68 + 0.670 Ra^(1/4) / [1 + (0.492/Pr)^(9/16)]^(4/9)
* Churchill-Chu, horizontal cylinder:  Nu = {0.60 + 0.387 Ra^(1/6) / [1 + (0.559/Pr)^(9/16)]^(8/27)}^2
* natural_channel, symmetric isothermal parallel vertical plates (a finned heatsink; Elenbaas 1942, Bar-Cohen & Rohsenow 1984):
  Nu_S = h S / k = [576 / Ra*^2 + 2.873 / Ra*^(1/2)]^(-1/2),  Ra* = Ra_S S / L,  Ra_S = g beta dT S^3 / (nu alpha),
  S the gap between the plates, L the length the fluid flows along.  Limits: Ra* -> 0 fully developed Nu = Ra*/24; Ra* large the
  isolated plate 0.59 Ra*^(1/4).  Optimum gap (most heat from a fixed width): S_opt = 2.714 L / Ra_L^(1/4), Nu_opt = 1.307,
  Ra_L = g beta dT L^3 / (nu alpha).  Validity range quoted for the formula: 0.1 < Ra* < 1e5 (warned outside it).
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.domains.thermal._common import (
    DIM_BETA,
    DIM_CP,
    DIM_K,
    GRAVITY,
    QuantityIn,
    UnitSystemIn,
    check_data,
    check_range,
    choice,
    is_blank,
    out,
    plain,
    plain_number,
    positive_quantity,
    temperature,
)
from eng_toolkit.domains.thermal._common import unit_system as units_for
from eng_toolkit.domains.thermal._server import ENGINE, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError

CORRELATIONS = (
    "internal_laminar_constant_temperature",
    "internal_laminar_constant_flux",
    "dittus_boelter",
    "gnielinski",
    "flat_plate_laminar",
    "flat_plate_mixed",
    "cylinder_cross_flow",
    "sphere",
    "natural_vertical_plate",
    "natural_vertical_plate_laminar",
    "natural_horizontal_cylinder",
    "natural_channel",
)
NATURAL = {c for c in CORRELATIONS if c.startswith("natural")}


def petukhov_friction_factor(re: float) -> float:
    return (0.790 * math.log(re) - 1.64) ** -2


def gnielinski(re: float, pr: float) -> float:
    f8 = petukhov_friction_factor(re) / 8.0
    return f8 * (re - 1000.0) * pr / (1.0 + 12.7 * math.sqrt(f8) * (pr ** (2.0 / 3.0) - 1.0))


def dittus_boelter(re: float, pr: float, heating: bool) -> float:
    return 0.023 * re**0.8 * pr ** (0.4 if heating else 0.3)


def churchill_bernstein(re: float, pr: float) -> float:
    return 0.3 + 0.62 * math.sqrt(re) * pr ** (1.0 / 3.0) / (1.0 + (0.4 / pr) ** (2.0 / 3.0)) ** 0.25 * (1.0 + (re / 282000.0) ** (5.0 / 8.0)) ** 0.8


def whitaker_sphere(re: float, pr: float, viscosity_ratio: float = 1.0) -> float:
    return 2.0 + (0.4 * math.sqrt(re) + 0.06 * re ** (2.0 / 3.0)) * pr**0.4 * viscosity_ratio**0.25


def churchill_chu_plate(ra: float, pr: float) -> float:
    return (0.825 + 0.387 * ra ** (1.0 / 6.0) / (1.0 + (0.492 / pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)) ** 2


def churchill_chu_plate_laminar(ra: float, pr: float) -> float:
    return 0.68 + 0.670 * ra**0.25 / (1.0 + (0.492 / pr) ** (9.0 / 16.0)) ** (4.0 / 9.0)


def elenbaas_channel_nusselt(ra_star: float) -> float:
    """Nu_S = h S / k of symmetric isothermal parallel plates (Bar-Cohen & Rohsenow composite) for the modified Rayleigh number Ra*."""
    return (576.0 / ra_star**2 + 2.873 / math.sqrt(ra_star)) ** -0.5


CHANNEL_OPTIMUM_FACTOR = 2.714  # S_opt = 2.714 L / Ra_L^(1/4)
CHANNEL_OPTIMUM_NUSSELT = 1.307  # Nu_S at S_opt (isothermal plates)
CHANNEL_RA_STAR_RANGE = (0.1, 1e5)


def churchill_chu_cylinder(ra: float, pr: float) -> float:
    return (0.60 + 0.387 * ra ** (1.0 / 6.0) / (1.0 + (0.559 / pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)) ** 2


@server.tool(
    name="convection",
    description=(
        "Nusselt number and heat transfer coefficient h = Nu*k/L from a standard correlation. Fluid properties are INPUTS "
        "(no property tables): conductivity, density, dynamic viscosity and cp (or Prandtl number). correlation: "
        "internal_laminar_constant_temperature (Nu 3.66), internal_laminar_constant_flux (4.36), dittus_boelter, gnielinski "
        "(Petukhov friction factor), flat_plate_laminar, flat_plate_mixed, cylinder_cross_flow (Churchill-Bernstein), sphere "
        "(Whitaker), natural_vertical_plate / natural_vertical_plate_laminar / natural_horizontal_cylinder (Churchill-Chu), "
        "natural_channel (finned heatsink: Elenbaas / Bar-Cohen-Rohsenow, needs 'fin_gap', returns h and the optimum gap). "
        "'length' is the diameter, or the distance the fluid flows along (upright heatsink: the base height, NOT the fin height). "
        "Returns Re (or Ra), Pr, Nu, h; warns outside the correlation's range."
    ),
)
def convection(
    correlation: Annotated[str, Field(description="One of: internal_laminar_constant_temperature, internal_laminar_constant_flux, dittus_boelter, gnielinski, flat_plate_laminar, flat_plate_mixed, cylinder_cross_flow, sphere, natural_vertical_plate, natural_vertical_plate_laminar, natural_horizontal_cylinder, natural_channel")],
    length: Annotated[QuantityIn, Field(description="Diameter, or the distance the fluid flows along (upright heatsink: base height, NOT fin height), e.g. '100 mm'")],
    conductivity: Annotated[QuantityIn, Field(description="Fluid thermal conductivity, e.g. '0.026 W/(m*K)'")],
    density: Annotated[QuantityIn, Field(description="Fluid density, e.g. '1.2 kg/m^3'")],
    dynamic_viscosity: Annotated[QuantityIn, Field(description="Fluid dynamic viscosity, e.g. '1.8e-5 Pa*s'")],
    specific_heat: Annotated[str, Field(description="Fluid cp, e.g. '1007 J/(kg*K)'. Give this or 'prandtl'.")] = "",
    prandtl: Annotated[float, Field(description="Prandtl number (dimensionless). 0 = not given (then specific_heat is used).")] = 0.0,
    velocity: Annotated[str, Field(description="Forced convection: free-stream or mean velocity, e.g. '5 m/s'. Not used for natural convection.")] = "",
    heating: Annotated[bool, Field(description="dittus_boelter only: true if the fluid is being heated (n = 0.4), false if cooled (n = 0.3)")] = True,
    surface_temperature: Annotated[str, Field(description="Natural convection: absolute surface temperature, e.g. '80 degC'")] = "",
    ambient_temperature: Annotated[str, Field(description="Natural convection: absolute fluid temperature far away, e.g. '20 degC'")] = "",
    expansion_coefficient: Annotated[str, Field(description="Natural convection: volumetric expansion coefficient beta, e.g. '0.0034 1/K'. Blank = ideal gas, 1/T_film.")] = "",
    viscosity_surface: Annotated[str, Field(description="sphere optional: dynamic viscosity at the surface temperature (Whitaker mu/mu_s), e.g. '1.9e-5 Pa*s'")] = "",
    pipe_length: Annotated[str, Field(description="internal flow optional: pipe length, to check the entry region (L/D), e.g. '3 m'")] = "",
    fin_gap: Annotated[str, Field(description="natural_channel: clear gap between adjacent fins/plates, e.g. '5 mm'. On a plate correlation it only triggers a warning.")] = "",
    fin_height: Annotated[str, Field(description="natural_channel optional: fin height off the base (channel depth), e.g. '24 mm'; only used for a validity check")] = "",
    unit_system: UnitSystemIn = "metric",
) -> ToolResult:
    inputs = {
        "fin_gap": fin_gap, "fin_height": fin_height,
        "correlation": correlation, "length": length, "conductivity": conductivity, "density": density,
        "dynamic_viscosity": dynamic_viscosity, "specific_heat": specific_heat, "prandtl": prandtl, "velocity": velocity,
        "heating": heating, "surface_temperature": surface_temperature, "ambient_temperature": ambient_temperature,
        "expansion_coefficient": expansion_coefficient, "viscosity_surface": viscosity_surface, "pipe_length": pipe_length,
        "unit_system": unit_system,
    }

    def compute(_: JobHandle) -> Computed:
        units = units_for(unit_system)
        corr = choice(correlation, name="correlation", allowed=CORRELATIONS)
        natural = corr in NATURAL
        notes: list[str] = []
        warnings: list[str] = []
        big_l = positive_quantity(length, name="length", dimension="length", unit="m", lo=1e-6, hi=1e4)
        k = positive_quantity(conductivity, name="conductivity", dimension=DIM_K, unit="W/m/K", lo=1e-3, hi=1e3, notes=notes)
        rho = positive_quantity(density, name="density", dimension="density", unit="kg/m**3", lo=1e-4, hi=3e4)
        mu = positive_quantity(dynamic_viscosity, name="dynamic_viscosity", dimension="dynamic_viscosity", unit="Pa*s", lo=1e-7, hi=1e4)
        pr_given = None if prandtl == 0 else plain_number(prandtl, name="prandtl", lo=0.0, hi=1e6, lo_open=True, hint="Prandtl number is dimensionless and positive")
        if not is_blank(specific_heat):
            cp = positive_quantity(specific_heat, name="specific_heat", dimension=DIM_CP, unit="J/kg/K", lo=10.0, hi=1e5, notes=notes)
            pr = cp * mu / k
            if pr_given is not None and abs(pr_given - pr) > 0.05 * pr:
                warnings.append(f"'prandtl' = {pr_given:g} differs from cp*mu/k = {pr:.4g} by more than 5 %; cp*mu/k was used")
        elif pr_given is not None:
            pr = pr_given
        else:
            raise UnitsError("input.missing", "give 'specific_heat' or 'prandtl'", {"param": "specific_heat|prandtl"})
        check_range(pr, name="Prandtl number", unit="", lo=1e-4, hi=1e5, received=f"{pr:.4g}")

        values: dict[str, QuantityOut] = {}
        data: dict[str, Any] = {"correlation": corr}
        re = ra = None
        h_len = big_l  # the length in h = Nu k / L: the gap for natural_channel
        gap = depth = None
        if natural:
            if is_blank(surface_temperature) or is_blank(ambient_temperature):
                raise UnitsError("input.missing", "natural convection needs 'surface_temperature' and 'ambient_temperature'", {"param": "surface_temperature|ambient_temperature"})
            ts = temperature(surface_temperature, name="surface_temperature")
            ta = temperature(ambient_temperature, name="ambient_temperature")
            if ts == ta:
                raise UnitsError("input.invalid", "surface and ambient temperatures are equal: no buoyancy, no natural convection", {"param": "surface_temperature|ambient_temperature"})
            if is_blank(expansion_coefficient):
                beta = 1.0 / (0.5 * (ts + ta))
                warnings.append(f"expansion_coefficient not given: ideal gas assumed, beta = 1/T_film = {beta:.4g} 1/K")
            else:
                beta = positive_quantity(expansion_coefficient, name="expansion_coefficient", dimension=DIM_BETA, unit="1/K", lo=1e-7, hi=1.0, notes=notes)
            if not is_blank(velocity):
                warnings.append("'velocity' is not used for natural convection and was ignored")
            d_t = abs(ts - ta)
            gap = None if is_blank(fin_gap) else positive_quantity(fin_gap, name="fin_gap", dimension="length", unit="m", lo=1e-5, hi=1.0)
            depth = None if is_blank(fin_height) else positive_quantity(fin_height, name="fin_height", dimension="length", unit="m", lo=1e-5, hi=10.0)
            if corr == "natural_channel" and gap is None:
                raise UnitsError("input.missing", "natural_channel needs 'fin_gap' (the clear gap between adjacent fins); 'length' is the flow length", {"param": "fin_gap"})
            gr = GRAVITY * beta * d_t * big_l**3 * rho * rho / (mu * mu)
            ra = gr * pr
            values["grashof"] = plain(gr, label="grashof")
            values["rayleigh"] = plain(ra, label="rayleigh")
            data["rayleigh_definition"] = "Ra = g beta |Ts - Tinf| L^3 rho^2 Pr / mu^2, g = 9.80665 m/s^2"
            if corr == "natural_channel":
                # Rayleigh numbers of the gap: Ra_S = Ra (S/L)^3, Ra* = Ra_S S/L = Ra (S/L)^4
                ra_s = ra * (gap / big_l) ** 3
                ra_star = ra_s * gap / big_l
                nu = elenbaas_channel_nusselt(ra_star)
                h_len = gap
                s_opt = CHANNEL_OPTIMUM_FACTOR * big_l / ra**0.25
                values["rayleigh_gap"] = plain(ra_s, label="rayleigh_gap")
                values["rayleigh_modified"] = plain(ra_star, label="rayleigh_modified")
                values["optimum_gap"] = out(s_opt, "small_length", units, label="optimum_gap")
                values["h_at_optimum_gap"] = out(CHANNEL_OPTIMUM_NUSSELT * k / s_opt, "h", units, label="h_at_optimum_gap")
                data.update(
                    rayleigh_definition_channel="Ra_S = g beta dT S^3 rho^2 Pr / mu^2 (gap S); Ra* = Ra_S S/L; 'rayleigh' above is based on the flow length L",
                    formula_channel="Nu_S = h S/k = [576/Ra*^2 + 2.873/Ra*^0.5]^-0.5 (Elenbaas; Bar-Cohen & Rohsenow composite, symmetric isothermal plates)",
                    h_basis="h is per wetted plate surface (both faces of every fin and the base between fins), referred to Ts - Tinf; Nu_S uses the gap S",
                    optimum_gap_formula="S_opt = 2.714 L / Ra_L^(1/4), Nu_S = 1.307 there; maximises the heat flow of a fixed-width array of thin fins",
                    gap_over_optimum=gap / s_opt,
                    h_isolated_plate_same_length_W_per_m2_K=churchill_chu_plate(ra, pr) * k / big_l,
                )
                lo_s, hi_s = CHANNEL_RA_STAR_RANGE
                if not lo_s <= ra_star <= hi_s:
                    warnings.append(
                        f"Ra* = {ra_star:.3g} is outside {lo_s:g} .. {hi_s:g}, the range the Elenbaas / Bar-Cohen-Rohsenow formula is quoted for; "
                        "the two limits (fully developed channel, isolated plate) still hold but treat the result as an extrapolation"
                    )
                if ra > 1e9:
                    warnings.append(f"Ra_L = {ra:.3g} based on the flow length is above 1e9: the flow turns turbulent, the laminar channel formula does not apply")
                if depth is not None and depth < gap:
                    warnings.append(f"fin_height {depth:.4g} m is smaller than the gap {gap:.4g} m: this is not a channel (fins too short), the plate correlation or a 3-D model fits better")
                if gap < 0.5 * s_opt or gap > 2.0 * s_opt:
                    warnings.append(f"the gap {gap * 1e3:.3g} mm is {gap / s_opt:.2g} times the optimum {s_opt * 1e3:.3g} mm: more or fewer fins in the same width would move more heat")
            elif corr == "natural_vertical_plate":
                nu = churchill_chu_plate(ra, pr)
                if ra > 1e12:
                    warnings.append(f"Ra = {ra:.3g} is above 1e12, beyond the data behind the Churchill-Chu correlation")
            elif corr == "natural_vertical_plate_laminar":
                nu = churchill_chu_plate_laminar(ra, pr)
                if ra > 1e9:
                    warnings.append(f"Ra = {ra:.3g} is above 1e9 (transition to turbulence): use natural_vertical_plate")
            else:
                nu = churchill_chu_cylinder(ra, pr)
                if not 1e-5 <= ra <= 1e12:
                    warnings.append(f"Ra = {ra:.3g} is outside 1e-5 .. 1e12, the range of the Churchill-Chu horizontal-cylinder correlation")
            if corr == "natural_horizontal_cylinder" and (gap is not None or depth is not None):
                warnings.append("'fin_gap' / 'fin_height' are not used by natural_horizontal_cylinder and were ignored")
            if corr in ("natural_vertical_plate", "natural_vertical_plate_laminar") and (gap is not None or depth is not None):
                hint = ""
                if gap is not None:
                    h_channel = elenbaas_channel_nusselt(ra * (gap / big_l) ** 4) * k / gap
                    h_plate = nu * k / big_l
                    hint = f" The channel formula (natural_channel) gives h = {h_channel:.3g} W/m^2/K at this gap against {h_plate:.3g} here ({h_plate / h_channel:.2g} times higher)."
                    data["h_channel_for_comparison_W_per_m2_K"] = h_channel
                warnings.append(
                    "fin_gap / fin_height was given: this looks like a finned array. The isolated-plate correlation holds only when the boundary layers "
                    "do not meet (gap above about 2 boundary-layer thicknesses, roughly 10 mm for air near a 100 mm plate); in narrower gaps it overstates h. "
                    "Use natural_channel with 'length' = the flow length." + hint
                )
        else:
            if is_blank(velocity):
                raise UnitsError("input.missing", "forced convection needs 'velocity'", {"param": "velocity"})
            v = positive_quantity(velocity, name="velocity", dimension="velocity", unit="m/s", lo=1e-6, hi=1e4)
            re = rho * v * big_l / mu
            values["reynolds"] = plain(re, label="reynolds")
            plen = None if is_blank(pipe_length) else positive_quantity(pipe_length, name="pipe_length", dimension="length", unit="m", lo=1e-6, hi=1e6)
            if corr.startswith("internal") or corr in ("dittus_boelter", "gnielinski"):
                if corr == "internal_laminar_constant_temperature":
                    nu = 3.66
                elif corr == "internal_laminar_constant_flux":
                    nu = 4.36
                elif corr == "dittus_boelter":
                    nu = dittus_boelter(re, pr, heating)
                else:
                    if re <= 1000.0:
                        raise UnitsError("input.out_of_range", f"Gnielinski's correlation is undefined at Re = {re:.4g} (it needs Re > 1000, valid 3000..5e6)", {"param": "velocity", "reynolds": re})
                    nu = gnielinski(re, pr)
                    data["petukhov_friction_factor"] = petukhov_friction_factor(re)
                laminar = corr.startswith("internal_laminar")
                if laminar and re > 2300.0:
                    warnings.append(f"Re = {re:.4g} is above 2300: the flow is not laminar, the constant Nusselt number does not apply (use gnielinski)")
                if laminar and plen is not None:
                    entry = 0.05 * re * pr * big_l
                    if plen < entry:
                        warnings.append(f"pipe length {plen:.4g} m is below the thermal entry length ~0.05 Re Pr D = {entry:.4g} m: the flow is not thermally developed, the real Nu is higher")
                if corr == "dittus_boelter":
                    if re < 1e4:
                        warnings.append(f"Re = {re:.4g} is below 10 000: outside the Dittus-Boelter range (Re >= 10 000)")
                    if not 0.6 <= pr <= 160.0:
                        warnings.append(f"Pr = {pr:.4g} is outside 0.6 .. 160, the Dittus-Boelter range")
                    if plen is not None and plen / big_l < 10.0:
                        warnings.append(f"L/D = {plen / big_l:.3g} is below 10: entry effects, Dittus-Boelter underpredicts")
                if corr == "gnielinski":
                    if not 3000.0 <= re <= 5e6:
                        warnings.append(f"Re = {re:.4g} is outside 3000 .. 5e6, the stated range of Gnielinski's correlation (2300 .. 3000 is a transition-region extrapolation)")
                    if not 0.5 <= pr <= 2000.0:
                        warnings.append(f"Pr = {pr:.4g} is outside 0.5 .. 2000, the range of Gnielinski's correlation")
            elif corr == "flat_plate_laminar":
                nu = 0.664 * math.sqrt(re) * pr ** (1.0 / 3.0)
                data["local_nusselt_at_trailing_edge"] = 0.332 * math.sqrt(re) * pr ** (1.0 / 3.0)
                if re > 5e5:
                    warnings.append(f"Re_L = {re:.4g} is above 5e5: the boundary layer is turbulent at the trailing edge, use flat_plate_mixed")
                if pr < 0.6:
                    warnings.append(f"Pr = {pr:.4g} is below 0.6: outside the range of the laminar flat-plate correlation (0.6 <= Pr <= 50)")
                elif pr > 50.0:
                    warnings.append(f"Pr = {pr:.4g} is above 50: outside the range of the laminar flat-plate correlation (0.6 <= Pr <= 50)")
            elif corr == "flat_plate_mixed":
                nu = (0.037 * re**0.8 - 871.0) * pr ** (1.0 / 3.0)
                if nu <= 0.0:
                    raise UnitsError("input.out_of_range", f"the mixed flat-plate formula gives no heat transfer at Re_L = {re:.4g} (valid 5e5 .. 1e8); use flat_plate_laminar", {"param": "velocity", "reynolds": re})
                if not 5e5 <= re <= 1e8:
                    warnings.append(f"Re_L = {re:.4g} is outside 5e5 .. 1e8: the mixed laminar/turbulent formula (transition at 5e5) does not apply")
                if not 0.6 <= pr <= 60.0:
                    warnings.append(f"Pr = {pr:.4g} is outside 0.6 .. 60, the range of the mixed flat-plate correlation")
            elif corr == "cylinder_cross_flow":
                nu = churchill_bernstein(re, pr)
                if re * pr <= 0.2:
                    warnings.append(f"Re*Pr = {re * pr:.4g} is not above 0.2: outside the Churchill-Bernstein range")
            else:  # sphere
                ratio = 1.0
                if not is_blank(viscosity_surface):
                    mu_s = positive_quantity(viscosity_surface, name="viscosity_surface", dimension="dynamic_viscosity", unit="Pa*s", lo=1e-7, hi=1e4)
                    ratio = mu / mu_s
                    if not 1.0 <= ratio <= 3.2:
                        warnings.append(f"mu/mu_s = {ratio:.3g} is outside 1 .. 3.2, the Whitaker range")
                else:
                    warnings.append("viscosity_surface not given: mu/mu_s taken as 1 (valid for small temperature differences)")
                nu = whitaker_sphere(re, pr, ratio)
                if not 3.5 <= re <= 7.6e4:
                    warnings.append(f"Re = {re:.4g} is outside 3.5 .. 7.6e4, the Whitaker range")
                if not 0.71 <= pr <= 380.0:
                    warnings.append(f"Pr = {pr:.4g} is outside 0.71 .. 380, the Whitaker range")
            if not is_blank(surface_temperature) or not is_blank(ambient_temperature) or not is_blank(expansion_coefficient):
                warnings.append("temperatures and expansion_coefficient are only used for natural convection and were ignored")
            if not is_blank(fin_gap) or not is_blank(fin_height):
                warnings.append("'fin_gap' / 'fin_height' are only used for natural convection and were ignored")
        values["prandtl"] = plain(pr, label="prandtl")
        values["nusselt"] = plain(nu, label="nusselt")
        h = nu * k / h_len
        values["h"] = out(h, "h", units, label="h")
        data.update(
            h_definition="h = Nu k / S with the gap S" if corr == "natural_channel" else "h = Nu k / L",
            assumptions=[
                "fluid properties as given, taken at the temperature the correlation prescribes (film or bulk); no property tables are built in",
                "smooth surfaces, no radiation, no mass transfer, uniform surface condition as stated by the correlation",
            ],
            reynolds=re,
            rayleigh=ra,
        )
        return Computed(values=values, primary="h", warnings=warnings + notes, data=check_data(data))

    return run_tool(
        tool_name="thermal_convection",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Empirical correlations with typical scatter of 10-25 %; fluid properties are supplied by the caller. Two-phase "
            "flow, mixed (forced + natural) convection, rough surfaces, tube banks and liquid metals are not covered.",
        ),
    )


__all__ = ["churchill_bernstein", "churchill_chu_cylinder", "churchill_chu_plate", "convection", "dittus_boelter", "elenbaas_channel_nusselt", "gnielinski", "petukhov_friction_factor", "whitaker_sphere"]
