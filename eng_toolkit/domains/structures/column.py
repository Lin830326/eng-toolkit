"""Euler, Johnson and eccentric-column secant estimates."""

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.domains.structures._common import Result, System, choice, invalid, number, q, run
from eng_toolkit.domains.structures._server import server


def bisect(function, lo, hi, *, residual_tolerance=0.):
    """Bracket-preserving bisection, including roots at either endpoint."""
    flo, fhi = function(lo), function(hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if flo*fhi > 0:
        invalid("eccentricity", "no root exists in the supported elastic bracket")
    for _ in range(400):
        if hi-lo <= 1e-14*max(abs(lo),abs(hi)):
            return (lo+hi)/2
        mid = math.exp((math.log(lo)+math.log(hi))/2) if lo > 0 and hi/lo > 100 else (lo+hi)/2
        fm = function(mid)
        if abs(fm) <= residual_tolerance:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    invalid("eccentricity", "root bracket did not converge within 400 iterations", "input.out_of_range")


def fixed_pinned_factor():
    # tan(z)=z on (pi,3pi/2); sin(z)-z*cos(z) avoids tangent's pole.
    root = bisect(lambda z: math.sin(z)-z*math.cos(z), math.pi, 1.5*math.pi)
    return math.pi/root


@server.tool(description="Euler and Johnson column buckling, slenderness, secant-formula eccentric yield load and safety. Standard effective lengths include fixed-pinned K=0.6992 from tan(z)=z. Prismatic straight column, linear elastic material; Johnson is an inelastic empirical estimate. No AISC/Eurocode check.")
def column(
    length: Annotated[str, Field(description="Required unsupported column length before applying K, e.g. '2 m'.")] = "",
    youngs_modulus: Annotated[str, Field(description="Required elastic Young's modulus for buckling, e.g. '200 GPa'.")] = "",
    area: Annotated[str, Field(description="Required cross-sectional area carrying compression, e.g. '1000 mm^2'.")] = "",
    inertia: Annotated[str, Field(description="Required second moment of area about the buckling axis, e.g. '1e6 mm^4'.")] = "",
    end_condition: Annotated[str, Field(description="pinned-pinned, fixed-free, fixed-pinned, fixed-fixed, or custom.")] = "pinned-pinned",
    effective_length_factor: Annotated[float, Field(description="Dimensionless K for custom ends, e.g. 0.8; required in (0,10] for custom; leave 0 for standard ends.")] = 0,
    yield_strength: Annotated[str, Field(description="Compressive yield stress for Johnson and secant checks, e.g. '250 MPa'; '' omits; required with eccentricity.")] = "",
    applied_load: Annotated[str, Field(description="Nonnegative compressive load for safety factor and secant stress, e.g. '50 kN'; '' omits demand checks.")] = "",
    eccentricity: Annotated[str, Field(description="Nonnegative load offset for pinned-pinned secant analysis, e.g. '5 mm'; requires extreme_fibre and yield_strength; '' omits.")] = "",
    extreme_fibre: Annotated[str, Field(description="Centroid-to-extreme-fibre distance on the bending axis, e.g. '50 mm'; required with eccentricity; '' omits.")] = "",
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(_):
        r = Result(unit_system, {
            "Euler": "Pe=pi^2*E*I/(K*L)^2; r=sqrt(I/A); lambda=K*L/r",
            "Johnson": "sigmaJ=Sy-Sy^2*lambda^2/(4*pi^2*E); transition=sqrt(2*pi^2*E/Sy)",
            "secant": "sigma_max=P/A*(1+e*c/r^2/cos(K*L/(2*r)*sqrt(P/(E*A)))); yield root below Pe by bisection",
            "fixed_pinned": "tan(z)=z, first root in (pi,3*pi/2); K=pi/z=0.6991556596",
        }, "Straight prismatic member, centred compression for Euler/Johnson. Secant assumes pinned ends and constant eccentricity. No imperfections, local buckling, residual stresses or design factors.",
                   "Positive E,A,I,L. Johnson applies below the transition and is not elastic buckling. Secant is restricted to pinned-pinned, 0<=P<Pe. No design-code check.")
        l, e, a, i = (q(raw, name, kind) for raw, name, kind in ((length,"length","length"),(youngs_modulus,"youngs_modulus","stress"),(area,"area","area"),(inertia,"inertia","inertia")))
        condition = choice(end_condition, "end_condition", ("pinned-pinned", "fixed-free", "fixed-pinned", "fixed-fixed", "custom"))
        k = number(effective_length_factor, "effective_length_factor", 0, 10)
        if condition == "custom":
            if k <= 0:
                invalid("effective_length_factor", "custom end condition requires K > 0")
        else:
            if k != 0:
                invalid("effective_length_factor", "K is only accepted with custom end_condition")
            k = {"pinned-pinned":1., "fixed-free":2., "fixed-pinned":fixed_pinned_factor(), "fixed-fixed":.5}[condition]
        sy = q(yield_strength, "yield_strength", "stress", optional=True)
        load = q(applied_load, "applied_load", "force", zero=True, optional=True)
        ecc = q(eccentricity, "eccentricity", "length", zero=True, optional=True)
        c = q(extreme_fibre, "extreme_fibre", "length", optional=True)
        if (ecc is None) != (c is None):
            invalid("eccentricity", "eccentricity and extreme_fibre must be supplied together")
        radius = math.sqrt(i/a)
        slenderness = k*l/radius
        pe = math.pi**2*e*i/(k*l)**2
        capacity = pe
        r.add("effective_length_factor", k)
        r.add("radius_of_gyration", radius, "length")
        r.add("slenderness", slenderness)
        r.add("euler_load", pe, "force")
        r.add("euler_stress", pe/a, "stress")
        r.data["elastic_check"] = "Yield strength omitted; elastic applicability cannot be checked."
        if sy is not None:
            transition = math.sqrt(2*math.pi**2*e/sy)
            r.add("transition_slenderness", transition)
            if slenderness < transition:
                capacity = a*(sy - sy**2*slenderness**2/(4*math.pi**2*e))
                r.add("johnson_load", capacity, "force")
                r.data["selected_method"] = "Johnson"
                r.data["elastic_check"] = "Below the Johnson/Euler transition; selected capacity is an inelastic empirical estimate, not elastic buckling."
                r.warnings.append("Euler alone overestimates short/intermediate column capacity; Johnson estimate selected.")
            else:
                r.data["selected_method"] = "Euler"
                r.data["elastic_check"] = "Above the Johnson/Euler transition; Euler stress is at most Sy/2. Elastic estimate within this idealized model."
        if ecc is not None:
            if condition != "pinned-pinned" or sy is None:
                invalid("eccentricity", "secant yield solution requires pinned-pinned ends and yield_strength")
            factor = ecc*c/radius**2
            def stress(p):
                return p/a*(1+factor/math.cos(math.pi/2*math.sqrt(p/pe)))
            if ecc == 0 and sy*a >= pe:
                r.data["secant_yield_load"] = None
                r.data["secant_reason"] = "No yield root below Euler buckling for zero eccentricity."
            else:
                hi = min(a*sy,pe*(1-1e-12))
                if stress(hi) < sy:
                    invalid("eccentricity", "yield root is too close to Euler singularity to resolve", "input.out_of_range")
                py = bisect(lambda p: stress(p)-sy, 0., hi, residual_tolerance=1e-12*sy)
                r.add("secant_yield_load", py, "force")
                capacity = min(capacity, py)
            if load is not None:
                if load >= pe:
                    invalid("applied_load", "secant formula requires applied_load below Euler critical load")
                r.add("maximum_stress", stress(load), "stress")
        r.add("estimated_capacity", capacity, "force")
        if load is not None:
            r.safety("safety_factor", capacity, load)
            if load >= capacity:
                r.warnings.append("Applied compression reaches or exceeds the estimated capacity.")
        return r.done("estimated_capacity")
    return run("column", inputs, compute)
