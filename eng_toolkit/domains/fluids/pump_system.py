"""Piecewise-linear pump/efficiency curves and bounded system intersections."""

import math
from typing import Annotated

from pydantic import Field

from ._common import Density, G, Kinematic, Result, Viscosity, bisect, choice, count, csv_text, fluid, invalid, number, pipe_loss, q, run
from ._model import pump_curves
from ._server import server


def add_curve_table(handle, r, curves, combination, system, rho, rows_wanted, flow_op, head_op):
    """system_curve.csv: flow, system head, pump head, efficiency over 0 .. the end of the pump curve, the operating point inserted as a row.

    ``system(Q)`` is the head the system needs at flow Q, the pump side is the model's own pump curve (single, series: heads added at a common flow;
    parallel: flows added at a common head, then inverted).  The efficiency is hydraulic power over shaft power of the running pumps (0 at Q = 0).
    """
    if combination == "parallel":
        lo = max(c.heads[-1] for c in curves.values())
        hi = max(c.heads[0] for c in curves.values())

        def total_flow(h):
            return sum(c.flow(h) for c in curves.values())

        flow_end = total_flow(lo)

        def pump_state(flow):
            """(head, shaft power per rho*g) for the total ``flow``."""
            if flow <= 0:
                return hi, 0.
            h = bisect(lambda x: total_flow(x)-flow, lo, hi, "curve_points", rtol=1e-14, atol=0.)
            shaft = sum(c.flow(h)*h/c.efficiency(c.flow(h)) for c in curves.values() if c.flow(h) > 0)
            return h, shaft
    else:
        flow_end = min(c.flows[-1] for c in curves.values())

        def pump_state(flow):
            h = sum(c.head(flow) for c in curves.values())
            shaft = sum(flow*c.head(flow)/c.efficiency(flow) for c in curves.values()) if flow > 0 else 0.
            return h, shaft

    grid = [flow_end*i/(rows_wanted-1) for i in range(rows_wanted)]
    if all(abs(flow_op-g) > 1e-12*flow_end for g in grid):
        grid = sorted([*grid, flow_op])
    rows = []
    for flow in grid:
        pump_head, shaft = pump_state(flow)
        if shaft > 0:
            efficiency = flow*pump_head/shaft
        else:  # no flow, or zero delivered head at the end of the curve: the curve's own efficiency where one pump runs
            efficiency = next(iter(curves.values())).efficiency(flow) if flow > 0 and len(curves) == 1 else 0.
        rows.append([flow, system(flow), pump_head, efficiency, 1 if abs(flow-flow_op) <= 1e-12*flow_end else 0])
    r.artifacts.append(handle.write_text("system_curve.csv", csv_text(
        ["flow_m3_s", "system_head_m", "pump_head_m", "efficiency", "is_operating_point"], rows), fmt="csv"))
    hydraulic_over_rho_g = flow_op*head_op
    shaft_op = pump_state(flow_op)[1]
    r.data["operating_point"] = {"flow_m3_s": flow_op, "head_m": head_op, "efficiency": hydraulic_over_rho_g/shaft_op if shaft_op > 0 else 0.,
                                 "system_head_m": system(flow_op), "pump_head_m": pump_state(flow_op)[0]}
    r.data["curve_table"] = {"artifact": "system_curve.csv", "rows": len(rows), "flow_max_m3_s": flow_end,
                             "columns": ["flow_m3_s", "system_head_m", "pump_head_m", "efficiency", "is_operating_point"],
                             "note": "pump head and efficiency are the supplied curves (after speed and trim scaling), system head is static + friction; "
                                     "they cross at the operating point; efficiency is 0 at zero flow (and for several pumps at zero delivered head); one row is the operating point itself"}


