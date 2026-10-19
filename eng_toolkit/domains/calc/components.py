"""``calc`` component-sizing tools: orifice flow, pump power, accumulator pre-charge, cylinder speed.

Same conventions as :mod:`eng_toolkit.domains.calc.hydraulics`: every physical input carries a unit, results use the unified
contract, and the formulas are textbook closed forms (no tables).  They register on the one ``calc`` server.

Formulas (all named after the usual textbook form; none is copied from a standard's text or tables)
* orifice: the orifice equation ``Q = Cd * A * sqrt(2 dp / rho)`` (Bernoulli with a discharge coefficient)
* pump: hydraulic power ``P = Q dp``; shaft power ``P / eta``; torque ``P / omega``
* accumulator: Boyle's law for ``n = 1`` and the polytropic law ``p V^n = const`` otherwise
* cylinder: continuity, ``v = Q / A``
"""

from __future__ import annotations

import math
from typing import Annotated

import pint
from pydantic import Field

from eng_toolkit.contract import QuantityOut, ToolResult
from eng_toolkit import __version__
from eng_toolkit.domains.calc._common import (
    bounded,
    is_blank,
    plain_number,
    positive_quantity,
    revolutions_per_second,
)
from eng_toolkit.domains.calc.hydraulics import ENGINE, QuantityIn, server
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, to_quantity_out, ureg

TOOL_NAMES: tuple[str, ...] = ("orifice_flow", "pump_power", "accumulator_precharge", "cylinder_speed")

# Standard atmosphere (a definition, 101 325 Pa), used only to turn gauge pressures into absolute ones.
_P_ATM_PA = 101_325.0
_G = 9.80665


def _out(qty, unit: str, *, param: str, uncertainty=None) -> QuantityOut:
    """``to_quantity_out`` that reports a wrong output unit as a unit error instead of an engine failure."""
    try:
        return to_quantity_out(qty, unit, uncertainty=uncertainty)
    except pint.errors.DimensionalityError as exc:
        raise UnitsError(
            "units.dimension_mismatch",
            f"'{param}' = {unit!r} cannot express this result (expected {qty.dimensionality})",
            {"param": param, "received": unit},
        ) from exc
    except (pint.errors.UndefinedUnitError, pint.errors.DefinitionSyntaxError, ValueError, AttributeError) as exc:
        raise UnitsError(
            "units.parse_error", f"'{param}': unknown unit {unit!r} ({exc})", {"param": param, "received": unit}
        ) from exc


# --------------------------------------------------------------------------- orifice flow


