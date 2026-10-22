"""Calorically perfect gas relations, solved on explicit Mach branches."""

import math
from typing import Annotated

from pydantic import Field

from ._common import Density, Kinematic, Result, Viscosity, bisect, choice, fluid, invalid, number, q, run
from ._server import server


def area_ratio(m, g):
    return math.exp(-math.log(m)+(g+1)/(2*(g-1))*math.log((2+(g-1)*m*m)/(g+1)))


def pm(m, g):
    z = math.sqrt((m-1)*(m+1))
    if z < .01:
        ratio = (g-1)/(g+1)
        return sum((-1)**k*(ratio**k-1)*z**(2*k+1)/(2*k+1) for k in range(1, 8))
    return math.sqrt((g+1)/(g-1))*math.atan(math.sqrt((g-1)/(g+1))*z)-math.atan(z)


def shock(m, g):
    p = 1+2*g/(g+1)*(m*m-1)
    rho = (g+1)*m*m/((g-1)*m*m+2)
    m2 = math.sqrt((1+(g-1)*m*m/2)/(g*m*m-(g-1)/2))
    logp0 = g/(g-1)*math.log(rho)+1/(g-1)*math.log((g+1)/(2*g*m*m-(g-1)))
    return {"downstream_mach": m2, "pressure_ratio": p, "density_ratio": rho,
            "temperature_ratio": p/rho, "stagnation_pressure_ratio": math.exp(logp0)}


