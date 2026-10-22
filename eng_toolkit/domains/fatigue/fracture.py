"""Mode-I handbook geometries and bounded Paris/Forman crack-growth integration."""

from dataclasses import dataclass
import math
from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, choice, csv_text, integer, invalid, number, positive_exp, q, root, run
from ._server import server

GEOMETRIES = ("infinite", "center_secant", "center_tada", "edge_small", "edge", "double_edge", "surface", "senb", "ct")


@dataclass(frozen=True)
class Geometry:
    name: str
    width: float | None
    thickness: float | None
    span: float | None
    aspect: float | None
    point: str

    def bounds(self):
        if self.name == "infinite":
            return 1e-12, 1e4
        if self.name == "surface":
            # c/(W/2)<.5 and a/t<=.8; preserve a/c during growth.
            return 1e-12, min(.8 * self.thickness, .249999 * self.width * self.aspect)
        ratio = {"center_secant": .45, "center_tada": .45, "edge_small": .05,
                 "edge": .6, "double_edge": .4, "senb": .8, "ct": .8}[self.name]
        return (.2 * self.width if self.name in ("senb", "ct") else 1e-12), ratio * self.width

    def factor(self, a, point=None):
        lo, hi = self.bounds()
        if a < lo * (1-1e-12) or a > hi * (1+1e-12):
            invalid("crack_size", f"crack outside {self.name} validity: {lo:g}..{hi:g} m", "input.out_of_range")
        if self.name == "infinite":
            return 1.
        x = a / self.width
        if self.name in ("center_secant", "center_tada"):
            secant = 1 / math.sqrt(math.cos(math.pi * x))
            return secant if self.name == "center_secant" else (1 - .025*(2*x)**2 + .06*(2*x)**4)*secant
        if self.name == "edge_small":
            return 1.12
        if self.name == "edge":
            return 1.122 - .231*x + 10.55*x*x - 21.71*x**3 + 30.382*x**4
        if self.name == "double_edge":
            z = 2*x
            return (1.122 - .561*z - .205*z*z + .471*z**3 - .190*z**4) / math.sqrt(1-z)
        if self.name == "senb":
            return 3*math.sqrt(x)*(1.99-x*(1-x)*(2.15-3.93*x+2.7*x*x))/(2*(1+2*x)*(1-x)**1.5)
        if self.name == "ct":
            return (2+x)*(.886+4.64*x-13.32*x*x+14.72*x**3-5.6*x**4)/(1-x)**1.5
        ratio = self.aspect
        depth = a / self.thickness
        c = a / ratio
        shape = 1 + 1.464*ratio**1.65
        m1 = 1.13 - .09*ratio
        m2 = -.54 + .89/(.2+ratio)
        m3 = .5 - 1/(.65+ratio) + 14*(1-ratio)**24
        width_factor = 1/math.sqrt(math.cos(math.pi*c/self.width*math.sqrt(depth)))
        base = (m1 + m2*depth**2 + m3*depth**4)*width_factor/math.sqrt(shape)
        where = point or self.point
        return base if where == "deepest" else base*math.sqrt(ratio)*(1+.1+.35*depth**2)

    def intensity(self, a, demand, point=None):
        f = self.factor(a, point)
        if self.name == "senb":
            return demand*self.span/(self.thickness*self.width**1.5)*f/1e6
        if self.name == "ct":
            return demand/(self.thickness*math.sqrt(self.width))*f/1e6
        return demand*math.sqrt(math.pi*a)*f

    def net_stress(self, a, demand):
        """(membrane, peak) elastic net-section stress in MPa; peak adds bending from load eccentricity.

        Symmetric sections (central, double edge) carry no bending. Single-edge and surface cracks
        assume ends free to rotate (as the handbook factors do), so the remote resultant acts at the
        gross centroid, eccentric to the net-section centroid."""
        if self.name == "infinite":
            return demand, demand
        if self.name in ("center_secant", "center_tada", "double_edge"):
            membrane = demand/(1-2*a/self.width)
            return membrane, membrane
        if self.name in ("edge", "edge_small"):
            ligament = self.width-a
            membrane = demand*self.width/ligament
            # M=sigma*W*B*a/2 on ligament W-a: peak=sigma*W/(W-a)*(1+3a/(W-a)).
            return membrane, membrane*(1+3*a/ligament)
        if self.name == "surface":
            # Gross W*t minus a semi-ellipse (depth a, half-length c) at y=0, the cracked face.
            w, t, c = self.width, self.thickness, a/self.aspect
            gross, removed = w*t, math.pi*a*c/2
            y_removed = 4*a/(3*math.pi)
            area = gross-removed
            centroid = (gross*t/2-removed*y_removed)/area
            inertia = (w*t**3/12 + gross*(t/2-centroid)**2
                       - (math.pi*c*a**3/8 - removed*y_removed**2 + removed*(y_removed-centroid)**2))
            force = demand*gross
            membrane = force/area
            return membrane, membrane + force*(centroid-t/2)*centroid/inertia
        ligament = self.width-a
        if self.name == "senb":
            return 0., 3*demand*self.span/(2*self.thickness*ligament**2)/1e6
        return demand/(self.thickness*ligament)/1e6, 2*demand*(2*self.width+a)/(self.thickness*ligament**2)/1e6


