"""Open/crossed flat and V belt geometry and limiting capstan transmission."""

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult

from eng_toolkit.domains.machine._common import Q, Factor, Result, System, choice, invalid, number, q, rpm, run
from eng_toolkit.domains.machine._server import server


@server.tool(description="Flat/V belt drive: open or crossed length, wrap, Euler capstan ratio, centrifugal tension, limiting power and inverse tensions/preload. Give velocity OR small-pulley speed; power OR effective tight_tension. V groove_angle is the included angle. Optional maximum total tension gives maximum-power speed. No slip/creep model.")
def belt(
    small_diameter: Q, large_diameter: Q, centre_distance: Q,
    configuration: Annotated[str, Field(description="'open' (same rotation) or 'crossed' (opposite rotation); crossed V belts are refused.")] = "open",
    belt_type: Annotated[str, Field(description="'flat' or 'v'; V friction uses the included groove_angle.")] = "flat",
    friction: Factor = .3, groove_angle: Q = "40 deg", velocity: Q = "", speed: Q = "", mass_per_length: Q = "",
    tight_tension: Annotated[str, Field(description="Effective tight-side traction tension, exclusive of m*v^2, e.g. '300 N'; give this OR power.")] = "",
    power: Q = "",
    maximum_tension: Annotated[str, Field(description="Optional maximum TOTAL belt tension including centrifugal tension, e.g. '1 kN'; requires mass_per_length for optimum speed.")] = "",
    unit_system: System = "metric",
) -> ToolResult:
    inputs = dict(locals())

    def compute():
        r = Result(unit_system, {
            "length": "open L~2C+pi*(D+d)/2+(D-d)^2/(4C); crossed replace (D-d)^2 by (D+d)^2",
            "exact_length": "L=2sqrt(C^2-k^2)+pi*(R+r)+2k*asin(k/C); k=R-r open, R+r crossed",
            "wrap": "open theta=pi-2asin((D-d)/(2C)); crossed theta=pi+2asin((D+d)/(2C))",
            "Euler_capstan": "effective T1/T2=exp(mu_eff*theta); mu_eff=mu (flat), mu/sin(beta/2) (V)",
            "power": "Tc=m*v^2; P=(T1-T2)*v; total strand tensions=T1+Tc,T2+Tc; initial tension=(T1+T2)/2+Tc",
            "inverse": "T2=(P/v)/(ratio-1); T1=T2+P/v",
            "maximum_power": "v_opt=sqrt(Tmax_total/(3*m)); Pmax=(Tmax_total-m*v_opt^2)*(1-1/ratio)*v_opt",
            "shaft_load": "Fx=(T1+T2)*cos(alpha); Fy=(T1-T2)*sin(alpha); load=hypot(Fx,Fy). Effective tensions account for moving-belt momentum; alpha=asin(k/C).",
        }, "Ideal flexible belt at impending slip on small pulley; identical friction at both pulleys. T1,T2 are effective tractive tensions, exclusive of centrifugal tension; the total-tension capstan equation is (T1_total-Tc)/(T2_total-Tc)=exp(mu_eff*theta). Initial tension uses the equal-strand elongation approximation. No creep, slip, sag, bending stiffness, belt thickness, groove bottoming or belt life model.",
                   "D>=d>0; C>(D+d)/2 to avoid pulley overlap. Length polynomial is a long-centre approximation; exact geometric length also returned. Warn for C<D+d or small-pulley wrap<120 deg. Exponent mu_eff*theta<=50. Crossed V belts are refused.")
        d, big, c = [q(raw, name, "length") for raw, name in ((small_diameter, "small_diameter"), (large_diameter, "large_diameter"), (centre_distance, "centre_distance"))]
        config = choice(configuration, "configuration", ("open", "crossed"))
        kind = choice(belt_type, "belt_type", ("flat", "v"))
        if kind == "v" and config == "crossed":
            invalid("configuration", "crossed V belts are not supported")
        if big < d:
            invalid("large_diameter", "large diameter must be >= small diameter")
        if c <= (big+d)/2:
            invalid("centre_distance", "centre distance must exceed the sum of pulley radii")
        mu = number(friction, "friction", 0, 10, positive=True)
        beta = q(groove_angle, "groove_angle", "angle")
        if not math.radians(5) <= beta <= math.radians(120):
            invalid("groove_angle", "included V groove angle must be 5..120 deg", "input.out_of_range")
        if kind == "flat" and groove_angle != "40 deg":
            invalid("groove_angle", "groove_angle is only applicable to a V belt")
        v = q(velocity, "velocity", "speed", optional=True)
        omega = rpm(speed) if speed else None
        if v is not None and omega is not None:
            invalid("velocity", "give velocity or rotational speed, not both")
        if omega is not None:
            v = omega*d/2000
        mass = q(mass_per_length, "mass_per_length", "mass_length", optional=True)
        t1 = q(tight_tension, "tight_tension", "force", optional=True)
        p = q(power, "power", "power", optional=True)
        maximum = q(maximum_tension, "maximum_tension", "force", optional=True)
        if t1 is not None and p is not None:
            invalid("power", "give power or effective tight_tension, not both")
        if v is None and (p is not None or t1 is not None):
            invalid("velocity", "tension/power evaluation requires belt velocity or small-pulley speed")
        k = (big-d)/2 if config == "open" else (big+d)/2
        alpha = math.asin(k/c)
        theta = math.pi-2*alpha if config == "open" else math.pi+2*alpha
        exponent = mu*theta/(math.sin(beta/2) if kind == "v" else 1)
        if not 1e-9 <= exponent <= 50:
            invalid("friction", "mu_eff*theta must be 1e-9..50", "input.out_of_range")
        ratio_minus_one = math.expm1(exponent)
        ratio = ratio_minus_one+1
        approximate = 2*c+math.pi*(big+d)/2+k*k/c
        exact = 2*math.sqrt(c*c-k*k)+math.pi*(big+d)/2+2*k*alpha
        r.add("belt_length", approximate, "length")
        r.add("exact_belt_length", exact, "length")
        r.add("wrap_angle", theta, "angle")
        r.add("tension_ratio", ratio)
        r.data["length_relative_error"] = (approximate-exact)/exact
        if c < big+d:
            r.warnings.append("Centre distance below D+d; short-centre installation and length approximation need review.")
        if theta < 2*math.pi/3:
            r.warnings.append("Small-pulley wrap angle is below 120 degrees.")
        if v is not None:
            tc = (mass or 0)*v*v
            r.add("belt_velocity", v, "speed")
            r.add("centrifugal_tension", tc, "force")
            if p is not None:
                t2 = p/v/ratio_minus_one
                t1 = t2+p/v
            elif t1 is not None:
                t2 = t1/ratio
                p = (t1-t2)*v
            if t1 is not None:
                for name, value in (("tight_tension", t1), ("slack_tension", t2),
                                    ("total_tight_tension", t1+tc), ("total_slack_tension", t2+tc),
                                    ("initial_tension", (t1+t2)/2+tc),
                                    ("shaft_load_x", (t1+t2)*math.cos(alpha)),
                                    ("shaft_load_y", (t1-t2)*math.sin(alpha)),
                                    ("shaft_load", math.hypot((t1+t2)*math.cos(alpha), (t1-t2)*math.sin(alpha)))):
                    r.add(name, value, "force")
                r.add("transmitted_power", p, "power")
                if maximum is not None and t1+tc > maximum:
                    r.warnings.append("Total tight-side tension exceeds maximum_tension.")
            elif maximum is not None and mass is not None and tc >= maximum:
                r.warnings.append("Centrifugal tension reaches the total tension limit; no traction capacity remains.")
        if mass is not None and v is None and maximum is None:
            invalid("velocity", "mass_per_length needs a velocity or maximum_tension")
        if maximum is not None:
            if mass is None:
                invalid("mass_per_length", "maximum-power speed requires mass per length")
            optimum = math.sqrt(maximum/(3*mass))
            r.add("maximum_power_speed", optimum, "speed")
            r.add("maximum_power", 2*maximum/3*(1-1/ratio)*optimum, "power")
        return r.done("transmitted_power" if p is not None else "belt_length")
    return run("belt", inputs, compute)
