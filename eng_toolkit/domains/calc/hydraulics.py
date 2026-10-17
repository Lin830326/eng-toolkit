"""``calc.hydraulics`` sub-server: cylinder force and pipe pressure drop.

Both tools take every physical input *with a unit* (``"100 mm"`` or
``{"value": 100, "unit": "mm"}``) and return the unified
:class:`eng_toolkit.contract.ToolResult`.

Engines
-------
* cylinder force: closed-form, pure pint (engine ``eng_toolkit.calc.hydraulics``)
* pipe pressure drop: Darcy-Weisbach; friction factor from the MIT-licensed
  ``fluids`` package (Clamond's exact Colebrook solution, laminar ``64/Re``)
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import fluids
import fluids.core
import fluids.friction
from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__
from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import parse_quantity, require_positive, to_quantity_out, ureg

ENGINE = "eng_toolkit.calc.hydraulics"
TOOL_NAMES: tuple[str, ...] = ("cylinder_force", "pipe_pressure_drop")

QuantityIn = Annotated[
    str | dict[str, Any],
    Field(
        description=(
            "Quantity WITH unit: a string like '100 mm' / '210 bar', "
            "or {'value': 100, 'unit': 'mm'}. Bare numbers are rejected."
        )
    ),
]

server = FastMCP(
    "calc",
    instructions=(
        "Hydraulic calculations. Every input must carry a unit. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)

# Flow-regime limits used for warnings and the nominal uncertainty band.
_RE_LAMINAR = 2300.0
_RE_TURBULENT = 4000.0


# --------------------------------------------------------------------------- cylinder force


@server.tool(
    name="cylinder_force",
    description=(
        "Theoretical push (cap end) and pull (rod end) force of a single-rod hydraulic "
        "cylinder from bore diameter, rod diameter and working pressure. "
        "Ideal: no seal friction, no back pressure, no rod-side pressure."
    ),
)
def cylinder_force(
    bore: QuantityIn,
    pressure: QuantityIn,
    rod: QuantityIn | None = None,
    output_unit: Annotated[str, Field(description="Force unit for the outputs, e.g. 'kN', 'N', 'lbf'")] = "kN",
) -> ToolResult:
    inputs = {"bore": bore, "rod": rod, "pressure": pressure, "output_unit": output_unit}

    def compute(_: JobHandle) -> Computed:
        d = require_positive(parse_quantity(bore, name="bore", dimension="length"), name="bore")
        p = require_positive(parse_quantity(pressure, name="pressure", dimension="pressure"), name="pressure")
        if rod is None:
            r = ureg.Quantity(0.0, "mm")
        else:
            r = parse_quantity(rod, name="rod", dimension="length")
            if float(r.magnitude) < 0:
                from eng_toolkit.units import UnitsError

                raise UnitsError("input.invalid", f"'rod' must be >= 0, got {r:~P}", {"param": "rod"})
        if r.to("m").magnitude >= d.to("m").magnitude:
            from eng_toolkit.units import UnitsError

            raise UnitsError(
                "input.invalid",
                f"rod diameter ({r:~P}) must be smaller than bore ({d:~P})",
                {"param": "rod", "bore": str(d), "rod": str(r)},
            )

        bore_area = (math.pi / 4.0) * d**2
        annulus_area = (math.pi / 4.0) * (d**2 - r**2)
        push = (p * bore_area).to("N")
        pull = (p * annulus_area).to("N")

        values = {
            "push_force": to_quantity_out(push, output_unit),
            "pull_force": to_quantity_out(pull, output_unit),
            "bore_area": to_quantity_out(bore_area, "mm**2"),
            "annulus_area": to_quantity_out(annulus_area, "mm**2"),
            "area_ratio": QuantityOut(
                quantity=float((bore_area / annulus_area).to("dimensionless").magnitude),
                unit="dimensionless",
            ),
        }
        warnings = []
        if rod is None:
            warnings.append("rod not given: pull force assumes rod diameter 0 (equal to push force)")
        return Computed(
            values=values,
            primary="push_force",
            warnings=warnings,
            data={"formula": "F_push = p * pi/4 * d_bore^2 ; F_pull = p * pi/4 * (d_bore^2 - d_rod^2)"},
        )

    return run_tool(
        tool_name="calc_cylinder_force",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Ideal theoretical force: seal friction, back pressure and rod-side pressure are not included.",
        ),
    )


# --------------------------------------------------------------------------- pipe pressure drop


@server.tool(
    name="pipe_pressure_drop",
    description=(
        "Frictional pressure drop of a fully developed, incompressible, steady flow in a straight "
        "circular pipe (Darcy-Weisbach). Friction factor: laminar 64/Re, turbulent Colebrook "
        "(Clamond) via the MIT-licensed `fluids`. Minor losses (fittings, bends) are NOT included."
    ),
)
def pipe_pressure_drop(
    diameter: Annotated[QuantityIn, Field(description="Pipe inner diameter, e.g. '10 mm'")],
    length: Annotated[QuantityIn, Field(description="Straight pipe length, e.g. '1 m'")],
    flow_rate: Annotated[QuantityIn, Field(description="Volumetric flow rate, e.g. '4.7 L/min'")],
    density: Annotated[QuantityIn, Field(description="Fluid density, e.g. '870 kg/m^3'")],
    viscosity: Annotated[
        QuantityIn,
        Field(description="DYNAMIC viscosity, e.g. '0.04 Pa*s' or '40 cP' (kinematic cSt is rejected)"),
    ],
    roughness: Annotated[
        QuantityIn, Field(description="Absolute wall roughness; default 0.0015 mm (drawn tubing)")
    ] = "0.0015 mm",
    output_unit: Annotated[str, Field(description="Pressure unit for the drop, e.g. 'bar', 'kPa', 'psi'")] = "bar",
) -> ToolResult:
    inputs = {
        "diameter": diameter,
        "length": length,
        "flow_rate": flow_rate,
        "density": density,
        "viscosity": viscosity,
        "roughness": roughness,
        "output_unit": output_unit,
    }

    def compute(_: JobHandle) -> Computed:
        D = require_positive(parse_quantity(diameter, name="diameter", dimension="length"), name="diameter")
        L = require_positive(parse_quantity(length, name="length", dimension="length"), name="length")
        Q = require_positive(
            parse_quantity(flow_rate, name="flow_rate", dimension="volumetric_flow"), name="flow_rate"
        )
        rho = require_positive(parse_quantity(density, name="density", dimension="density"), name="density")
        mu = require_positive(
            parse_quantity(viscosity, name="viscosity", dimension="dynamic_viscosity"), name="viscosity"
        )
        eps = parse_quantity(roughness, name="roughness", dimension="length")

        D_m = float(D.to("m").magnitude)
        L_m = float(L.to("m").magnitude)
        rho_si = float(rho.to("kg/m**3").magnitude)
        mu_si = float(mu.to("Pa*s").magnitude)
        eps_m = float(eps.to("m").magnitude)

        area = math.pi / 4.0 * D_m**2
        v = float(Q.to("m**3/s").magnitude) / area
        Re = fluids.core.Reynolds(V=v, D=D_m, rho=rho_si, mu=mu_si)
        f = fluids.friction.friction_factor(Re=Re, eD=eps_m / D_m)  # Darcy friction factor
        dp_pa = f * (L_m / D_m) * rho_si * v**2 / 2.0
        head_m = dp_pa / (rho_si * 9.80665)

        if Re < _RE_LAMINAR:
            regime, rel_unc = "laminar", 0.05
        elif Re < _RE_TURBULENT:
            regime, rel_unc = "transitional", 0.20
        else:
            regime, rel_unc = "turbulent", 0.10

        dp = ureg.Quantity(dp_pa, "Pa")
        values = {
            "pressure_drop": to_quantity_out(dp, output_unit, uncertainty=dp * rel_unc),
            "velocity": to_quantity_out(ureg.Quantity(v, "m/s"), "m/s"),
            "reynolds": QuantityOut(quantity=float(Re), unit="dimensionless"),
            "friction_factor_darcy": QuantityOut(quantity=float(f), unit="dimensionless"),
            "head_loss": to_quantity_out(ureg.Quantity(head_m, "m"), "m"),
        }
        warnings = [
            f"flow regime: {regime} (Re = {Re:.0f}); uncertainty is a nominal +/-{rel_unc:.0%} band "
            "for Darcy-Weisbach with Colebrook friction, not a calibrated estimate",
        ]
        if regime == "transitional":
            warnings.append("transitional regime (2300 < Re < 4000): friction factor is poorly defined")
        return Computed(
            values=values,
            primary="pressure_drop",
            warnings=warnings,
            data={
                "formula": "dp = f * (L/D) * rho * v^2 / 2",
                "friction_method": "fluids.friction.friction_factor(Method='Clamond'); laminar 64/Re",
                "regime": regime,
                "relative_roughness": eps_m / D_m,
            },
        )

    return run_tool(
        tool_name="calc_pipe_pressure_drop",
        engine="fluids",
        version=fluids.__version__,
        inputs=inputs,
        compute=compute,
        standing_warnings=(
            "Straight-pipe friction loss only; fittings, bends, entrance/exit and elevation are not included.",
        ),
    )


__all__ = ["ENGINE", "TOOL_NAMES", "cylinder_force", "pipe_pressure_drop", "server"]