@server.tool(
    name="orifice_flow",
    description=(
        "Flow through a sharp-edged orifice or a fixed throttle (orifice equation Q = Cd*A*sqrt(2*dp/rho)). "
        "Give the pressure drop to get the flow, or the flow to get the pressure drop (exactly one of the two). "
        "Incompressible liquid, turbulent flow; Cd is the discharge coefficient (about 0.6 for a sharp edge)."
    ),
)
def orifice_flow(
    diameter: Annotated[QuantityIn, Field(description="Orifice (throat) diameter with unit, e.g. '5 mm'")],
    density: Annotated[QuantityIn, Field(description="Fluid density with unit, e.g. '870 kg/m^3'")],
    pressure_drop: Annotated[
        str, Field(description="Pressure drop across the orifice, e.g. '100 bar'. Give this OR flow_rate, not both.")
    ] = "",
    flow_rate: Annotated[
        str, Field(description="Volumetric flow, e.g. '40 L/min'. Give this OR pressure_drop, not both.")
    ] = "",
    discharge_coeff: Annotated[
        float, Field(description="Discharge coefficient Cd, 0 < Cd <= 1. Sharp-edged orifice about 0.6-0.62.")
    ] = 0.62,
    viscosity: Annotated[
        str,
        Field(
            description="Optional DYNAMIC viscosity, e.g. '40 cP'. Only used to report the orifice Reynolds number "
            "and warn when it is too low for a constant Cd. Empty = skip."
        ),
    ] = "",
    flow_unit: Annotated[str, Field(description="Unit for the flow result, e.g. 'L/min', 'm^3/s', 'gal/min'")] = "L/min",
    pressure_unit: Annotated[str, Field(description="Unit for the pressure-drop result, e.g. 'bar', 'MPa', 'psi'")] = "bar",
) -> ToolResult:
    inputs = {
        "diameter": diameter,
        "density": density,
        "pressure_drop": pressure_drop,
        "flow_rate": flow_rate,
        "discharge_coeff": discharge_coeff,
        "viscosity": viscosity,
        "flow_unit": flow_unit,
        "pressure_unit": pressure_unit,
    }

    def compute(_: JobHandle) -> Computed:
        have_dp, have_q = not is_blank(pressure_drop), not is_blank(flow_rate)
        if have_dp == have_q:
            raise UnitsError(
                "input.missing" if not have_dp else "input.invalid",
                "give exactly one of 'pressure_drop' (to compute the flow) or 'flow_rate' (to compute the pressure drop)",
                {"param": "pressure_drop|flow_rate", "pressure_drop": pressure_drop, "flow_rate": flow_rate},
            )
        d = positive_quantity(diameter, name="diameter", dimension="length", unit="mm", lo=0.01, hi=1000.0)
        rho = positive_quantity(density, name="density", dimension="density", unit="kg/m**3", lo=1.0, hi=25_000.0)
        cd = plain_number(discharge_coeff, name="discharge_coeff", lo=0.0, hi=1.0, lo_open=True)
        mu = None
        if not is_blank(viscosity):
            mu = positive_quantity(
                viscosity, name="viscosity", dimension="dynamic_viscosity", unit="Pa*s", lo=1e-6, hi=1e3
            )

        d_m = float(d.to("m").magnitude)
        rho_si = float(rho.to("kg/m**3").magnitude)
        area = math.pi / 4.0 * d_m**2
        if have_dp:
            dp_q = positive_quantity(
                pressure_drop, name="pressure_drop", dimension="pressure", unit="Pa", lo=1.0, hi=1.0e8
            )
            dp_pa = float(dp_q.to("Pa").magnitude)
            q_si = cd * area * math.sqrt(2.0 * dp_pa / rho_si)
            primary = "flow_rate"
        else:
            q_q = positive_quantity(
                flow_rate, name="flow_rate", dimension="volumetric_flow", unit="m**3/s", lo=1e-12, hi=100.0
            )
            q_si = float(q_q.to("m**3/s").magnitude)
            dp_pa = rho_si / 2.0 * (q_si / (cd * area)) ** 2
            bounded(ureg.Quantity(dp_pa, "Pa"), name="pressure_drop (result)", unit="Pa", lo=1.0, hi=1.0e8)
            primary = "pressure_drop"
        v = q_si / area  # mean velocity over the orifice area (not over the vena contracta)

        values = {
            "flow_rate": _out(ureg.Quantity(q_si, "m**3/s"), flow_unit, param="flow_unit"),
            "pressure_drop": _out(ureg.Quantity(dp_pa, "Pa"), pressure_unit, param="pressure_unit"),
            "orifice_velocity": QuantityOut(quantity=v, unit="meter / second"),
            "orifice_area": to_quantity_out(ureg.Quantity(area, "m**2"), "mm**2"),
        }
        warnings: list[str] = []
        re_d = None
        if mu is not None:
            re_d = 4.0 * rho_si * q_si / (math.pi * d_m * float(mu.to("Pa*s").magnitude))
            values["reynolds_orifice"] = QuantityOut(quantity=re_d, unit="dimensionless")
            if re_d < 1.0e4:
                warnings.append(
                    f"orifice Reynolds number {re_d:.0f} is below about 10^4: Cd of a sharp-edged orifice is only "
                    "roughly constant at high Reynolds number and falls with Re (viscous regime), so the flow is "
                    "overestimated; use a measured Cd"
                )
        else:
            warnings.append("viscosity not given: the Reynolds-number validity of a constant Cd was not checked")
        if not 0.5 <= cd <= 0.9:
            warnings.append(f"Cd = {cd:g} is unusual (sharp edge ~0.6-0.65, well-rounded inlet up to ~0.9); check the source")
        if rho_si < 200.0:
            warnings.append(
                f"density {rho_si:g} kg/m^3 looks like a gas: the incompressible equation is invalid when the "
                "pressure drop is more than a few percent of the absolute pressure"
            )
        return Computed(
            values=values,
            primary=primary,
            warnings=warnings,
            data={
                "formula": "Q = Cd * A * sqrt(2 dp / rho) ; dp = rho/2 * (Q / (Cd A))^2",
                "solved_for": "flow_rate" if have_dp else "pressure_drop",
                "discharge_coeff": cd,
            },
        )

    return run_tool(
        tool_name="calc_orifice_flow",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Orifice equation for an incompressible liquid with a user-supplied Cd; cavitation, compressibility and "
            "pipe-to-orifice diameter ratio (velocity of approach) are not modelled.",
        ),
    )


