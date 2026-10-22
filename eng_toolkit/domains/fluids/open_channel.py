"""Prismatic section geometry, depth branches and standard-step profiles."""

import math
from dataclasses import dataclass
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Density, G, Kinematic, Result, Viscosity, bisect, choice, count, csv_text, fluid, invalid, maximum, number, q, run
from ._server import server

_GAUSS_X, _GAUSS_W = np.polynomial.legendre.leggauss(24)
_PROFILE_REFINEMENTS = 12
_PROFILE_WORK_CAP = 500


@dataclass(frozen=True)
class Section:
    shape: str
    width: float
    side: float
    diameter: float

    @property
    def limits(self):
        scale = self.diameter if self.shape == "circular" else max(self.width, 1.)
        return max(1e-10, scale*1e-8), self.diameter*(1-1e-10) if self.shape == "circular" else 1e6

    def geometry(self, y):
        if y <= 0 or (self.shape == "circular" and y >= self.diameter):
            invalid("depth", "depth must be positive and strictly below the circular soffit")
        if self.shape == "circular":
            theta = 4*math.asin(math.sqrt(y/self.diameter))
            if theta < .1:
                difference = theta**3*(1/6-theta**2/120+theta**4/5040-theta**6/362880)
            else:
                difference = theta-math.sin(theta)
            a = self.diameter**2/8*difference
            p = self.diameter*theta/2
            t = 2*math.sqrt(y*(self.diameter-y))
        else:
            a = y*(self.width+self.side*y)
            p = self.width+2*y*math.sqrt(1+self.side*self.side)
            t = self.width+2*self.side*y
        return a, p, t

    def pressure_moment(self, y):
        if self.shape != "circular":
            return self.width*y*y/2+self.side*y**3/3
        if y/self.diameter < .01:
            # Integral of area over depth avoids the cancellation in the segment centroid formula.
            return y/2*sum(w*self.geometry(y*(x+1)/2)[0] for x, w in zip(_GAUSS_X, _GAUSS_W))
        a = self.geometry(y)[0]
        return (y-self.diameter/2)*a+2/3*(y*(self.diameter-y))**1.5


def classification(slope, normal, critical, depth):
    if slope == 0:
        return "H2" if depth > critical else "H3"
    if slope < 0:
        return "A2" if depth > critical else "A3"
    if abs(normal-critical) <= 1e-6*critical:
        return "C1" if depth > critical else "C3"
    if normal > critical:
        return "M1" if depth > normal else ("M2" if depth > critical else "M3")
    return "S1" if depth > critical else ("S2" if depth > normal else "S3")