class Integrator:
    """Adaptive Simpson on log(a), positive integrand, bounded global evaluation count."""
    def __init__(self, fn, tolerance):
        self.fn = fn
        self.tolerance = tolerance
        self.evaluations = 0

    def value(self, x):
        self.evaluations += 1
        if self.evaluations > 50000:
            invalid("integration_tolerance", "integration exceeded 50000 evaluations", "numerical.no_convergence")
        return self.fn(x)

    def integrate(self, left, right):
        mid = (left+right)/2
        fl, fm, fr = self.value(left), self.value(mid), self.value(right)
        estimate = (right-left)*(fl+4*fm+fr)/6

        def split(a, b, fa, fc, fb, old, depth):
            c = (a+b)/2
            fd, fe = self.value((a+c)/2), self.value((c+b)/2)
            first = (c-a)*(fa+4*fd+fc)/6
            second = (b-c)*(fc+4*fe+fb)/6
            total = first+second
            if abs(total-old) <= 15*self.tolerance*abs(total):
                return total+(total-old)/15
            if depth == 0:
                invalid("integration_tolerance", "adaptive Simpson did not converge at depth 24", "numerical.no_convergence")
            return split(a,c,fa,fd,fc,first,depth-1)+split(c,b,fc,fe,fb,second,depth-1)
        return split(left,right,fl,fm,fr,estimate,24)


