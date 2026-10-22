"""Continuum drag correlations and a bracketed buoyancy/drag balance."""

import math
from typing import Annotated

from pydantic import Field

from ._common import Density, G, Kinematic, Result, Viscosity, bisect, choice, fluid, invalid, q, run
from ._server import server


def sphere_cd(re):
    if not 1e-12 <= re <= 2e5:
        invalid("velocity", "Clift-Gauvin sphere correlation requires 1e-12<=Re<=2e5", "input.out_of_range")
    return 24/re*(1+.15*re**.687)+.42/(1+42500/re**1.16)


@server.tool(description="External Reynolds number and drag: Clift-Gauvin sphere, White cylinder, Blasius/1-5-power flat plate; bracketed sphere terminal velocity. Stokes only for Re<0.1. Smooth isolated bodies, incompressible continuum; sphere Re<=2e5, cylinder 1..2e5, plate 1e3..1e7; no added mass.")
def external_flow(
    shape: Annotated[str, Field(description="sphere, cylinder, or flat_plate; terminal_velocity requires sphere.")] = "sphere",
    mode: Annotated[str, Field(description="drag for a prescribed speed, or terminal_velocity for settling/rising under buoyancy.")] = "drag",
    length: Annotated[str, Field(description="Sphere/cylinder diameter or plate streamwise length, e.g. '10 mm'; required in all modes.")] = "",
    velocity: Annotated[str, Field(description="Nonnegative free-stream speed, e.g. '2 m/s'; required for drag, '' required for terminal_velocity.")] = "",
    area: Annotated[str, Field(description="Cylinder projected area or plate wetted area, e.g. '1 m^2'; required for those shapes; '' for sphere.")] = "",
    solid_density: Annotated[str, Field(description="Sphere material density, e.g. '2500 kg/m^3'; required for terminal_velocity; '' omits for drag.")] = "",
    plate_regime: Annotated[str, Field(description="auto, laminar, turbulent, or mixed; plate only; transition Re=5e5, fully turbulent assumes a tripped leading edge.")] = "auto",
    density: Density = "1000 kg/m^3",
    dynamic_viscosity: Viscosity = "1 mPa*s",
    kinematic_viscosity: Kinematic = "",
):
    inputs = dict(locals())

    def compute(_):
        choice(shape, "shape", ("sphere", "cylinder", "flat_plate"))
        choice(mode, "mode", ("drag", "terminal_velocity"))
        choice(plate_regime, "plate_regime", ("auto", "laminar", "turbulent", "mixed"))
        if shape != "flat_plate" and plate_regime != "auto":
            invalid("plate_regime", "plate_regime only applies to flat_plate")
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        d = q(length, "length", "length")
        r = Result({"drag": "F=0.5*rho*v^2*Cd*A; Re=rho*v*L/mu",
                    "Clift-Gauvin": "Cd=24/Re*(1+0.15 Re^0.687)+0.42/(1+42500/Re^1.16)",
                    "White cylinder": "Cd=1+10/Re^(2/3)",
                    "plate": "Cf_lam=1.328/sqrt(Re); Cf_turb=0.074/Re^0.2; Cf_mixed=Cf_turb-1742/Re",
                    "Stokes": "F=3*pi*mu*d*v; vt=g*d^2*(rho_s-rho)/(18*mu), valid Re<0.1"},
                   "Isolated smooth bodies, Newtonian incompressible continuum, no walls or free-surface interference. Plate area is total wetted area.",
                   "Sphere 1e-12<=Re<=2e5; cylinder 1<=Re<=2e5; plate 1e3<=Re<=1e7, laminar <=5e5, mixed >=5e5.")
        if shape == "sphere":
            if area:
                invalid("area", "sphere projected area is computed from its diameter")
            a = math.pi*d*d/4
        else:
            a = q(area, "area", "area")
        if mode == "terminal_velocity":
            if shape != "sphere" or velocity:
                invalid("velocity", "terminal_velocity requires sphere and omitted velocity")
            solid = q(solid_density, "solid_density", "density")
            delta = solid-rho
            if delta == 0:
                r.add("terminal_velocity", 0, "m/s")
                r.add("reynolds_number", 0)
                r.data["direction"] = "neutral"
                return r.done("terminal_velocity")
            force = abs(delta)*G*math.pi*d**3/6
            # Solve for Re so the bracket exactly matches the correlation's limits.
            def balance(re):
                speed = re*mu/(rho*d)
                return .5*rho*speed*speed*sphere_cd(re)*a-force
            re = bisect(balance, 1e-12, 2e5, "solid_density", atol=1e-20)
            if abs(balance(re)) > force*1e-9:
                invalid("solid_density", "terminal force balance did not converge", "input.nonconvergence")
            speed = re*mu/(rho*d)
            sign = 1 if delta > 0 else -1
            r.add("terminal_velocity", sign*speed, "m/s")
            r.data["direction"] = "settling" if sign > 0 else "rising"
            stokes = G*d*d*delta/(18*mu)
            if rho*abs(stokes)*d/mu < .1:
                r.add("stokes_terminal_velocity", stokes, "m/s")
            else:
                r.warnings.append("Stokes terminal estimate omitted: its predicted Reynolds number is >=0.1.")
        else:
            if solid_density:
                invalid("solid_density", "solid_density requires terminal_velocity mode")
            speed = q(velocity, "velocity", "velocity", zero=True)
            re = rho*speed*d/mu
        r.add("reynolds_number", re)
        r.add("reference_area", a, "m^2")
        if speed == 0:
            r.add("drag_force", 0, "N")
            r.warnings.append("Zero velocity: drag coefficient and boundary-layer thickness are undefined and omitted.")
            return r.done("drag_force")
        if shape == "sphere":
            cd = sphere_cd(re)
            if re < .1:
                r.add("stokes_drag", 3*math.pi*mu*d*speed, "N")
            else:
                r.warnings.append("Stokes drag omitted because Re>=0.1.")
        elif shape == "cylinder":
            if not 1 <= re <= 2e5:
                invalid("velocity", "White cylinder fit is restricted to 1<=Re<=2e5", "input.out_of_range")
            cd = 1+10/re**(2/3)
        else:
            regime = ("laminar" if re <= 5e5 else "mixed") if plate_regime == "auto" else plate_regime
            if not 1e3 <= re <= 1e7 or (regime == "laminar" and re > 5e5) or (regime in ("mixed", "turbulent") and re < 5e5):
                invalid("plate_regime", "requested boundary-layer correlation is outside its Reynolds validity range", "input.out_of_range")
            if regime == "laminar":
                cd, thickness = 1.328/math.sqrt(re), 5*d/math.sqrt(re)
            else:
                cd = .074/re**.2-(1742/re if regime == "mixed" else 0)
                thickness = .37*d/re**.2
            r.add("boundary_layer_thickness", thickness, "m")
            r.data["plate_regime"] = regime
        r.add("drag_coefficient", cd)
        r.add("drag_force", .5*rho*speed*speed*cd*a, "N")
        return r.done("terminal_velocity" if mode == "terminal_velocity" else "drag_force")

    return run("external_flow", inputs, compute)