@server.tool(description="Rectangular/trapezoidal/triangular/part-full circular Manning flow, normal/critical/alternate depths, Froude regime, Belanger or momentum jump, standard-step GVF CSV. SI Manning conversion, bracketed roots; <=500 profile steps. Circular dual normal-depth branches explicit; no pressurized or transcritical profile.")
def open_channel(
    shape: Annotated[str, Field(description="rectangular, trapezoidal, triangular, or circular; prismatic section throughout.")] = "rectangular",
    mode: Annotated[str, Field(description="analysis (given discharge), uniform (compute discharge at depth), or profile (given boundary depth and discharge).")]= "analysis",
    width: Annotated[str, Field(description="Channel bottom width, e.g. '2 m'; required for rectangular/trapezoidal, '' for triangular/circular.")] = "",
    side_slope: Annotated[float, Field(description="Horizontal/vertical bank slope z, e.g. 1; positive for triangular/trapezoidal; 0 for rectangular/circular.")] = 0,
    diameter: Annotated[str, Field(description="Circular inside diameter, e.g. '1 m'; required only for circular, '' for open polygonal sections.")] = "",
    manning_n: Annotated[float, Field(description="Conventional Manning roughness n, e.g. 0.015; 0.005..0.2; same tabulated value for SI and imperial inputs.")] = .015,
    bed_slope: Annotated[float, Field(description="Bed fall per horizontal length, e.g. 0.001; -0.1..0.1; positive required for uniform/normal-depth calculation.")] = .001,
    discharge: Annotated[str, Field(description="Positive channel discharge, e.g. '2 m^3/s'; required for analysis/profile; '' for uniform mode.")] = "",
    depth: Annotated[str, Field(description="Flow depth, e.g. '0.5 m'; required for uniform; '' uses normal depth in analysis; omit for profile.")] = "",
    normal_branch: Annotated[str, Field(description="lower or upper circular normal-depth root; lower is default; upper requires discharge above full-section capacity.")] = "lower",
    boundary_depth: Annotated[str, Field(description="Known profile boundary depth, e.g. '1 m'; profile only; downstream for subcritical, upstream for supercritical; '' omits.")] = "",
    profile_length: Annotated[str, Field(description="Positive reach length, e.g. '100 m'; required for profile; '' omits in other modes.")] = "",
    profile_steps: Annotated[int, Field(description="Requested profile subdivisions, 1..500; adaptive bisection adds up to 12 levels, with 500 total step attempts; actual resolution reported.")] = 50,
    density: Density = "1000 kg/m^3",
    dynamic_viscosity: Viscosity = "1 mPa*s",
    kinematic_viscosity: Kinematic = "",
):
    inputs = dict(locals())

    def compute(handle):
        choice(shape, "shape", ("rectangular", "trapezoidal", "triangular", "circular"))
        choice(mode, "mode", ("analysis", "uniform", "profile"))
        choice(normal_branch, "normal_branch", ("lower", "upper"))
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        n = number(manning_n, "manning_n", .005, .2)
        slope = number(bed_slope, "bed_slope", -.1, .1)
        side = number(side_slope, "side_slope", 0, 20)
        steps = count(profile_steps, "profile_steps", 1, 500)
        b, d = 0., 0.
        if shape in ("rectangular", "trapezoidal"):
            b = q(width, "width", "length")
        elif width:
            invalid("width", "width does not apply to triangular/circular sections")
        if shape == "circular":
            d = q(diameter, "diameter", "length")
        elif diameter:
            invalid("diameter", "diameter only applies to circular sections")
        if (shape in ("rectangular", "circular") and side != 0) or (shape in ("triangular", "trapezoidal") and side == 0):
            invalid("side_slope", "positive side slope is required only for triangular/trapezoidal sections")
        if shape != "circular" and normal_branch != "lower":
            invalid("normal_branch", "upper normal-depth branch is circular only")
        section = Section(shape, b, side, d)
        lo, hi = section.limits
        r = Result({"Manning": "Q=A R^(2/3) sqrt(S0)/n in SI; customary coefficient 1.486 is not applied to converted SI quantities",
                    "critical": "Fr^2=Q^2 T/(g A^3)=1; E=y+Q^2/(2g A^2)",
                    "jump": "momentum=Q^2/(g A)+integral_0^y A(z) dz; rectangular y2=y1*(sqrt(1+8Fr1^2)-1)/2",
                    "standard step": "E2-E1=S0*dx-(Sf1+Sf2)*dx/2; Sf=(n Q/(A R^(2/3)))^2"},
                   "Hydrostatic pressure, constant discharge, prismatic channel, velocity/energy coefficients=1. Manning is empirical; no lateral inflow or local structures.",
                   "Positive depth and discharge; circular depth strictly below soffit. Uniform slope>0. GVF stays on its boundary's Froude branch; no critical crossing.")

        def conveyance(y):
            a, p, _ = section.geometry(y)
            return a*(a/p)**(2/3)/n

        if mode == "uniform":
            if discharge or boundary_depth or profile_length:
                invalid("discharge", "uniform mode takes depth, not discharge/profile inputs")
            if slope <= 0:
                invalid("bed_slope", "uniform flow requires positive bed slope")
            y = q(depth, "depth", "length")
            flow = conveyance(y)*math.sqrt(slope)
        else:
            flow = q(discharge, "discharge", "flow")
            if mode == "profile":
                if depth:
                    invalid("depth", "profile uses boundary_depth")
                y = q(boundary_depth, "boundary_depth", "length")
            else:
                if boundary_depth or profile_length:
                    invalid("boundary_depth", "profile inputs require profile mode")
                y = q(depth, "depth", "length", optional=True)

        def fr2(y):
            a, _, t = section.geometry(y)
            return flow*flow*t/(G*a**3)

        critical = bisect(lambda y: fr2(y)-1, lo, hi, "discharge")
        normal = None
        if slope > 0:
            root_hi = hi
            if shape == "circular":
                peak = maximum(conveyance, .82*d, hi, "discharge")
                capacity = conveyance(peak)*math.sqrt(slope)
                r.add("maximum_uniform_discharge", capacity, "m^3/s")
                r.add("maximum_discharge_depth", peak, "m")
                if flow > capacity*(1+1e-12):
                    if y is None or mode == "uniform":
                        invalid("discharge", "discharge exceeds maximum part-full Manning capacity")
                    r.warnings.append("No normal depth: discharge exceeds circular Manning capacity.")
                else:
                    low, high = (lo, peak) if normal_branch == "lower" else (peak, hi)
                    normal = bisect(lambda z: conveyance(z)*math.sqrt(slope)-flow, low, high, "normal_branch", rtol=4e-15, atol=0.)
                root_hi = peak
            else:
                normal = bisect(lambda z: conveyance(z)*math.sqrt(slope)-flow, lo, root_hi, "discharge", rtol=4e-15, atol=0.)
        elif y is None:
            invalid("bed_slope", "normal/uniform depth requires positive slope; supply a depth for nonuniform analysis")
        if y is None:
            y = normal
        a, perimeter, top = section.geometry(y)
        radius = a/perimeter
        froude = math.sqrt(fr2(y))

        def energy(z):
            area = section.geometry(z)[0]
            return z+flow*flow/(2*G*area*area)

        def momentum(z):
            return flow*flow/(G*section.geometry(z)[0])+section.pressure_moment(z)

        r.add("discharge", flow, "m^3/s")
        r.add("depth", y, "m")
        r.add("area", a, "m^2")
        r.add("wetted_perimeter", perimeter, "m")
        r.add("hydraulic_radius", radius, "m")
        r.add("top_width", top, "m")
        r.add("velocity", flow/a, "m/s")
        r.add("reynolds_number", 4*rho*(flow/a)*radius/mu)
        r.add("froude_number", froude)
        r.add("critical_depth", critical, "m")
        r.add("specific_energy", energy(y), "m")
        if normal is not None:
            r.add("normal_depth", normal, "m")
        r.data["regime"] = "critical" if abs(froude-1) < 1e-7 else ("subcritical" if froude < 1 else "supercritical")
        if shape == "circular" and y/d > .82:
            r.warnings.append("Circular depth/full ratio exceeds 0.82; conveyance reaches a maximum near 0.938D then decreases. Select the normal-depth branch explicitly.")
        if abs(froude-1) > 1e-7:
            left, right = (critical, hi) if y < critical else (lo, critical)
            target = energy(y)
            if (energy(left)-target)*(energy(right)-target) <= 0:
                alternate = bisect(lambda z: energy(z)-target, left, right, "depth")
                r.add("alternate_depth", alternate, "m")
            else:
                r.warnings.append("Alternate depth lies outside the supported free-surface geometry; omitted.")
        if froude > 1+1e-7:
            if shape == "rectangular":
                sequent = y*4*froude*froude/(math.sqrt(1+8*froude*froude)+1)
            elif momentum(hi) >= momentum(y):
                sequent = bisect(lambda z: momentum(z)-momentum(y), critical, hi, "depth")
            else:
                sequent = None
                r.warnings.append("Sequent depth lies above the circular soffit; jump result omitted.")
            if sequent is not None:
                r.add("sequent_depth", sequent, "m")
                r.add("jump_energy_loss", (sequent-y)**3/(4*sequent*y) if shape == "rectangular" else energy(y)-energy(sequent), "m")
        if mode == "profile":
            length = q(profile_length, "profile_length", "length")
            if abs(froude-1) < 1e-4:
                invalid("boundary_depth", "standard-step profile cannot start at a critical control")
            if slope > 0 and normal is None:
                invalid("discharge", "profile classification needs a normal depth within section capacity")
            dx = length/steps*(-1 if froude < 1 else 1)
            left, right = (critical*(1+1e-7), hi) if froude < 1 else (lo, critical*(1-1e-7))

            def sf(z):
                return (flow/conveyance(z))**2

            rows = [[0., 0., y, y, energy(y), froude, classification(slope, normal, critical, y)]]
            previous = y
            x = 0.
            attempts = deepest = 0
            accepted_lengths = []

            def try_step(previous, step_dx):
                old_energy, old_sf = energy(previous), sf(previous)
                old_area = section.geometry(previous)[0]
                def step_residual(z):
                    area = section.geometry(z)[0]
                    energy_change = z-previous+flow**2/(2*G)*(old_area-area)*(old_area+area)/(area*old_area)**2
                    return energy_change+(.5*(old_sf+sf(z))-slope)*step_dx

                def consistent(z):
                    if (z-critical)*(previous-critical) <= 0:
                        return False
                    if normal is not None and ((previous != normal and (z-normal)*(previous-normal) <= 0)
                                               or (previous == normal and z != previous)):
                        return False
                    old_gradient = (slope-old_sf)/(1-fr2(previous))
                    new_gradient = (slope-sf(z))/(1-fr2(z))
                    if old_gradient*new_gradient < 0 or (z-previous)*step_dx*old_gradient < 0:
                        return False
                    # At the representable normal-depth limit, a constant depth
                    # is acceptable only if the energy equation still resolves.
                    if z == previous and old_gradient != 0:
                        if normal is None or abs(previous-normal) > 16*math.ulp(normal):
                            return False
                    scale = max(abs(step_dx*slope), abs(step_dx*old_sf), abs(z-previous))
                    return abs(step_residual(z)) <= 1e-10*scale+16*math.ulp(old_energy)

                if consistent(previous):
                    return previous
                # Scan a bounded branch and choose the nearest continuous root.
                grid = sorted(set([*np.geomspace(left, right, 100), previous,
                                   *([normal] if normal is not None and left < normal < right else [])]))
                roots = []
                last, f_last = grid[0], step_residual(grid[0])
                for z in grid[1:]:
                    fz = step_residual(z)
                    if fz == 0 or f_last == 0 or (fz > 0) != (f_last > 0):
                        roots.append(bisect(step_residual, last, z, "profile_length", rtol=4e-15, atol=0.))
                    last, f_last = z, fz
                if not roots:
                    return None
                current = min(roots, key=lambda z: abs(math.log(z/previous)))
                return current if consistent(current) else None

            for _ in range(steps):
                pending = [(dx, 0)]
                while pending:
                    step_dx, level = pending.pop()
                    if attempts >= _PROFILE_WORK_CAP:
                        invalid("profile_steps", "profile cannot preserve its normal/critical-depth branch within 500 step attempts; shorten the reach", "input.out_of_range")
                    attempts += 1
                    current = try_step(previous, step_dx)
                    if current is None:
                        if level == _PROFILE_REFINEMENTS:
                            invalid("profile_length", "profile cannot preserve its normal/critical-depth branch after 12 step bisections; shorten the reach", "input.out_of_range")
                        pending.extend([(step_dx/2, level+1), (step_dx/2, level+1)])
                        continue
                    deepest = max(deepest, level)
                    accepted_lengths.append(abs(step_dx))
                    x += step_dx
                    bed = -slope*x
                    rows.append([x, bed, current, bed+current, bed+energy(current), math.sqrt(fr2(current)), classification(slope, normal, critical, current)])
                    previous = current
            r.data["profile_classification"] = rows[0][-1]
            r.data["integration_direction"] = "upstream" if dx < 0 else "downstream"
            r.data["requested_profile_steps"] = steps
            r.data["profile_steps"] = len(accepted_lengths)
            r.data["profile_step_attempts"] = attempts
            r.data["profile_refinements"] = deepest
            r.data["minimum_step_length_m"] = min(accepted_lengths)
            r.data["maximum_step_length_m"] = max(accepted_lengths)
            r.add("profile_end_depth", previous, "m")
            r.artifacts.append(handle.write_text("channel_profile.csv", csv_text(["x_m", "bed_m", "depth_m", "water_surface_m", "total_head_m", "froude", "profile"], rows), fmt="csv"))
        return r.done("discharge")

    return run("open_channel", inputs, compute)