@server.tool(description="Mode-I LEFM: infinite/finite plates, edge/double-edge, Newman-Raju surface tension, ASTM E399 SENB/CT factors. K, critical crack, Irwin zones and size screen; Paris/Forman growth with adaptive integration and threshold. Geometry/yield limits enforced; no retardation or closure model.")
def fracture(
    mode: Annotated[str, Field(description="evaluate, critical or growth; compute stress intensity, solve critical size or integrate crack growth.")] = "evaluate",
    geometry: Annotated[str, Field(description="infinite, center_secant, center_tada, edge_small, edge, double_edge, surface, senb or ct; see geometry limits in docs.")] = "infinite",
    crack_size: Annotated[str, Field(description="Initial crack depth a or central half-length, e.g. '2 mm'; required in evaluate/growth, optional critical; '' omits.")] = "",
    width: Annotated[str, Field(description="Full plate width W or specimen width, e.g. '100 mm'; all geometries except infinite require it; '' omits.")] = "",
    thickness: Annotated[str, Field(description="Plate/specimen thickness B or t, e.g. '10 mm'; surface/SENB/CT require it, others use for size screen; '' omits.")] = "",
    surface_half_length: Annotated[str, Field(description="Surface half-length c, e.g. '5 mm'; surface only, paired with initial a; growth holds a/c fixed; '' omits.")] = "",
    surface_point: Annotated[str, Field(description="deepest or surface point for Newman-Raju; both K values are reported, selected point controls growth.")] = "deepest",
    span: Annotated[str, Field(description="SENB support span S, e.g. '400 mm'; requires S/W=4, unused by other geometries; '' omits.")] = "",
    stress: Annotated[str, Field(description="Remote maximum tensile stress, e.g. '100 MPa'; plate geometries, conflicts with stress_range; '' omits.")] = "",
    load: Annotated[str, Field(description="Maximum applied force P, e.g. '10 kN'; SENB/CT only, conflicts with load_range; '' omits.")] = "",
    stress_range: Annotated[str, Field(description="Remote stress range Delta_sigma, e.g. '50 MPa'; growth plates only, max=range/(1-R); '' omits.")] = "",
    load_range: Annotated[str, Field(description="Force range Delta_P, e.g. '5 kN'; growth SENB/CT only, max=range/(1-R); '' omits.")] = "",
    toughness: Annotated[str, Field(description="Mode-I toughness Kc or KIc, e.g. '50 MPa*m^0.5'; required critical/Forman, optional evaluate/Paris; '' omits.")] = "",
    yield_strength: Annotated[str, Field(description="Yield stress for LEFM screens, e.g. '400 MPa'; optional, peak net-section stress above .8 yield is refused; '' omits.")] = "",
    final_crack: Annotated[str, Field(description="Growth end limit a_f, e.g. '10 mm'; '' uses critical size, explicit limit is capped by Kc instability.")] = "",
    growth_law: Annotated[str, Field(description="paris or forman; Forman requires Kc and its own dimensionally different coefficient C.")] = "paris",
    growth_c: Annotated[str, Field(description="Paris/Forman C with composite units, e.g. '1e-12 m/cycle/(MPa*m^0.5)^3'; growth only; see Forman units.")] = "",
    growth_m: Annotated[float, Field(description="Paris/Forman power m, dimensionless .5..8; must agree with the composite units of C.")] = 3,
    load_ratio: Annotated[float, Field(description="Growth stress ratio R=Kmin/Kmax, dimensionless 0..0.95, default 0; compressive cycles are outside scope.")] = 0,
    threshold: Annotated[str, Field(description="No-growth Delta_K threshold, e.g. '4 MPa*m^0.5'; growth only; '' means zero threshold.")] = "",
    integration_tolerance: Annotated[float, Field(description="Relative adaptive Simpson tolerance, 1e-10..1e-3, default 1e-7; global cap 50000 evaluations.")] = 1e-7,
    table_points: Annotated[int, Field(description="Growth CSV stations, 2..101, default 31; log-spaced crack lengths, cumulative cycles.")] = 31,
):
    inputs = dict(locals())

    def compute(handle):
        kind = choice(mode, "mode", ("evaluate", "critical", "growth"))
        name = choice(geometry, "geometry", GEOMETRIES)
        point = choice(surface_point, "surface_point", ("deepest", "surface"))
        r = Result({"LEFM": "K=Y*sigma*sqrt(pi*a); SENB K=P*S/(B*W^1.5)*f; CT K=P/(B*sqrt(W))*f",
                    "Irwin": "ry_plane_stress=(K/Sy)^2/(2*pi), ry_plane_strain=(K/Sy)^2/(6*pi); zone size rp=2*ry along the crack plane",
                    "net_section": "peak=membrane+bending; single edge sigma*W/(W-a)*(1+3a/(W-a)); surface semi-ellipse section, free ends; screen peak<=.8Sy",
                    "E399_size_screen": "B,a,(W-a for specimens)>=2.5*(KIc/Sy)^2",
                    "Paris_Forman": "da/dN=C*DeltaK^m; Forman divides by ((1-R)*Kc-DeltaK)"},
                   "Linear elastic Mode I, long plates under uniform remote tension. Width is full width; a is half-length for central cracks, depth for edges. Surface growth preserves initial a/c and uses selected front point.",
                   "Central a/W<=.45; small edge<=.05; edge<=.6; double edge a/W<=.4; SENB/CT .2<=a/W<=.8, SENB S/W=4. Surface 0<a/c<=1, a/t<=.8, 2c/W<.5. Peak net-section stress, membrane plus bending, must be <=.8 supplied yield.")
        a0 = q(crack_size, "crack_size", "length", optional=kind == "critical")
        w = q(width, "width", "length", optional=name == "infinite")
        t = q(thickness, "thickness", "length", optional=name not in ("surface", "senb", "ct"))
        s = q(span, "span", "length", optional=name != "senb")
        c0 = q(surface_half_length, "surface_half_length", "length", optional=name != "surface")
        if name == "surface" and a0 is None:
            invalid("crack_size", "surface geometry needs an initial a to define aspect ratio", "input.missing")
        aspect = a0/c0 if name == "surface" else None
        if aspect is not None:
            number(aspect, "surface_half_length", 1e-4, 1)
            if a0/t > .8 or 2*c0/w >= .5:
                invalid("crack_size", "Newman-Raju verified range requires a/t<=.8 and 2c/W<.5", "input.out_of_range")
            if aspect < .2:
                r.warnings.append("a/c below .2 uses the Newman-Raju edge-crack limiting extension; finite-element calibration is strongest for .2..1.")
        if name == "senb" and not math.isclose(s/w, 4, rel_tol=1e-8):
            invalid("span", "SENB factor requires span/width=4")
        if (span and name != "senb") or (surface_half_length and name != "surface") or (width and name == "infinite"):
            invalid("geometry", "geometry-specific dimensions conflict with the selected geometry")
        g = Geometry(name, w, t, s, aspect, point)
        if a0 is not None:
            g.factor(a0)
        ratio = number(load_ratio, "load_ratio", 0, .95)
        specimen = name in ("senb", "ct")
        maximum = q(load if specimen else stress, "load" if specimen else "stress", "force" if specimen else "stress", optional=True)
        delta = q(load_range if specimen else stress_range, "load_range" if specimen else "stress_range", "force" if specimen else "stress", optional=True)
        if (specimen and (stress or stress_range)) or (not specimen and (load or load_range)):
            invalid("load" if specimen else "stress", "plates use stress; SENB/CT specimens use force")
        if maximum is not None and delta is not None:
            invalid("load_range" if specimen else "stress_range", "supply maximum or range, not both")
        if delta is not None:
            if kind != "growth":
                invalid("mode", "load ranges require growth mode")
            maximum = delta/(1-ratio)
        if maximum is None:
            invalid("load" if specimen else "stress", "maximum loading or growth range is required", "input.missing")
        kc = q(toughness, "toughness", "K", optional=kind != "critical")
        sy = q(yield_strength, "yield_strength", "stress", optional=True)

        def check_net(a):
            membrane, peak = g.net_stress(a, maximum)
            if sy is not None and peak > .8*sy:
                invalid("yield_strength", "peak net-section stress (membrane plus bending) exceeds .8 yield; LEFM estimate refused")
            return membrane, peak

        def critical_between(lo, hi):
            return math.exp(root(lambda la: math.log(g.intensity(math.exp(la), maximum)/kc),
                                 math.log(lo), math.log(hi), "toughness"))

        if kind == "critical":
            a = critical_between(*g.bounds())
            r.add("critical_crack_size", a, "m")
        else:
            a = a0
        membrane, net = check_net(a)
        intensity = g.intensity(a, maximum)
        r.add("stress_intensity", intensity, "MPa*m^0.5")
        r.add("geometry_factor", g.factor(a))
        r.add("net_section_stress", net, "MPa")
        r.add("net_section_membrane_stress", membrane, "MPa")
        if name == "surface":
            r.add("K_deepest", g.intensity(a, maximum, "deepest"), "MPa*m^0.5")
            r.add("K_surface", g.intensity(a, maximum, "surface"), "MPa*m^0.5")
        if kc is not None:
            r.add("fracture_margin", kc/intensity)
            if intensity >= kc and kind == "evaluate":
                r.warnings.append("Applied K reaches or exceeds supplied toughness; unstable fracture is predicted by this model.")
        if sy is not None:
            # Irwin first-order radius r_y and the plastic zone size r_p=2*r_y, both along the crack plane.
            ratio_squared = (intensity/sy)**2
            r.add("ry_plane_stress", ratio_squared/(2*math.pi), "m")
            r.add("ry_plane_strain", ratio_squared/(6*math.pi), "m")
            r.add("rp_plane_stress", ratio_squared/math.pi, "m")
            r.add("rp_plane_strain", ratio_squared/(3*math.pi), "m")
            if kc is not None:
                minimum = 2.5*(kc/sy)**2
                r.add("plane_strain_minimum_dimension", minimum, "m")
                valid = a >= minimum and t is not None and t >= minimum and (not specimen or w-a >= minimum)
                r.data["plane_strain_size_screen_passed"] = valid
                if not valid:
                    r.warnings.append("KIc plane-strain size screen fails or thickness is missing. This does not establish ASTM E399 specimen/test validity.")
        else:
            r.warnings.append("Yield strength omitted; net-section LEFM and plastic-zone validity could not be checked.")
        if kind != "growth":
            if growth_c or final_crack or threshold or growth_law != "paris" or load_ratio != 0:
                invalid("mode", "growth settings require growth mode")
            return r.done("critical_crack_size" if kind == "critical" else "stress_intensity")
        law = choice(growth_law, "growth_law", ("paris", "forman"))
        m = number(growth_m, "growth_m", .5, 8)
        tol = number(integration_tolerance, "integration_tolerance", 1e-10, 1e-3)
        stations = integer(table_points, "table_points", 2, 101)
        if law == "forman" and kc is None:
            invalid("toughness", "Forman requires toughness", "input.missing")
        exponent = m if law == "paris" else m-1
        coefficient = q(growth_c, "growth_c", "stress", target=f"m/cycle/(MPa*m^0.5)^{exponent:g}")
        dkth = q(threshold, "threshold", "K", zero=True, optional=True) or 0.
        af = q(final_crack, "final_crack", "length", optional=True)
        if kc is not None and intensity >= kc:
            invalid("crack_size", "initial crack is already unstable at maximum load")
        if af is None:
            if kc is None:
                invalid("final_crack", "growth needs final crack limit or toughness", "input.missing")
            af = critical_between(a0, g.bounds()[1])
        if af <= a0:
            invalid("final_crack", "final crack must exceed initial crack")
        g.factor(af)
        stop = "specified_crack_limit"
        if kc is not None and g.intensity(af, maximum) >= kc*(1-1e-10):
            if g.intensity(af, maximum) > kc:
                af = critical_between(a0, af)
            stop = "Kmax_instability"
        check_net(af)
        r.data["growth_stop"] = stop
        initial_dk = intensity*(1-ratio)
        r.add("initial_delta_k", initial_dk, "MPa*m^0.5")
        if initial_dk <= dkth:
            r.data["growth_status"] = "arrested_at_initial_threshold"
            r.data["cycles_to_failure"] = None
            r.add("final_crack_size", a0, "m")
            r.artifacts.append(handle.write_text("crack_growth.csv", csv_text(["crack_m", "cycles"], [[a0, 0]]), fmt="csv"))
            return r.done("final_crack_size")

        def integrand(log_a):
            size = math.exp(log_a)
            kmax = g.intensity(size, maximum)
            dk = kmax*(1-ratio)
            if dk <= dkth:
                invalid("threshold", "growth crosses a no-growth threshold within the integration interval")
            log_value = log_a-math.log(coefficient)-m*math.log(dk)
            if law == "forman":
                remaining = (1-ratio)*(kc-kmax)
                if remaining < -1e-8*kc:
                    invalid("final_crack", "Forman integration reached Kmax instability")
                if remaining <= 0:
                    return 0.
                log_value += math.log(remaining)
            return positive_exp(log_value, "growth_c")

        integrator = Integrator(integrand, tol)
        log_sizes = np.linspace(math.log(a0), math.log(af), stations)
        rows, cumulative = [[a0, 0.]], 0.
        for left, right in zip(log_sizes, log_sizes[1:]):
            cumulative += integrator.integrate(float(left), float(right))
            rows.append([math.exp(float(right)), cumulative])
        r.add("cycles_to_limit", cumulative, "count")
        if stop == "Kmax_instability":
            r.add("cycles_to_failure", cumulative, "count")
        r.add("final_crack_size", af, "m")
        r.data.update(integration_evaluations=integrator.evaluations, relative_tolerance=tol,
                      growth_status="integrated", coefficient_units=f"m/cycle/(MPa*m^0.5)^{exponent:g}")
        r.artifacts.append(handle.write_text("crack_growth.csv", csv_text(["crack_m", "cycles"], rows), fmt="csv"))
        r.warnings.append("Paris/Forman coefficients must be calibrated for the material, environment, R and growth regime. Threshold is a hard arrest model; no closure, overload retardation or sequence effects.")
        return r.done("cycles_to_limit")
    return run("fracture", inputs, compute)