# --------------------------------------------------------------------------- pump power


@server.tool(
    name="pump_power",
    description=(
        "Hydraulic (output) power Q*dp and shaft (input) power Q*dp/eta of a pump, with optional shaft torque from "
        "the speed and the heat loss. 'efficiency' is the OVERALL efficiency (volumetric x hydro-mechanical), "
        "a fraction between 0 and 1, and 'flow_rate' is the delivered flow."
    ),
)
def pump_power(
    flow_rate: Annotated[QuantityIn, Field(description="Delivered volumetric flow with unit, e.g. '60 L/min'")],
    pressure_rise: Annotated[
        QuantityIn, Field(description="Pressure rise across the pump (outlet minus inlet) with unit, e.g. '210 bar'")
    ],
    efficiency: Annotated[
        float, Field(description="Overall efficiency as a fraction, 0 < eta <= 1 (0.85, not 85)")
    ],
    speed: Annotated[
        str,
        Field(
            description="Optional shaft speed, e.g. '1500 rpm' (a bare '25 Hz' counts as 25 revolutions per second). "
            "Given: the shaft torque is also returned. Empty = skip."
        ),
    ] = "",
    power_unit: Annotated[str, Field(description="Unit for the powers, e.g. 'kW', 'W', 'hp'")] = "kW",
) -> ToolResult:
    inputs = {
        "flow_rate": flow_rate,
        "pressure_rise": pressure_rise,
        "efficiency": efficiency,
        "speed": speed,
        "power_unit": power_unit,
    }

    def compute(_: JobHandle) -> Computed:
        q = positive_quantity(flow_rate, name="flow_rate", dimension="volumetric_flow", unit="m**3/s", lo=1e-9, hi=10.0)
        dp = positive_quantity(pressure_rise, name="pressure_rise", dimension="pressure", unit="bar", lo=1e-3, hi=1500.0)
        eta = plain_number(efficiency, name="efficiency", lo=0.0, hi=1.0, lo_open=True, hint="a fraction, e.g. 0.85 not 85")
        p_hyd = float(q.to("m**3/s").magnitude) * float(dp.to("Pa").magnitude)
        p_shaft = p_hyd / eta
        values = {
            "shaft_power": _out(ureg.Quantity(p_shaft, "W"), power_unit, param="power_unit"),
            "hydraulic_power": _out(ureg.Quantity(p_hyd, "W"), power_unit, param="power_unit"),
            "power_loss": _out(ureg.Quantity(p_shaft - p_hyd, "W"), power_unit, param="power_unit"),
        }
        warnings: list[str] = []
        data = {"formula": "P_hyd = Q * dp ; P_shaft = P_hyd / eta ; T = P_shaft / omega", "efficiency": eta}
        if not is_blank(speed):
            revs_per_s = revolutions_per_second(speed, name="speed", lo_rpm=1.0, hi_rpm=1.0e5)
            omega = 2.0 * math.pi * revs_per_s
            values["shaft_torque"] = _out(ureg.Quantity(p_shaft / omega, "N*m"), "N*m", param="shaft_torque")
            data["speed_rpm"] = revs_per_s * 60.0
        if eta > 0.95:
            warnings.append(f"overall efficiency {eta:g} is higher than any hydraulic pump reaches in practice (typically 0.75-0.92)")
        elif eta < 0.3:
            warnings.append(f"overall efficiency {eta:g} is very low; check that it is a fraction and not a percentage")
        if float(dp.to("bar").magnitude) > 450.0:
            warnings.append("pressure rise above 450 bar is outside normal industrial pump ratings; check the unit")
        return Computed(values=values, primary="shaft_power", warnings=warnings, data=data)

    return run_tool(
        tool_name="calc_pump_power",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Steady operating point with a single overall efficiency; efficiency varies with pressure, speed and "
            "viscosity, and the drive motor's efficiency is not included.",
        ),
    )


