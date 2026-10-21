"""External standard full-depth involute spur gears: basic Lewis and Hertz checks."""

import bisect
import math

from eng_toolkit.contract import ToolResult

from eng_toolkit.domains.machine._common import (
    Q, Count, Factor, Result, System, count, invalid, number, paired, q, rpm, run,
)
from eng_toolkit.domains.machine._server import server

# Shigley, Mechanical Engineering Design, Lewis form factors, 20 degree full-depth.
# Y multiplies module, not circular pitch. Rack limit = 0.485.
LEWIS = ((12, .245), (14, .277), (16, .296), (18, .309), (20, .322),
         (24, .337), (30, .359), (38, .384), (43, .397), (50, .409), (60, .422),
         (100, .447), (150, .460), (300, .472))


def lewis(n: int) -> float:
    if not 12 <= n <= 300:
        invalid("lewis_factor", "table covers 12..300 teeth; give a form factor explicitly", "input.out_of_range")
    i = bisect.bisect_left([x[0] for x in LEWIS], n)
    if LEWIS[i][0] == n:
        return LEWIS[i][1]
    x0, y0 = LEWIS[i-1]
    x1, y1 = LEWIS[i]
    return y0 + (y1-y0)*(n-x0)/(x1-x0)


@server.tool(description="External spur pair: full-depth involute geometry, contact ratio/interference, Wt/velocity, basic Lewis bending and pitch-point Hertz stress. Give module OR diametral_pitch; torque OR power+speed. Empirical Lewis table only at 20 deg. This is not an AGMA/ISO 6336 rating.")
def gear(
    teeth_1: Count, teeth_2: Count, face_width: Q, module: Q = "", diametral_pitch: Q = "",
    pressure_angle: Q = "20 deg", torque: Q = "", power: Q = "", speed: Q = "",
    lewis_factor_1: Factor = 0, lewis_factor_2: Factor = 0, load_factor: Factor = 1,
    youngs_modulus_1: Q = "", youngs_modulus_2: Q = "", poisson_1: Factor = .3,
    poisson_2: Factor = .3, allowable_bending: Q = "", allowable_contact: Q = "",
    unit_system: System = "metric",
) -> ToolResult:
    inputs = dict(locals())

    def compute():
        r = Result(unit_system, {
            "geometry": "d=m*N; C=(d1+d2)/2; db=d*cos(phi); addendum=m; dedendum=1.25m; do=d+2m",
            "contact_ratio": "[sqrt(Ro1^2-Rb1^2)+sqrt(Ro2^2-Rb2^2)-C*sin(phi)]/[pi*m*cos(phi)]",
            "interference": "sqrt(Ro2^2-Rb2^2)-R2*sin(phi)<=R1*sin(phi), and converse; rack Nmin=2/sin(phi)^2",
            "Lewis": "sigma=load_factor*Wt/(F*m*Y); Wt=2T/d1; Wr=Wt*tan(phi); v=omega*d1/2; P=Wt*v",
            "Hertz": "sigma_H=sqrt((load_factor*Wt)/(F*cos(phi))*(1/rho1+1/rho2)/(pi*((1-nu1^2)/E1+(1-nu2^2)/E2))); rho_i=di*sin(phi)/2",
        }, "External unshifted spur gears, 1m addendum and 1.25m dedendum, no backlash. Lewis tooth-tip load and Hertz elastic line contact at pitch point. Combined load_factor multiplies stress-design Wt only; transmitted power uses nominal Wt. No AGMA or ISO 6336 rating, dynamics, mounting or lubrication model.",
                   "Pressure angle 5..45 deg. Automatic Lewis Y at 20 deg and 12..300 teeth, linear interpolation. Rack limit Y=0.485 is documented only. Contact ratio >=1 needed; rack undercut and pair interference are separate checks.")
        n1, n2 = count(teeth_1, "teeth_1"), count(teeth_2, "teeth_2")
        width = q(face_width, "face_width", "length")
        mod = q(module, "module", "length", optional=True)
        dp = q(diametral_pitch, "diametral_pitch", "pitch", optional=True)
        if (mod is None) == (dp is None):
            invalid("module", "give exactly one of module or diametral_pitch")
        mod = mod if mod is not None else 1/dp
        phi = q(pressure_angle, "pressure_angle", "angle")
        if not math.radians(5) <= phi <= math.radians(45):
            invalid("pressure_angle", "pressure angle must be 5..45 deg", "input.out_of_range")
        factor = number(load_factor, "load_factor", 0, 100, positive=True)
        ys = [number(y, name, 0, 1) for y, name in ((lewis_factor_1, "lewis_factor_1"), (lewis_factor_2, "lewis_factor_2"))]
        nus = [number(nu, name, 0, .4999) for nu, name in ((poisson_1, "poisson_1"), (poisson_2, "poisson_2"))]
        e1 = q(youngs_modulus_1, "youngs_modulus_1", "stress", optional=True)
        e2 = q(youngs_modulus_2, "youngs_modulus_2", "stress", optional=True)
        paired(e1, e2, "youngs_modulus_2")
        ab = q(allowable_bending, "allowable_bending", "stress", optional=True)
        ac = q(allowable_contact, "allowable_contact", "stress", optional=True)
        t = q(torque, "torque", "moment", optional=True)
        p = q(power, "power", "power", optional=True)
        omega = rpm(speed) if speed else None
        if p is not None and t is not None:
            invalid("torque", "give torque or power, not both")
        if p is not None and omega is None:
            invalid("speed", "power requires input rotational speed")
        if p is not None:
            t = p/omega*1000
        d1, d2 = mod*n1, mod*n2
        rr1, rr2 = d1/2, d2/2
        rb1, rb2 = rr1*math.cos(phi), rr2*math.cos(phi)
        ro1, ro2 = rr1+mod, rr2+mod
        c = rr1+rr2
        path1, path2 = math.sqrt(ro1*ro1-rb1*rb1), math.sqrt(ro2*ro2-rb2*rb2)
        cr = (path1+path2-c*math.sin(phi))/(math.pi*mod*math.cos(phi))
        undercut_min = 2/math.sin(phi)**2
        interference1 = path2 > c*math.sin(phi) + 1e-12*c
        interference2 = path1 > c*math.sin(phi) + 1e-12*c
        for name, value in (("module", mod), ("pitch_diameter_1", d1), ("pitch_diameter_2", d2),
                            ("centre_distance", c), ("base_diameter_1", 2*rb1), ("base_diameter_2", 2*rb2),
                            ("outside_diameter_1", 2*ro1), ("outside_diameter_2", 2*ro2),
                            ("addendum", mod), ("dedendum", 1.25*mod)):
            r.add(name, value, "length")
        r.add("speed_ratio", n2/n1)
        r.add("contact_ratio", cr)
        r.add("minimum_teeth_rack", undercut_min)
        r.data.update(interference_1=interference1, interference_2=interference2,
                      undercut_1=n1 < undercut_min, undercut_2=n2 < undercut_min)
        if n1 < undercut_min or n2 < undercut_min:
            r.warnings.append("Tooth count below the rack-cut undercut threshold; no profile-shift correction.")
        if interference1 or interference2:
            r.warnings.append("Pair involute interference: contact extends below a base-circle tangent point.")
        if cr < 1:
            r.warnings.append("Contact ratio below 1; continuous involute contact is not possible.")
        if omega is not None:
            r.add("pitch_line_velocity", omega*d1/2000, "speed")
            r.add("output_speed", omega*n1/n2, "angular_speed")
        if t is None and (e1 is not None or ab is not None or ac is not None or any(ys) or factor != 1):
            invalid("torque", "stress/material inputs require torque or power")
        if t is not None:
            if not math.isclose(phi, math.radians(20), rel_tol=0, abs_tol=1e-10) and not all(ys):
                invalid("lewis_factor_1", "non-20-degree teeth need explicit Lewis factors for both gears")
            ys = [y or lewis(n) for y, n in zip(ys, (n1, n2))]
            wt = 2*t/d1
            r.add("input_torque", t, "moment")
            r.add("tangential_force", wt, "force")
            r.add("design_tangential_force", factor*wt, "force")
            r.add("radial_force", wt*math.tan(phi), "force")
            for i, y in enumerate(ys, 1):
                stress = factor*wt/(width*mod*y)
                r.add(f"lewis_factor_{i}", y)
                r.add(f"bending_stress_{i}", stress, "stress")
                r.safety(f"bending_safety_factor_{i}", ab, stress)
            if omega is not None:
                r.add("transmitted_power", wt*omega*d1/2000, "power")
            if ac is not None and e1 is None:
                invalid("youngs_modulus_1", "contact safety requires both elastic moduli")
            if e1 is not None:
                compliance = (1-nus[0]**2)/e1+(1-nus[1]**2)/e2
                hertz = math.sqrt(factor*wt/(width*math.cos(phi))*(1/(rr1*math.sin(phi))+1/(rr2*math.sin(phi)))/(math.pi*compliance))
                r.add("contact_stress", hertz, "stress")
                r.safety("contact_safety_factor", ac, hertz)
            r.data["lewis_table"] = "Shigley, Mechanical Engineering Design, 20 degree full-depth Y (module convention); piecewise linear."
        return r.done("pitch_diameter_1")
    return run("gear", inputs, compute)