@server.tool(description="Calorically perfect ideal-gas isentropic, normal/oblique shock, Prandtl-Meyer, Fanno, Rayleigh and nozzle mass-flow relations. NACA 1135 formulas; bracketed branch inverses. 1.01<=gamma<=2, 1e-6<=Mach<=50; attached shocks only. No real-gas or viscous nozzle model.")
def compressible(
    mode: Annotated[str, Field(description="isentropic, normal_shock, oblique_shock, expansion, fanno, rayleigh, or nozzle.")] = "isentropic",
    mach: Annotated[float, Field(description="Mach number, e.g. 2; 0 selects inverse area/pressure input for isentropic; nozzle ignores it.")] = 0,
    gamma: Annotated[float, Field(description="Constant heat-capacity ratio, e.g. 1.4; all modes, supported 1.01..2.")] = 1.4,
    gas_constant: Annotated[str, Field(description="Specific ideal-gas constant, e.g. '287.05 J/kg/K'; needed for nozzle; no gas-name lookup.")] = "287.05 J/kg/K",
    area_ratio_input: Annotated[float, Field(description="A/A*, e.g. 1.6875; isentropic inverse only with mach=0; 0 omits.")] = 0,
    pressure_ratio_input: Annotated[float, Field(description="p0/p, e.g. 7.8244; isentropic inverse only with mach=0; 0 omits.")] = 0,
    branch: Annotated[str, Field(description="subsonic/supersonic for area inversion; weak/strong for oblique shock.")] = "subsonic",
    deflection: Annotated[str, Field(description="Flow turning angle, e.g. '10 deg'; required for oblique_shock and expansion; '' omits in other modes.")] = "",
    stagnation_pressure: Annotated[str, Field(description="Absolute nozzle reservoir pressure, e.g. '2 bar'; required for nozzle; '' omits in relation modes.")] = "",
    stagnation_temperature: Annotated[str, Field(description="Absolute nozzle reservoir temperature, e.g. '20 degC'; required for nozzle; '' omits otherwise.")] = "",
    back_pressure: Annotated[str, Field(description="Absolute nozzle outlet pressure, e.g. '1 bar'; required for nozzle, may be zero; '' omits otherwise.")] = "",
    area: Annotated[str, Field(description="Nozzle throat area, e.g. '1 cm^2'; required for nozzle; '' omits in other modes.")] = "",
    discharge_coefficient: Annotated[float, Field(description="Nozzle discharge coefficient Cd in (0,1], e.g. 0.98; nozzle only.")] = 1,
    density: Density = "1.2 kg/m^3",
    dynamic_viscosity: Viscosity = "18.1 uPa*s",
    kinematic_viscosity: Kinematic = "",
):
    inputs = dict(locals())

    def compute(_):
        choice(mode, "mode", ("isentropic", "normal_shock", "oblique_shock", "expansion", "fanno", "rayleigh", "nozzle"))
        if mode not in ("isentropic", "oblique_shock") and branch != "subsonic":
            invalid("branch", "branch selection only applies to isentropic inverses and oblique shocks")
        g = number(gamma, "gamma", 1.01, 2)
        gas_r = q(gas_constant, "gas_constant", "gas_constant")
        rho, mu = fluid(density, dynamic_viscosity, kinematic_viscosity)
        r = Result({"isentropic": "t=1+(gamma-1)M^2/2; p0/p=t^(gamma/(gamma-1)); A/A*=(2t/(gamma+1))^((gamma+1)/(2(gamma-1)))/M",
                    "shock": "NACA 1135 normal shock and theta-beta-M; oblique uses upstream normal Mach",
                    "Fanno": "4f_F L*/D=(1-M^2)/(gamma M^2)+(gamma+1)/(2gamma) ln((gamma+1)M^2/(2+(gamma-1)M^2))",
                    "Rayleigh": "p/p*=(gamma+1)/(1+gamma M^2); T/T*=M^2(p/p*)^2",
                    "nozzle": "mdot=Cd A p0 sqrt(gamma/(R T0)) (2/(gamma+1))^((gamma+1)/(2(gamma-1))) when choked"},
                   "Constant gamma and R, continuum ideal gas. Density/viscosity are caller reference properties, not used in inviscid ratios.",
                   "Mach 1e-6..50; gamma 1.01..2. Shock upstream Mach >=1. No detached shock or variable heat capacities.")
        r.data["reference_properties"] = {"density_kg_m3": rho, "dynamic_viscosity_Pa_s": mu}
        ainput = number(area_ratio_input, "area_ratio_input", 0, 1e12)
        pinput = number(pressure_ratio_input, "pressure_ratio_input", 0, 1e15)
        m = number(mach, "mach", 0, 50)
        turn = q(deflection, "deflection", "angle", zero=True, optional=True)
        nozzle_inputs = [stagnation_pressure, stagnation_temperature, back_pressure, area]
        cd = number(discharge_coefficient, "discharge_coefficient", 1e-9, 1)
        if mode != "nozzle" and (any(nozzle_inputs) or cd != 1):
            invalid("stagnation_pressure", "nozzle quantities and Cd require nozzle mode")
        if mode not in ("oblique_shock", "expansion") and turn is not None:
            invalid("deflection", "deflection requires oblique_shock or expansion mode")
        if mode != "isentropic" and (ainput or pinput):
            invalid("area_ratio_input", "inverse inputs require isentropic mode")
        if mode == "nozzle":
            if m != 0:
                invalid("mach", "nozzle solves choking from pressures; leave mach=0")
            p0 = q(stagnation_pressure, "stagnation_pressure", "pressure")
            t0 = q(stagnation_temperature, "stagnation_temperature", "temperature", absolute_temperature=True)
            pb = q(back_pressure, "back_pressure", "pressure", zero=True)
            a = q(area, "area", "area")
            if pb > p0:
                invalid("back_pressure", "back pressure must not exceed reservoir pressure")
            critical = (2/(g+1))**(g/(g-1))
            ratio = pb/p0
            choked = ratio <= critical
            if choked:
                flux = math.sqrt(g/(gas_r*t0))*(2/(g+1))**((g+1)/(2*(g-1)))
            elif pb == p0:
                flux = 0.
            else:
                log_ratio = math.log1p((pb-p0)/p0)
                flux = math.sqrt(2*g/(gas_r*t0*(g-1))*math.exp(2/g*log_ratio)*(-math.expm1((g-1)/g*log_ratio)))
            r.add("mass_flow", cd*a*p0*flux, "kg/s")
            r.add("critical_pressure_ratio", critical)
            r.add("stagnation_density", p0/(gas_r*t0), "kg/m^3")
            r.data["choked"] = choked
            return r.done("mass_flow")
        if mode == "isentropic":
            choice(branch, "branch", ("subsonic", "supersonic"))
            if sum(bool(x) for x in (m, ainput, pinput)) != 1:
                invalid("mach", "provide exactly one of mach, area_ratio_input or pressure_ratio_input")
            if ainput:
                if ainput < 1:
                    invalid("area_ratio_input", "A/A* must be at least 1")
                lo, hi = (1e-6, 1.) if branch == "subsonic" else (1., 50.)
                m = bisect(lambda x: area_ratio(x, g)-ainput, lo, hi, "area_ratio_input")
            if pinput:
                if pinput <= 1:
                    invalid("pressure_ratio_input", "p0/p must exceed 1 for positive Mach")
                m = math.sqrt(2/(g-1)*math.expm1((g-1)/g*math.log(pinput)))
        if not 1e-6 <= m <= 50:
            invalid("mach", "supported Mach range is 1e-6..50", "input.out_of_range")
        r.add("mach", m)
        if mode == "isentropic":
            t = 1+(g-1)*m*m/2
            r.add("stagnation_temperature_ratio", t)
            r.add("stagnation_pressure_ratio", t**(g/(g-1)))
            r.add("stagnation_density_ratio", t**(1/(g-1)))
            r.add("area_ratio", area_ratio(m, g))
        elif mode == "normal_shock":
            if m < 1:
                invalid("mach", "normal shock requires Mach >=1")
            for name, value in shock(m, g).items():
                r.add(name, value)
        elif mode == "oblique_shock":
            choice(branch, "branch", ("weak", "strong"))
            if m <= 1 or turn is None:
                invalid("deflection", "attached oblique shock requires Mach>1 and a deflection")
            delta = (m-1)*(m+1)
            m_squared = m*m
            # w=M^2*cos(beta)^2/(M^2-1) spans [0,1] even as M approaches 1.
            # Rationalize 1-sin(beta_max)^2 in the closed-form peak equation.
            radical = math.sqrt((g+1)*((g+1)*m_squared*m_squared+8*(g-1)*m_squared+16))
            peak_w = 2*((g-1)*m_squared+2)/((3*g-1)*m_squared+4+radical)

            def theta(w):
                cotangent = math.sqrt(delta*w/(m_squared-delta*w))
                return math.atan2(2*cotangent*delta*(1-w), (g-1)*m_squared+2+2*delta*w)

            max_turn = theta(peak_w)
            limit_tolerance = 4*math.ulp(max_turn)
            if turn > max_turn+limit_tolerance:
                invalid("deflection", "detached shock: deflection exceeds attached maximum")
            if abs(turn-max_turn) <= limit_tolerance:
                w = peak_w
            elif turn == 0:
                w = 1. if branch == "weak" else 0.
            else:
                lo, hi = (peak_w, 1.) if branch == "weak" else (0., peak_w)
                w = bisect(lambda x: theta(x)-turn, lo, hi, "deflection", rtol=2e-14, atol=0.)
            if abs(theta(w)-turn) > max(limit_tolerance, 1e-10*turn):
                invalid("deflection", "shock-angle root cannot resolve the requested deflection", "input.out_of_range")
            beta = math.atan2(math.sqrt(m_squared-delta*w), math.sqrt(delta*w))
            out = shock(max(1., m*math.sin(beta)), g)
            out["downstream_mach"] /= math.sin(beta-turn)
            for name, value in out.items():
                r.add(name, value)
            r.add("shock_angle", beta, "rad")
            r.add("maximum_deflection", max_turn, "rad")
        elif mode == "expansion":
            if m < 1 or turn is None:
                invalid("deflection", "expansion requires Mach>=1 and a turning angle")
            target = pm(m, g)+turn
            m2 = bisect(lambda x: pm(x, g)-target, m, 50., "deflection")
            t_ratio = (1+(g-1)*m*m/2)/(1+(g-1)*m2*m2/2)
            r.add("downstream_mach", m2)
            r.add("prandtl_meyer_angle", pm(m, g), "rad")
            r.add("temperature_ratio", t_ratio)
            r.add("pressure_ratio", t_ratio**(g/(g-1)))
        elif mode == "fanno":
            delta = (m-1)*(m+1)
            # log1p and a near-sonic series avoid subtracting equal O(delta) terms.
            c = (g-1)/(g+1)
            if abs(delta) < 1e-3:
                length = sum((-1)**k*(1/g-(g+1)/(2*g)*(1-c**k)/k)*delta**k for k in range(2, 10))
            else:
                length = -delta/(g*m*m)+(g+1)/(2*g)*(math.log1p(delta)-math.log1p(c*delta))
            temp = (g+1)/(2+(g-1)*m*m)
            r.add("fanno_parameter", length)
            r.add("temperature_star_ratio", temp)
            r.add("pressure_star_ratio", math.sqrt(temp)/m)
        else:
            pressure = (g+1)/(1+g*m*m)
            temp = m*m*pressure*pressure
            r.add("pressure_star_ratio", pressure)
            r.add("temperature_star_ratio", temp)
            r.add("stagnation_temperature_star_ratio", temp*(2+(g-1)*m*m)/(g+1))
            r.add("stagnation_pressure_star_ratio", pressure*((2+(g-1)*m*m)/(g+1))**(g/(g-1)))
        return r.done("mach")

    return run("compressible", inputs, compute)