# --------------------------------------------------------------------------- accumulator


@server.tool(
    name="accumulator_precharge",
    description=(
        "Size a gas-charged hydraulic accumulator (bladder, piston, diaphragm): the nominal gas volume V0 that delivers "
        "a given usable fluid volume between a minimum and a maximum working pressure, from the gas law "
        "p*V^n = constant (n = 1.0 isothermal / slow, 1.4 adiabatic / fast discharge). The pre-charge pressure is "
        "given, or defaults to 0.9 x the minimum pressure."
    ),
)
def accumulator_precharge(
    usable_volume: Annotated[QuantityIn, Field(description="Fluid volume to deliver between the two pressures, e.g. '5 L'")],
    min_pressure: Annotated[
        QuantityIn, Field(description="Lowest working pressure the circuit still needs, e.g. '100 bar'")
    ],
    max_pressure: Annotated[QuantityIn, Field(description="Highest (system) pressure, e.g. '200 bar'")],
    precharge_pressure: Annotated[
        str, Field(description="Gas pre-charge pressure, e.g. '90 bar'. Empty = 0.9 x min_pressure (a common rule of thumb).")
    ] = "",
    exponent: Annotated[
        float, Field(description="Polytropic exponent n, 1.0 to 1.67: 1.0 slow (isothermal), 1.4 fast (adiabatic) nitrogen")
    ] = 1.4,
    pressures_are_gauge: Annotated[
        bool, Field(description="true: the three pressures are gauge (bar g); false: they are absolute")
    ] = True,
    volume_unit: Annotated[str, Field(description="Unit for the volumes, e.g. 'L', 'm^3', 'gal'")] = "L",
) -> ToolResult:
    inputs = {
        "usable_volume": usable_volume,
        "min_pressure": min_pressure,
        "max_pressure": max_pressure,
        "precharge_pressure": precharge_pressure,
        "exponent": exponent,
        "pressures_are_gauge": pressures_are_gauge,
        "volume_unit": volume_unit,
    }

    def compute(_: JobHandle) -> Computed:
        dv = positive_quantity(usable_volume, name="usable_volume", dimension="volume", unit="L", lo=1e-3, hi=1.0e5)
        p1_in = positive_quantity(min_pressure, name="min_pressure", dimension="pressure", unit="bar", lo=1e-2, hi=1500.0)
        p2_in = positive_quantity(max_pressure, name="max_pressure", dimension="pressure", unit="bar", lo=1e-2, hi=1500.0)
        n = plain_number(exponent, name="exponent", lo=1.0, hi=1.67)
        if not isinstance(pressures_are_gauge, bool):
            raise UnitsError("input.invalid", "'pressures_are_gauge' must be true or false", {"param": "pressures_are_gauge"})
        offset = _P_ATM_PA if pressures_are_gauge else 0.0
        p1_in_pa = float(p1_in.to("Pa").magnitude)
        p2_in_pa = float(p2_in.to("Pa").magnitude)
        if p2_in_pa <= p1_in_pa:
            raise UnitsError(
                "input.invalid",
                f"'max_pressure' ({p2_in:~P}) must be greater than 'min_pressure' ({p1_in:~P})",
                {"param": "max_pressure", "min_pressure": str(p1_in), "max_pressure": str(p2_in)},
            )
        defaulted = is_blank(precharge_pressure)
        if defaulted:
            p0_in_pa = 0.9 * p1_in_pa
        else:
            p0_q = positive_quantity(
                precharge_pressure, name="precharge_pressure", dimension="pressure", unit="bar", lo=1e-2, hi=1500.0
            )
            p0_in_pa = float(p0_q.to("Pa").magnitude)
        p0, p1, p2 = p0_in_pa + offset, p1_in_pa + offset, p2_in_pa + offset  # absolute, Pa
        if p0 >= p1:
            raise UnitsError(
                "input.invalid",
                f"pre-charge ({p0 / 1e5:g} bar abs) must be below the minimum working pressure ({p1 / 1e5:g} bar abs), "
                "otherwise the accumulator holds no fluid at that pressure and delivers nothing",
                {"param": "precharge_pressure", "precharge_abs_pa": p0, "min_pressure_abs_pa": p1},
            )
        # Gas volumes from p V^n = const: V_i = V0 (p0/p_i)^(1/n); delivered = V1 - V2.
        dv_l = float(dv.to("L").magnitude)
        r1 = (p0 / p1) ** (1.0 / n)
        r2 = (p0 / p2) ** (1.0 / n)
        v0 = dv_l / (r1 - r2)
        v1, v2 = v0 * r1, v0 * r2
        def vol(x: float, param: str) -> QuantityOut:
            return _out(ureg.Quantity(x, "L"), volume_unit, param=param)

        values = {
            "gas_volume": vol(v0, "volume_unit"),
            "gas_volume_at_min_pressure": vol(v1, "volume_unit"),
            "gas_volume_at_max_pressure": vol(v2, "volume_unit"),
            "precharge_pressure_gauge": QuantityOut(quantity=(p0 - _P_ATM_PA) / 1e5, unit="bar"),
            "precharge_pressure_absolute": QuantityOut(quantity=p0 / 1e5, unit="bar"),
            "pressure_ratio": QuantityOut(quantity=p2 / p0, unit="dimensionless"),
        }
        warnings: list[str] = []
        if defaulted:
            warnings.append(
                "pre-charge not given: assumed 0.9 x min_pressure (rule of thumb used by accumulator makers, "
                "not a standard); give the value from the accumulator's data sheet"
            )
        elif p0_in_pa > 0.9 * p1_in_pa:
            warnings.append(
                f"pre-charge is {p0_in_pa / p1_in_pa:.2f} x the minimum pressure; makers usually advise at most 0.9 x, "
                "otherwise the bladder or piston sits against the fluid port at minimum pressure"
            )
        if p2 / p0 > 4.0:
            warnings.append(
                f"pressure ratio p_max/pre-charge = {p2 / p0:.2f} exceeds about 4, a typical limit for bladder "
                "accumulators; check the data sheet (piston types allow more)"
            )
        if pressures_are_gauge and p1_in_pa < 1.0e5:
            warnings.append("minimum gauge pressure below 1 bar: check that the pressures are gauge, not absolute")
        return Computed(
            values=values,
            primary="gas_volume",
            warnings=warnings,
            data={
                "formula": "V0 = dV / ((p0/p1)^(1/n) - (p0/p2)^(1/n)) with absolute pressures",
                "exponent": n,
                "pressures_are_gauge": pressures_are_gauge,
                "atmosphere_pa": offset,
            },
        )

    return run_tool(
        tool_name="calc_accumulator_precharge",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Ideal gas, no temperature change between charge and operation, no fluid compressibility. Pick the next "
            "larger standard accumulator size and verify the pre-charge at the operating temperature.",
        ),
    )


