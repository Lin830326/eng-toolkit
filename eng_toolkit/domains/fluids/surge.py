"""Elastic water hammer and Courant-one frictionless characteristics."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Density, G, Kinematic, Result, Viscosity, choice, count, csv_text, fields, fluid, invalid, number, q, records, run
from ._server import server


@server.tool(description="Thin-wall elastic wave speed, Joukowsky surge, Michaud slow-closure estimate and reservoir-pipe-valve MOC CSV. Linear valve-area closure, friction neglected; Courant=1. D/e>=20; <=200 reaches, 20000 steps and 2 million reach-steps. No cavitation/column-separation model.")
def surge(
    length: Annotated[str, Field(description="Reservoir-to-valve pipe length, e.g. '1000 m'; required for estimates and simulation.")] = "",
    diameter: Annotated[str, Field(description="Pipe inside diameter, e.g. '0.5 m'; required in all modes.")] = "",
    wall_thickness: Annotated[str, Field(description="Elastic pipe wall thickness, e.g. '10 mm'; required, thickness/diameter<=0.05.")] = "",
    bulk_modulus: Annotated[str, Field(description="Fluid bulk modulus, e.g. '2.2 GPa'; required for elastic wave speed.")] = "",
    youngs_modulus: Annotated[str, Field(description="Pipe-wall Young's modulus, e.g. '200 GPa'; required for elastic compliance.")] = "",
    initial_velocity: Annotated[str, Field(description="Forward velocity before complete valve closure, e.g. '1 m/s'; required and positive.")] = "",
    closure_time: Annotated[str, Field(description="Time for linear valve-area closure, e.g. '1 s'; required; '0 s' means instantaneous closure.")] = "",
    anchoring: Annotated[str, Field(description="expansion_joints (c1=1), restrained (1-nu^2), or upstream_anchor (1-nu/2).")]= "restrained",
    poisson_ratio: Annotated[float, Field(description="Pipe-wall Poisson ratio nu, e.g. 0.3; all anchoring cases, 0..0.49.")] = .3,
    duration: Annotated[str, Field(description="MOC simulation duration, e.g. '5 s'; '' returns estimates only; actual final time rounds up by less than one step.")] = "",
    reservoir_head: Annotated[str, Field(description="Reservoir head above valve outlet, e.g. '100 m'; required for MOC, '' for estimates only; horizontal pipe.")] = "",
    reaches: Annotated[int, Field(description="Equal pipe reaches for MOC, 2..200; default 40; ignored for estimate-only calls.")] = 40,
    time_step: Annotated[str, Field(description="MOC time step, e.g. '0.01 s'; '' uses dx/a; supplied value must satisfy Courant=1 within 1e-8.")] = "",
    locations: Annotated[str, Field(description="MOC output positions, e.g. '0 m;500 m'; <=20 records, nearest grid point reported; '' selects 0,L/2,L.")] = "",
    density: Density = "1000 kg/m^3",
    dynamic_viscosity: Viscosity = "1 mPa*s",
    kinematic_viscosity: Kinematic = "",
):
    inputs = dict(locals())

    def compute(handle):
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        l = q(length, "length", "length")
        d = q(diameter, "diameter", "length")
        e = q(wall_thickness, "wall_thickness", "length")
        bulk = q(bulk_modulus, "bulk_modulus", "modulus")
        elastic = q(youngs_modulus, "youngs_modulus", "modulus")
        v0 = q(initial_velocity, "initial_velocity", "velocity")
        tc = q(closure_time, "closure_time", "time", zero=True)
        nu = number(poisson_ratio, "poisson_ratio", 0, .49)
        choice(anchoring, "anchoring", ("expansion_joints", "restrained", "upstream_anchor"))
        n = count(reaches, "reaches", 2, 200)
        if e/d > .05:
            invalid("wall_thickness", "thin-wall wave-speed formula requires thickness/diameter<=0.05")
        c1 = {"expansion_joints": 1., "restrained": 1-nu*nu, "upstream_anchor": 1-nu/2}[anchoring]
        wave = math.sqrt(bulk/rho/(1+bulk*d/(elastic*e)*c1))
        if v0/wave > .1:
            invalid("initial_velocity", "linear water-hammer model requires initial velocity/wave speed<=0.1")
        critical = 2*l/wave
        dp = rho*wave*v0
        r = Result({"elastic wave speed": "a=sqrt((K/rho)/(1+K*D*c1/(E*e)))",
                    "Joukowsky": "delta_p=rho*a*delta_v; critical_time=2L/a; reservoir-valve period=4L/a",
                    "Michaud": "delta_p=2*rho*L*delta_v/tc for tc>2L/a",
                    "MOC": "C+=H+BQ, C-=H-BQ, B=a/(gA); dt=dx/a; upstream H=Hres; Qvalve=tau*Q0*sqrt(Hvalve/Hres)"},
                   "Horizontal, uniform, thin-walled elastic pipe; constant reservoir head. Friction neglected. Valve area closes linearly; constant valve Cd.",
                   "D/e>=20, initial v/a<=0.1, positive fluid properties and elastic moduli. Single-phase small perturbations; no column separation.")
        r.add("wave_speed", wave, "m/s")
        r.add("joukowsky_pressure_rise", dp, "Pa")
        r.add("joukowsky_head_rise", wave*v0/G, "m")
        r.add("critical_closure_time", critical, "s")
        r.add("oscillation_period", 2*critical, "s")
        r.add("anchoring_factor", c1)
        r.data["initial_reynolds_number"] = rho*v0*d/mu
        if tc > critical:
            r.add("slow_closure_pressure_rise", 2*rho*l*v0/tc, "Pa")
            r.warnings.append("Michaud is a slow-closure estimate; linear valve area does not imply a linear velocity history.")
        if not duration:
            if reservoir_head or time_step or locations:
                invalid("duration", "MOC inputs require duration")
            return r.done("joukowsky_pressure_rise")
        end = q(duration, "duration", "time")
        h0 = q(reservoir_head, "reservoir_head", "length")
        dt = l/(n*wave)
        given_dt = q(time_step, "time_step", "time", optional=True)
        if given_dt is not None and abs(given_dt/dt-1) > 1e-8:
            invalid("time_step", "this MOC requires Courant=1: choose time_step=length/(reaches*wave_speed)")
        steps = math.ceil(end/dt)
        if steps > 20000 or steps*n > 2000000:
            invalid("duration", "MOC caps are 20000 time steps and 2000000 reach-steps", "input.out_of_range")
        if end < dt:
            invalid("duration", "duration must include at least one Courant time step")
        positions = []
        for i, row in enumerate(records(locations, "locations", 20)):
            fields(row, (1,), f"locations/{i}")
            x = q(row[0], f"locations/{i}", "length", zero=True)
            if x > l:
                invalid(f"locations/{i}", "position lies beyond pipe end")
            positions.append(x)
        positions = positions or [0., l/2, l]
        indices = sorted(set(round(x/l*n) for x in positions))
        a = math.pi*d*d/4
        q0 = a*v0
        b = wave/(G*a)
        heads, flows = np.full(n+1, h0), np.full(n+1, q0)
        rows = [[0., *[h0 for _ in indices]]]
        peak, minimum = h0, h0
        for k in range(1, steps+1):
            cp = heads[:-1]+b*flows[:-1]
            cm = heads[1:]-b*flows[1:]
            hn, qn = np.empty(n+1), np.empty(n+1)
            hn[1:n] = (cp[:-1]+cm[1:])/2
            qn[1:n] = (cp[:-1]-cm[1:])/(2*b)
            hn[0] = h0
            qn[0] = (h0-cm[0])/b
            tau = max(0., 1-k*dt/tc) if tc else 0.
            if tau == 0:
                hn[n], qn[n] = cp[-1], 0.
            else:
                if cp[-1] < 0:
                    invalid("reservoir_head", "negative valve head: this forward-flow valve model cannot continue")
                coefficient = b*tau*q0/math.sqrt(h0)
                root_h = 2*cp[-1]/(math.hypot(coefficient, 2*math.sqrt(cp[-1]))+coefficient)
                hn[n], qn[n] = root_h*root_h, tau*q0*root_h/math.sqrt(h0)
            if not np.all(np.isfinite(hn)) or not np.all(np.isfinite(qn)):
                invalid("duration", "MOC exceeded finite numerical range", "input.out_of_range")
            peak = max(peak, float(np.max(hn)))
            minimum = min(minimum, float(np.min(hn)))
            rows.append([k*dt, *[float(hn[j]) for j in indices]])
            heads, flows = hn, qn
        if minimum < 0:
            r.warnings.append("Negative gauge head occurs in the frictionless model; check absolute pressure and vapour pressure before use.")
        r.add("moc_peak_pressure_rise", rho*G*(peak-h0), "Pa")
        r.add("moc_minimum_head", minimum, "m")
        r.add("time_step", dt, "s")
        r.data.update({"time_steps": steps, "reaches": n, "courant": 1., "actual_duration_s": steps*dt,
                       "locations_m": [j*l/n for j in indices], "friction": "neglected", "valve_law": "linear effective area"})
        if tc == 0:
            r.data["instantaneous_comparison"] = {"moc_Pa": rho*G*(peak-h0), "joukowsky_Pa": dp, "ratio": rho*G*(peak-h0)/dp}
        r.artifacts.append(handle.write_text("surge.csv", csv_text(["time_s", *[f"head_m_at_{j*l/n:.9g}m" for j in indices]], rows), fmt="csv"))
        return r.done("moc_peak_pressure_rise")

    return run("surge", inputs, compute)