@server.tool(description="Pump/system operating point from monotone head/efficiency point interpolation, Darcy-Weisbach or static+KQ^2 system, bracketed intersection. Series/parallel pumps, affinity scaling, NPSHa margin and dimensionless specific speed. No extrapolation; impeller trim ratio 0.9..1.0; constant-efficiency affinity approximation. curve_points writes system_curve.csv (flow, system head, pump head, efficiency) and data.operating_point.")
def pump_system(
    pump_points: Annotated[str, Field(description="id,Q,H,eta records, e.g. 'p,0 m^3/s,20 m,0;p,0.1 m^3/s,0 m,0.7'; eta 0..1 at Q=0, 0.01..1 above; <=120 points, <=8 pumps.")] = "",
    combination: Annotated[str, Field(description="single, series (sum heads at common flow), or parallel (sum flows at common head with check valves).")]= "single",
    static_head: Annotated[str, Field(description="System elevation/pressure head, e.g. '5 m'; required, signed; excludes friction.")] = "",
    system_coefficient: Annotated[str, Field(description="System K in H=Hstatic+KQ^2, e.g. '1000 s^2/m^5'; '' requires pipe geometry instead.")] = "",
    pipe_length: Annotated[str, Field(description="System pipe length, e.g. '100 m'; required without system_coefficient, otherwise ''.")] = "",
    pipe_diameter: Annotated[str, Field(description="System pipe inside diameter, e.g. '100 mm'; required with pipe_length, otherwise ''.")] = "",
    roughness: Annotated[str, Field(description="System pipe absolute roughness, e.g. '0.05 mm'; '' means smooth for pipe mode; omit with system_coefficient.")] = "",
    minor_loss_coefficient: Annotated[float, Field(description="System pipe fittings' total K, e.g. 2; pipe mode only, 0..1e6.")] = 0,
    speed_ratio: Annotated[float, Field(description="New/original pump speed ratio, e.g. 1.1; all curves, 0.2..3; Q scales with ratio, H with ratio squared.")] = 1,
    diameter_ratio: Annotated[float, Field(description="Trimmed/original impeller diameter ratio, e.g. 0.95; all curves, restricted to 0.9..1.0.")] = 1,
    rotational_speed: Annotated[str, Field(description="Actual operating angular speed, e.g. '1450 rpm'; '' omits dimensionless specific speed.")] = "",
    atmospheric_pressure: Annotated[str, Field(description="Absolute ambient pressure for NPSHa, e.g. '101325 Pa'; used when vapour_pressure is supplied.")] = "101325 Pa",
    surface_pressure: Annotated[str, Field(description="Suction vessel gauge pressure for NPSHa, e.g. '0 bar'; signed, used with vapour_pressure.")] = "0 Pa",
    vapour_pressure: Annotated[str, Field(description="Fluid absolute vapour pressure, e.g. '2.34 kPa'; '' omits NPSH check; requires suction_elevation.")] = "",
    suction_elevation: Annotated[str, Field(description="Liquid surface height above pump, e.g. '2 m'; signed, required for NPSH; '' omits with vapour_pressure.")] = "",
    suction_loss: Annotated[str, Field(description="Suction line head loss at operating flow, e.g. '0.5 m'; '' means zero for NPSH check.")] = "",
    npsh_required: Annotated[str, Field(description="Pump NPSHr at operating flow, e.g. '3 m'; '' reports NPSHa only; requires vapour_pressure.")] = "",
    density: Density = "1000 kg/m^3",
    dynamic_viscosity: Viscosity = "1 mPa*s",
    kinematic_viscosity: Kinematic = "",
    curve_points: Annotated[int, Field(description="0 (default) none; 2..500: rows of system_curve.csv over the pump's flow range, the operating point inserted as a row.")] = 0,
):
    inputs = dict(locals())

    def compute(handle):
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        choice(combination, "combination", ("single", "series", "parallel"))
        rows_wanted = 0 if (curve_points == 0 and curve_points is not False) else count(curve_points, "curve_points", 2, 500)
        speed = number(speed_ratio, "speed_ratio", .2, 3)
        trim = number(diameter_ratio, "diameter_ratio", .9, 1)
        curves = pump_curves(pump_points, speed, trim)
        if combination == "single" and len(curves) != 1:
            invalid("combination", "single requires exactly one pump id")
        static = q(static_head, "static_head", "length", signed=True)
        k = q(system_coefficient, "system_coefficient", "b", zero=True, optional=True)
        minor = number(minor_loss_coefficient, "minor_loss_coefficient", 0, 1e6)
        if k is not None:
            if pipe_length or pipe_diameter or roughness or minor:
                invalid("system_coefficient", "system coefficient and pipe geometry are mutually exclusive")
            def system(flow):
                return static+k*flow*flow
        else:
            l = q(pipe_length, "pipe_length", "length")
            d = q(pipe_diameter, "pipe_diameter", "length")
            rough = q(roughness or "0 m", "roughness", "length", zero=True)
            if rough/d > .05:
                invalid("roughness", "roughness/diameter must be <=0.05")
            def system(flow):
                return static+pipe_loss(flow, l, d, rough, minor, rho, mu)[0]
        r = Result({"operating point": "H_pump(Q)=H_static+K*Q^2, or H_static+(f(Re)*L/D+sumK)*v^2/(2g)",
                    "curves": "piecewise-linear head and efficiency interpolation; series sum H(Q); parallel sum Q(H)",
                    "affinity": "Q2/Q1=s*d; H2/H1=s^2*d^2; P2/P1=s^3*d^3 at unchanged efficiency",
                    "NPSH": "NPSHa=(p_atm+p_surface-p_v)/(rho*g)+z-h_suction; margin=NPSHa-NPSHr",
                    "specific speed": "Omega_s=omega*sqrt(Q)/(g*H)^(3/4), omega in rad/s"},
                   "Steady incompressible Newtonian fluid; monotone supplied pump curves, identical speed/trim ratios applied to each pump. Parallel check valves.",
                   "No curve extrapolation. Empirical affinity trim restricted to 0.9..1.0. Efficiencies held unchanged by affinity scaling; no cavitation model.")
        if combination == "parallel":
            lo = max(c.heads[-1] for c in curves.values())
            hi = max(c.heads[0] for c in curves.values())
            head_scale = max(hi, abs(static))
            flow_scale = sum(c.flows[-1] for c in curves.values())
            def total_flow(h):
                return sum(c.flow(h) for c in curves.values())
            def residual(x):
                h = lo+(hi-lo)*x
                return h/head_scale-system(total_flow(h))/head_scale
            fraction = bisect(residual, 0., 1., "pump_points", rtol=2e-14, atol=0.)
            head = lo+(hi-lo)*fraction
            flows = {name: c.flow(head) for name, c in curves.items()}
            flow = sum(flows.values())
            heads = {name: head for name in curves}
        else:
            hi = min(c.flows[-1] for c in curves.values())
            flow_scale = hi
            head_scale = max(sum(c.heads[0] for c in curves.values()), abs(static))
            def total_head(flow):
                return sum(c.head(flow) for c in curves.values())
            fraction = bisect(lambda x: total_head(x*flow_scale)/head_scale-system(x*flow_scale)/head_scale,
                              0., 1., "pump_points", rtol=2e-14, atol=0.)
            flow = fraction*flow_scale
            head = total_head(flow)
            flows = {name: flow for name in curves}
            heads = {name: c.head(flow) for name, c in curves.items()}
        if flow <= 0 or head <= 0:
            invalid("pump_points", "intersection must have positive flow and positive delivered head")
        residuals = [abs(head-system(flow))]
        if combination == "parallel":
            residuals.extend(abs(c.head(flows[name])-head) for name, c in curves.items() if flows[name] > 0)
        else:
            residuals.append(abs(head-sum(c.head(flow) for c in curves.values())))
        if max(residuals) > 1e-9*head_scale:
            invalid("pump_points", "operating point cannot resolve both curves at their head scale", "input.out_of_range")
        r.data["head_residual_m"] = max(residuals)
        r.data["head_tolerance_m"] = 1e-9*head_scale
        r.data["solver_scales"] = {"flow_m3_s": flow_scale, "head_m": head_scale}
        shaft = sum(rho*G*flows[name]*heads[name]/c.efficiency(flows[name]) for name, c in curves.items() if flows[name] > 0)  # a closed pump has no hydraulic power, and its shut-off efficiency may be 0
        hydraulic = rho*G*flow*head
        r.add("operating_flow", flow, "m^3/s")
        r.add("operating_head", head, "m")
        r.add("hydraulic_power", hydraulic, "W")
        r.add("shaft_power", shaft, "W")
        r.add("efficiency", hydraulic/shaft)
        if rows_wanted:
            add_curve_table(handle, r, curves, combination, system, rho, rows_wanted, flow, head)
        r.data["pumps"] = [{"id": name, "flow_m3_s": flows[name], "head_m": heads[name],
                             "efficiency": c.efficiency(flows[name]), "check_valve_closed": flows[name] == 0} for name, c in curves.items()]
        r.data["affinity_factors"] = {"flow": speed*trim, "head": (speed*trim)**2, "power": (speed*trim)**3}
        if k is None:
            loss, velocity, re, f = pipe_loss(flow, l, d, rough, minor, rho, mu)
            r.data["system_pipe"] = {"headloss_m": loss, "velocity_m_s": velocity, "reynolds_number": re, "darcy_factor": f}
            if 2000 <= re < 4000:
                r.warnings.append("System pipe uses the documented smooth laminar/Colebrook blend in transition.")
        omega = q(rotational_speed, "rotational_speed", "frequency", optional=True)
        if omega is not None:
            r.add("specific_speed", omega*math.sqrt(flow)/(G*head)**.75)
            r.data["specific_speed_basis"] = "Aggregate operating Q,H and supplied actual angular speed; for series stages evaluate each stage separately."
        patm = q(atmospheric_pressure, "atmospheric_pressure", "pressure")
        psurface = q(surface_pressure, "surface_pressure", "pressure", signed=True)
        if vapour_pressure or suction_elevation or suction_loss or npsh_required:
            pv = q(vapour_pressure, "vapour_pressure", "pressure", zero=True)
            z = q(suction_elevation, "suction_elevation", "length", signed=True)
            loss = q(suction_loss or "0 m", "suction_loss", "length", zero=True)
            if patm+psurface <= 0:
                invalid("surface_pressure", "vessel absolute pressure must be positive")
            available = (patm+psurface-pv)/(rho*G)+z-loss
            r.add("npsh_available", available, "m")
            required = q(npsh_required, "npsh_required", "length", zero=True, optional=True)
            if required is not None:
                r.add("npsh_margin", available-required, "m")
                if available <= required:
                    r.warnings.append("NPSHa does not exceed NPSHr: cavitation risk; no additional design margin has been applied.")
            if available < 0:
                r.warnings.append("Negative NPSHa: liquid may flash; single-phase pump estimate is not applicable.")
        return r.done("operating_flow")

    return run("pump_system", inputs, compute)
