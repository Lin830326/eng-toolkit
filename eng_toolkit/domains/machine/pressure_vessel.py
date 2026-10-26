"""Thin membrane and Lame elastic cylinder/sphere solutions."""

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult

from eng_toolkit.domains.machine._common import Q, Factor, Result, System, choice, invalid, number, q, run
from eng_toolkit.domains.machine._server import server


@server.tool(description="Thin-wall membrane or thick-wall Lame cylinder/sphere under internal/external pressure. Returns stresses, von Mises/Tresca, elastic bore displacement, and hoop-allowable wall sizing. Cylinder ends closed/open/constrained. Warns at r/t<10 with thin error. A sphere is a full sphere or hemispherical end only: no ellipsoidal/torispherical heads, nozzle openings or code check. joint_efficiency scales the allowable; corrosion_allowance is added to the sized wall.")
def pressure_vessel(
    geometry: Annotated[str, Field(description="'cylinder' or 'sphere'; spheres require closed geometry.")],
    inner_radius: Q, internal_pressure: Q, thickness: Q = "", outer_radius: Q = "",
    external_pressure: Q = "0 MPa",
    model: Annotated[str, Field(description="'thin' membrane or 'thick' Lame; wall sizing always reports both methods.")] = "thick",
    ends: Annotated[str, Field(description="Cylinder 'closed', 'open', or 'constrained' (zero axial strain). Sphere: 'closed' only.")] = "closed",
    radius: Q = "", youngs_modulus: Q = "", poisson_ratio: Factor = .3,
    yield_strength: Q = "", allowable_stress: Q = "", unit_system: System = "metric",
    joint_efficiency: Annotated[float, Field(description="Weld joint efficiency E, 0.5..1 (default 1): wall sizing uses the allowable stress times E; stresses are not changed.")] = 1.0,
    corrosion_allowance: Annotated[str, Field(description="Metal added to the sized wall, e.g. '1 mm'; '' = none. Gives nominal_thickness_*; with thickness, the margin after corrosion.")] = "",
) -> ToolResult:
    inputs = dict(locals())

    def compute():
        r = Result(unit_system, {
            "thin": "dp=pi-po; cylinder hoop=dp*a/t, axial_closed=dp*a/(2t); sphere tangent=dp*a/(2t); radial=0",
            "Lame_cylinder": "A=(pi*a^2-po*b^2)/(b^2-a^2); B=(pi-po)*a^2*b^2/(b^2-a^2); sr=A-B/r^2; st=A+B/r^2; sz=closed:A, open:0, constrained:2nu*A",
            "Lame_sphere": "A=(pi*a^3-po*b^3)/(b^3-a^3); B=(pi-po)*a^3*b^3/(b^3-a^3); sr=A-B/r^3; st=sp=A+B/(2r^3)",
            "equivalents": "vm=sqrt(((sr-st)^2+(st-sz)^2+(sz-sr)^2)/2); Tresca=max(sr,st,sz)-min(sr,st,sz)",
            "sizing": "S = allowable_stress*joint_efficiency; corrosion: t_nominal = t_required + corrosion_allowance; thin t=abs(dp)*a/(S for cylinder,2S for sphere); thick target H=sign(dp)*S: cylinder b/a=sqrt((H+pi)/(H-pi+2po)); sphere b/a=((H+pi)/(H-pi/2+3po/2))^(1/3)",
            "displacement": "cylinder u(a)=a/E*(st-nu*(sr+sz)); sphere u(a)=a/E*((1-nu)*st-nu*sr), using exact bore stresses",
        }, "Homogeneous isotropic linear elasticity, tension positive, a=inner radius, b=outer radius. Thin model uses pressure difference and neglects radial stress. Cylinder constrained means zero axial strain. Wall sizing limits absolute bore hoop stress only, not von Mises; it is not a code design. Bore displacement always uses Lame. No end discontinuities, plasticity, thermal stresses, ASME code check or external-pressure buckling.",
                   "b>a>0; t/a>=1e-6; elastic small displacement. Thin approximation normally a/t>=10. Required thickness exists only when allowable exceeds the asymptotic Lame hoop demand. External-pressure stress results do not establish stability.")
        shape = choice(geometry, "geometry", ("cylinder", "sphere"))
        method = choice(model, "model", ("thin", "thick"))
        end = choice(ends, "ends", ("closed", "open", "constrained"))
        if shape == "sphere" and end != "closed":
            invalid("ends", "spheres require closed geometry")
        a = q(inner_radius, "inner_radius", "length")
        t = q(thickness, "thickness", "length", optional=True)
        b = q(outer_radius, "outer_radius", "length", optional=True)
        if t is not None and b is not None:
            invalid("thickness", "give thickness or outer_radius, not both")
        pi = q(internal_pressure, "internal_pressure", "stress", zero=True)
        po = q(external_pressure, "external_pressure", "stress", zero=True)
        nu = number(poisson_ratio, "poisson_ratio", 0, .4999)
        e = q(youngs_modulus, "youngs_modulus", "stress", optional=True)
        sy = q(yield_strength, "yield_strength", "stress", optional=True)
        allow = q(allowable_stress, "allowable_stress", "stress", optional=True)
        efficiency = number(joint_efficiency, "joint_efficiency", .5, 1)
        corrosion = q(corrosion_allowance, "corrosion_allowance", "length", zero=True, optional=True) or 0.0
        if (efficiency != 1 or corrosion) and allow is None:
            invalid("allowable_stress", "joint_efficiency and corrosion_allowance apply to wall sizing: give allowable_stress", "input.missing")
        if allow is not None:
            allow *= efficiency  # sizing limits the hoop stress to S*E
        pos = q(radius, "radius", "length", optional=True)
        dp = pi-po
        if po:
            r.warnings.append("External pressure present: buckling is not evaluated.")
        if shape == "sphere":
            r.warnings.append("The sphere model is a full sphere or a hemispherical end: its wall is about half the cylinder wall. A 2:1 ellipsoidal or a torispherical dished end is NOT this shape "
                              "and needs about the cylinder wall; this tool has no formula for it. Do not use the sphere result for such an end.")
        r.warnings.append("Not modelled: dished (ellipsoidal/torispherical) heads, openings and nozzle reinforcement, saddles and supports, fatigue, and code margins (plate under-tolerance, test pressure). "
                          "Weld joint efficiency and corrosion allowance enter only through joint_efficiency and corrosion_allowance in wall sizing.")
        if allow is not None:
            thin_t = abs(dp)*a/(allow*(1 if shape == "cylinder" else 2))
            if dp == 0:
                if max(pi, po) > allow:
                    invalid("allowable_stress", "equal internal and external pressure give a bore hoop stress of -p at any wall thickness, which exceeds the allowable")
                required = 0.0
            else:
                target = math.copysign(allow, dp)
                den = target-pi+2*po if shape == "cylinder" else target-pi/2+1.5*po
                ratio = (target+pi)/den if den else -1
                if ratio <= 1:
                    invalid("allowable_stress", "no finite wall meets this absolute Lame bore-hoop allowable")
                required = a*math.expm1(math.log(ratio)/(2 if shape == "cylinder" else 3))
            r.add("required_thickness_thin", thin_t, "length")
            r.add("required_thickness_lame", required, "length")
            if corrosion:
                r.add("nominal_thickness_thin", thin_t+corrosion, "length")
                r.add("nominal_thickness_lame", required+corrosion, "length")
                r.data["corrosion_note"] = "nominal = pressure thickness + corrosion_allowance; the pressure thickness is computed on the as-given inner radius (the corrosion allowance does not enlarge the radius)"
            if efficiency != 1:
                r.data["allowable_used_for_sizing"] = {"allowable_stress_times_joint_efficiency_MPa": allow, "joint_efficiency": efficiency}
            if thin_t > 0 and a/thin_t < 10:
                r.warnings.append("Thin-wall required-thickness estimate has r/t below 10; use the exact Lame sizing.")
        if t is None and b is None:
            if allow is None:
                invalid("thickness", "give thickness/outer_radius or an allowable for sizing")
            if pos is not None or e is not None or sy is not None:
                invalid("thickness", "stress/displacement evaluation needs an actual wall thickness")
            return r.done("required_thickness_lame" if method == "thick" else "required_thickness_thin")
        b = a+t if b is None else b
        if b <= a:
            invalid("outer_radius", "outer radius must exceed inner radius")
        t = b-a
        if t/a < 1e-6:
            invalid("thickness", "t/r below supported resolution", "input.out_of_range")
        pos = a if pos is None else pos
        if not a <= pos <= b:
            invalid("radius", "evaluation radius must lie inside the wall")
        n = 2 if shape == "cylinder" else 3
        an, bn = a**n, b**n
        aa, bb = (pi*an-po*bn)/(bn-an), dp*an*bn/(bn-an)
        axial = aa if end == "closed" else 0 if end == "open" else 2*nu*aa
        def exact(x):
            sr = aa-bb/x**n
            st = aa+bb/x**n/(1 if shape == "cylinder" else 2)
            return sr, st, axial if shape == "cylinder" else st
        bore = exact(a)
        selected = exact(pos)
        if method == "thin":
            hoop = dp*a/t/(1 if shape == "cylinder" else 2)
            if po and abs(hoop) < 10*max(pi, po):
                invalid("model", "thin-wall model neglects the radial stress, which is comparable to the membrane stress here (external pressure); use model thick")
            az = hoop if shape == "sphere" else hoop/2 if end == "closed" else 0 if end == "open" else nu*hoop
            selected = (0, hoop, az)
            r.data["thin_hoop_relative_error_against_lame"] = (hoop-bore[1])/bore[1] if bore[1] else None
            if a/t < 10:
                r.warnings.append("Thin-wall r/t below 10; inspect the reported error against Lame.")
        sr, st, sz = selected
        inner = bore if method == "thick" else selected
        ir, it, iz = inner
        vm = math.sqrt(((ir-it)**2+(it-iz)**2+(iz-ir)**2)/2)
        tresca = max(inner)-min(inner)
        for name, value in (("radial_stress", sr), ("hoop_stress", st), ("axial_stress", sz),
                            ("inner_radial_stress", ir), ("inner_hoop_stress", it), ("inner_axial_stress", iz),
                            ("inner_von_mises", vm), ("inner_tresca", tresca),
                            ("lame_inner_hoop", bore[1])):
            r.add(name, value, "stress")
        r.add("thickness", t, "length")
        if corrosion:
            r.add("thickness_after_corrosion", t-corrosion, "length")
            if allow is not None:
                r.add("thickness_margin_after_corrosion", t-corrosion-(required if method == "thick" else thin_t), "length")
        r.add("radius_thickness_ratio", a/t)
        r.safety("yield_safety_factor", sy, vm)
        r.safety("tresca_safety_factor", sy, tresca)
        if e is not None:
            u = a/e*(bore[1]-nu*(bore[0]+bore[2]))
            r.add("inner_displacement", u, "length")
            if abs(u)/a > .01:
                r.warnings.append("Bore strain exceeds 1 percent; small-strain elasticity needs review.")
        if sy is not None and vm > sy:
            r.warnings.append("Elastic von Mises stress exceeds yield strength.")
        r.data["lame_constants"] = {"A_MPa": aa, "B_MPa_mm_power_n": bb, "n": n}
        return r.done("inner_von_mises")
    return run("pressure_vessel", inputs, compute)
