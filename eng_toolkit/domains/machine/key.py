"""Parallel key stresses and idealized spline bearing capacity."""

from typing import Annotated

from pydantic import Field

from eng_toolkit.contract import ToolResult

from eng_toolkit.domains.machine._common import Q, Count, Result, System, choice, count, invalid, number, q, run
from eng_toolkit.domains.machine._server import server


@server.tool(description="Parallel key shear/crushing, safety and minimum length; or straight-sided/involute spline bearing torque capacity n*p*L*h*r_mean*sharing. Key half-height bears in hub; shear strength may be 0.577*Sy. Spline default sharing=0.75 is an assumed utilization, not a standards rating. Static only.")
def key(
    joint_type: Annotated[str, Field(description="'key' (parallel key), 'straight_spline', or 'involute_spline'. Spline height is effective loaded radial contact height.")],
    length: Q, torque: Q = "", shaft_diameter: Q = "", width: Q = "", height: Q = "",
    yield_strength: Q = "", shear_strength: Q = "", bearing_strength: Q = "",
    tooth_count: Count = 1, mean_radius: Q = "", tooth_height: Q = "", allowable_pressure: Q = "",
    load_sharing: Annotated[float, Field(description="Spline flank utilization f in (0,1], default .75; an assumed uneven-load allowance, not a standards factor.")] = .75,
    unit_system: System = "metric",
) -> ToolResult:
    inputs = dict(locals())

    def compute():
        r = Result(unit_system, {
            "parallel_key": "tau=2T/(d*b*L); bearing=4T/(d*h*L); Lshear=2T/(d*b*Sshear); Lbearing=4T/(d*h*Sbearing); Sshear=given or 0.577Sy",
            "spline": "A=L*h (one loaded flank per tooth); Tcapacity=n*p*A*r_mean*f; actual p=T/(n*A*r_mean*f)",
        }, "Static uniform key load, half key height engaged in hub. Strength inputs are allowable stresses for the desired criterion. If only yield is given, shear allowable=0.577Sy and bearing allowable=Sy. Spline tooth height is effective radial contact height, one flank per tooth; f=0.75 assumes imperfect load sharing. No standard key selection, notch fatigue, fretting, hub splitting, spline tooth bending, flank pressure distribution or shaft weakening calculation.",
                   "Positive dimensions. Key width,height < shaft diameter; warn L/d>1.5 or L/d<0.5. Spline f in (0,1], 1..100000 teeth, effective height<2*r_mean; straight-sided and involute use the same projected bearing-area model.")
        kind = choice(joint_type, "joint_type", ("key", "straight_spline", "involute_spline"))
        l = q(length, "length", "length")
        t = q(torque, "torque", "moment", optional=True)
        n = count(tooth_count, "tooth_count")
        share = number(load_sharing, "load_sharing", 0, 1, positive=True)
        if kind == "key":
            if any((mean_radius, tooth_height, allowable_pressure)) or n != 1 or share != .75:
                invalid("joint_type", "spline parameters cannot be used for a parallel key")
            if t is None:
                invalid("torque", "parallel key requires torque")
            d = q(shaft_diameter, "shaft_diameter", "length")
            b = q(width, "width", "length")
            h = q(height, "height", "length")
            if b >= d or h >= d:
                invalid("width" if b >= d else "height", "key width and height must be smaller than shaft diameter")
            sy = q(yield_strength, "yield_strength", "stress", optional=True)
            ss = q(shear_strength, "shear_strength", "stress", optional=True)
            bs = q(bearing_strength, "bearing_strength", "stress", optional=True)
            ss = ss if ss is not None else .577*sy if sy is not None else None
            bs = bs if bs is not None else sy
            tau, bearing = 2*t/(d*b*l), 4*t/(d*h*l)
            r.add("shear_stress", tau, "stress")
            r.add("bearing_stress", bearing, "stress")
            r.safety("shear_safety_factor", ss, tau)
            r.safety("bearing_safety_factor", bs, bearing)
            if ss is not None:
                r.add("minimum_length_shear", 2*t/(d*b*ss), "length")
            if bs is not None:
                r.add("minimum_length_bearing", 4*t/(d*h*bs), "length")
            if not .5 <= l/d <= 1.5:
                r.warnings.append("Key L/d outside 0.5..1.5; uniform load distribution needs review.")
            return r.done("shear_stress")
        if any((shaft_diameter, width, height, yield_strength, shear_strength, bearing_strength)):
            invalid("joint_type", "parallel-key parameters cannot be used for a spline")
        rm = q(mean_radius, "mean_radius", "length")
        h = q(tooth_height, "tooth_height", "length")
        allow = q(allowable_pressure, "allowable_pressure", "stress")
        if h >= 2*rm:
            invalid("tooth_height", "effective tooth height must be below mean diameter")
        capacity = n*allow*l*h*rm*share
        r.add("torque_capacity", capacity, "moment")
        r.add("effective_bearing_area", n*l*h*share, "area")
        if t is not None:
            r.add("bearing_pressure", t/(n*l*h*rm*share), "stress")
            r.add("safety_factor", capacity/t)
        r.warnings.append("Spline capacity uses projected flank area and assumed load sharing; tooth bending and standard spline ratings are not evaluated.")
        return r.done("torque_capacity")
    return run("key", inputs, compute)