# --------------------------------------------------------------------------- cylinder speed


@server.tool(
    name="cylinder_speed",
    description=(
        "Extend and retract speed of a single-rod hydraulic cylinder from the oil flow into it (v = Q/A), with the "
        "stroke time if the stroke is given and the oil flow returning from the other side (to size the return line "
        "and valves)."
    ),
)
def cylinder_speed(
    bore: Annotated[QuantityIn, Field(description="Cylinder bore (piston) diameter with unit, e.g. '100 mm'")],
    flow_rate: Annotated[QuantityIn, Field(description="Oil flow fed to the cylinder with unit, e.g. '60 L/min'")],
    rod: Annotated[
        str, Field(description="Rod diameter with unit, e.g. '50 mm'. Empty string = no rod (retract area = bore area).")
    ] = "",
    stroke: Annotated[str, Field(description="Optional stroke length, e.g. '500 mm'. Given: stroke times are also returned.")] = "",
    speed_unit: Annotated[str, Field(description="Unit for the speeds, e.g. 'mm/s', 'm/s', 'in/s'")] = "mm/s",
) -> ToolResult:
    inputs = {"bore": bore, "flow_rate": flow_rate, "rod": rod, "stroke": stroke, "speed_unit": speed_unit}

    def compute(_: JobHandle) -> Computed:
        d = positive_quantity(bore, name="bore", dimension="length", unit="mm", lo=1.0, hi=2000.0)
        q = positive_quantity(flow_rate, name="flow_rate", dimension="volumetric_flow", unit="m**3/s", lo=1e-9, hi=10.0)
        has_rod = not is_blank(rod)
        if has_rod:
            r = parse_quantity(rod, name="rod", dimension="length")
            if float(r.magnitude) <= 0:
                raise UnitsError("input.invalid", f"'rod' must be > 0, got {r:~P} (leave it empty for no rod)", {"param": "rod"})
            bounded(r, name="rod", unit="mm", lo=0.5, hi=2000.0)
        else:
            r = ureg.Quantity(0.0, "mm")
        d_m, r_m = float(d.to("m").magnitude), float(r.to("m").magnitude)
        if r_m >= d_m:
            raise UnitsError(
                "input.invalid",
                f"rod diameter ({r:~P}) must be smaller than bore ({d:~P})",
                {"param": "rod", "bore": str(d), "rod": str(r)},
            )
        q_si = float(q.to("m**3/s").magnitude)
        a_bore = math.pi / 4.0 * d_m**2
        a_ann = math.pi / 4.0 * (d_m**2 - r_m**2)
        v_ext, v_ret = q_si / a_bore, q_si / a_ann
        values = {
            "extend_speed": _out(ureg.Quantity(v_ext, "m/s"), speed_unit, param="speed_unit"),
            "retract_speed": _out(ureg.Quantity(v_ret, "m/s"), speed_unit, param="speed_unit"),
            # Oil leaving the opposite chamber (continuity): extend pushes a_ann/a_bore of the inflow out of the rod side.
            "return_flow_extend": to_quantity_out(ureg.Quantity(q_si * a_ann / a_bore, "m**3/s"), "L/min"),
            "return_flow_retract": to_quantity_out(ureg.Quantity(q_si * a_bore / a_ann, "m**3/s"), "L/min"),
        }
        warnings: list[str] = []
        if not has_rod:
            warnings.append("rod not given: retract speed and return flows assume rod diameter 0 (retract area = bore area)")
        if not is_blank(stroke):
            s = positive_quantity(stroke, name="stroke", dimension="length", unit="mm", lo=0.1, hi=1.0e5)
            s_m = float(s.to("m").magnitude)
            values["extend_time"] = QuantityOut(quantity=s_m / v_ext, unit="second")
            values["retract_time"] = QuantityOut(quantity=s_m / v_ret, unit="second")
        if max(v_ext, v_ret) > 0.5:
            warnings.append(
                f"speed {max(v_ext, v_ret):.2f} m/s is above about 0.5 m/s: check end-of-stroke cushioning, seal and "
                "rod-bearing ratings and the supplier's maximum piston speed"
            )
        if a_bore / a_ann > 1.6:
            warnings.append(
                f"area ratio {a_bore / a_ann:.2f}: while extending, the rod side returns {a_ann / a_bore:.0%} of the "
                f"inflow, but while retracting the bore side returns {a_bore / a_ann:.2f} x the inflow "
                f"({q_si * a_bore / a_ann * 60_000.0:.0f} L/min); size the return line and valve for it"
            )
        return Computed(
            values=values,
            primary="extend_speed",
            warnings=warnings,
            data={"formula": "v = Q / A ; A_extend = pi/4 d^2 ; A_retract = pi/4 (d^2 - d_rod^2)", "area_ratio": a_bore / a_ann},
        )

    return run_tool(
        tool_name="calc_cylinder_speed",
        engine=ENGINE,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("pint",),
        standing_warnings=(
            "Continuity only: cylinder and valve leakage, oil compressibility, load and acceleration/deceleration "
            "phases are not included.",
        ),
    )


__all__ = ["TOOL_NAMES", "accumulator_precharge", "cylinder_speed", "orifice_flow", "pump_power"]
