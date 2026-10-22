"""Review regressions with independent conservation laws and Decimal references."""

import math
from decimal import Decimal, localcontext

import pytest

from eng_toolkit.domains.fluids.compressible import compressible
from eng_toolkit.domains.fluids.external_flow import external_flow
from eng_toolkit.domains.fluids.open_channel import open_channel
from eng_toolkit.domains.fluids.pipe_network import pipe_network
from eng_toolkit.domains.fluids.pump_system import pump_system
from eng_toolkit.domains.fluids.surge import surge
from tests.fluids_testing import CHANNEL, EXTERNAL, G, NETWORK, NOZZLE, PUMP, SURGE, call, ok, table, v


TINY_PUMP = "p,0 m^3/s,1e-9 m,0.8;p,1e-14 m^3/s,0 m,0.8"


@pytest.mark.parametrize("slope,boundary,profile", [
    (.001, 1.2, "M1"), (.001, .6, "M2"), (.01, .4, "S2"),
])
def test_coarse_profile_keeps_normal_and_critical_sides(slope, boundary, profile):
    result = ok(open_channel, **CHANNEL, mode="profile", bed_slope=slope,
                boundary_depth=f"{boundary} m", profile_length="10000 m", profile_steps=1)
    rows = table(result)
    normal = v(result, "normal_depth")
    # Independent rectangular-section Manning and critical-depth equations.
    area = 2*normal
    assert area*(area/(2+2*normal))**(2/3)*math.sqrt(slope)/.015 == pytest.approx(2., rel=1e-13)
    critical = (1/G)**(1/3)
    depths = [float(row["depth_m"]) for row in rows]
    assert all((depth-normal)*(boundary-normal) > 0 for depth in depths)
    assert all((depth-critical)*(boundary-critical) > 0 for depth in depths)
    assert all(row["profile"] == profile for row in rows)
    assert all((two-one)*(normal-boundary) >= 0 for one, two in zip(depths, depths[1:]))
    assert depths[-1] == pytest.approx(normal, rel=1e-12)
    assert v(result, "profile_end_depth") == depths[-1]
    assert float(rows[-1]["x_m"]) == (-10000 if boundary > critical else 10000)
    assert 1 < result.data["profile_steps"] == len(rows)-1 <= 500
    assert result.data["profile_step_attempts"] <= 500
    assert 0 < result.data["profile_refinements"] <= 12
    lengths = [abs(float(b["x_m"])-float(a["x_m"])) for a, b in zip(rows, rows[1:])]
    assert result.data["minimum_step_length_m"] == min(lengths)
    assert result.data["maximum_step_length_m"] == max(lengths)
    # Each accepted subdivision still solves the standard-step energy equation.
    for a, b in zip(rows, rows[1:]):
        y1, y2 = float(a["depth_m"]), float(b["depth_m"])
        dx = float(b["x_m"])-float(a["x_m"])
        sf = lambda y: (.015/(y*(y/(1+y))**(2/3)))**2
        energy = lambda y: y+1/(2*G*y*y)
        assert energy(y2)-energy(y1) == pytest.approx((slope-(sf(y1)+sf(y2))/2)*dx, abs=2e-12)


@pytest.mark.parametrize("slope,boundary", [(.001, 1.2), (.001, .6), (.01, .4)])
def test_coarse_profile_end_against_independent_gvf_integration(slope, boundary):
    integrate = pytest.importorskip("scipy.integrate")
    critical = (1/G)**(1/3)
    end = -10000 if boundary > critical else 10000

    def gvf(x, depth):
        y = depth[0]
        # dE/dy=1-Fr^2; dE/dx=S0-Sf, independently of the standard-step solver.
        sf = (.015/(y*(y/(1+y))**(2/3)))**2
        return [(slope-sf)/(1-1/(G*y**3))]

    reference = integrate.solve_ivp(gvf, (0, end), [boundary], method="DOP853",
                                    rtol=2e-12, atol=2e-14, max_step=10)
    assert reference.success, reference.message
    result = ok(open_channel, **CHANNEL, mode="profile", bed_slope=slope,
                boundary_depth=f"{boundary} m", profile_length="10000 m", profile_steps=1)
    assert v(result, "profile_end_depth") == pytest.approx(reference.y[0, -1], rel=1e-11)


@pytest.mark.parametrize("slope,boundary", [(.001, ".2 m"), (.01, "1 m")])
def test_profile_refuses_reach_that_runs_into_critical_control(slope, boundary):
    result = call(open_channel, **CHANNEL, mode="profile", bed_slope=slope,
                  boundary_depth=boundary, profile_length="10000 m", profile_steps=1)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] in ("/profile_steps", "/profile_length")
    assert "branch" in result.error.message


@pytest.mark.parametrize("slope,boundary,steps,pointer,cap", [
    (.01, ".4 m", 1, "/profile_length", "12 step bisections"),
    (.001, "1.2 m", 500, "/profile_steps", "500 step attempts"),
])
def test_profile_refuses_normal_depth_crossing_when_refinement_budget_is_exhausted(slope, boundary, steps, pointer, cap):
    result = call(open_channel, **CHANNEL, mode="profile", bed_slope=slope,
                  boundary_depth=boundary, profile_length="1000000 m", profile_steps=steps)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == pointer
    assert cap in result.error.message


@pytest.mark.parametrize("tool,args", [
    (pipe_network, NETWORK), (pump_system, PUMP), (open_channel, CHANNEL),
    (compressible, {"mach": 2}), (surge, SURGE), (external_flow, EXTERNAL),
])
@pytest.mark.parametrize("unit", ["degF", "degC", "celsius", "degree_Celsius", "fahrenheit", "degree_Fahrenheit"])
def test_all_tools_refuse_offset_viscosity_composites(tool, args, unit):
    result = call(tool, **args, dynamic_viscosity=f"1 mPa*s*{unit}/K")
    assert not result.ok
    assert result.error.family == "input.invalid"
    assert result.error.details["param"] == "/dynamic_viscosity"


@pytest.mark.parametrize("param,raw", [
    ("density", "1000 kg/m^3*degC/degC"),
    ("dynamic_viscosity", "1 mPa*s*degF^0"),
    ("gas_constant", "287.05 J/kg/degree_Celsius"),
    ("area", "1 cm^2*fahrenheit/degF"),
    ("stagnation_temperature", "300 K*degC/degC"),
])
def test_cancelled_offset_tokens_and_other_quantity_parameters_are_checked(param, raw):
    result = call(compressible, **{**NOZZLE, param: raw})
    assert not result.ok
    assert result.error.details["param"] == "/"+param


def test_offset_check_reaches_record_fields():
    result = call(pipe_network, **{**NETWORK, "pipes": "p,r,j,10 m*degC/K,50 mm,0 mm,0"})
    assert not result.ok
    assert result.error.details["param"] == "/pipes/0/3"


@pytest.mark.parametrize("unit,factor", [("rankine", 5/9), ("delta_degF", 5/9), ("delta_degC", 1)])
def test_multiplicative_temperature_units_remain_valid(unit, factor):
    result = ok(compressible, mach=2, dynamic_viscosity=f"1 mPa*s*{unit}/K")
    assert result.data["reference_properties"]["dynamic_viscosity_Pa_s"] == pytest.approx(.001*factor, rel=1e-14)


@pytest.mark.parametrize("temperature", ["20 degC", "68 degF", "20 celsius", "68 degree_Fahrenheit", "527.67 rankine"])
def test_plain_absolute_offset_and_rankine_temperatures(temperature):
    result = ok(compressible, **{**NOZZLE, "stagnation_temperature": temperature})
    assert v(result, "stagnation_density") == pytest.approx(200000/(287.05*293.15), rel=1e-14)


def test_tiny_network_demand_is_not_a_converged_zero_flow():
    result = ok(pipe_network, nodes="r,0 m,head,20 m;j,0 m,demand,1e-15 m^3/s",
                pipes="p,r,j,10 m,10 um,0 m,0")
    # Poiseuille pressure loss divided by rho*g. Re=4Q*rho/(pi*D*mu)<<2000.
    loss = 128*.001*10*1e-15/(1000*G*math.pi*(10e-6)**4)
    pipe = result.data["pipes"][0]
    assert pipe["flow_m3_s"] == pytest.approx(1e-15, rel=1e-9, abs=0)
    assert pipe["headloss_m"] == pytest.approx(loss, rel=1e-9)
    assert result.data["nodes"][1]["head_m"] == pytest.approx(20-loss, rel=1e-10)
    assert v(result, "iterations") > 1
    assert result.data["mass_tolerance_m3_s"] == pytest.approx(1e-24, rel=1e-9, abs=0)
    assert abs(pipe["flow_m3_s"]-1e-15) <= result.data["mass_tolerance_m3_s"]
    assert v(result, "mass_balance_residual") <= result.data["mass_tolerance_m3_s"]


def test_network_refuses_unresolvable_head_difference():
    # The required head change is below an ulp of the reservoir datum.
    result = call(pipe_network, nodes="r,0 m,head,1000000 m;j,0 m,demand,1e-15 m^3/s",
                  pipes="p,r,j,10 m,1 m,0 m,0")
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == "/nodes"


@pytest.mark.parametrize("combination,count", [("single", 1), ("series", 2), ("parallel", 2)])
@pytest.mark.parametrize("speed,trim", [(1., 1.), (.2, .9), (3., 1.)])
def test_tiny_pump_intersection_and_affinity_combinations(combination, count, speed, trim):
    points = TINY_PUMP if count == 1 else TINY_PUMP+";"+TINY_PUMP.replace("p,", "q,")
    result = ok(pump_system, pump_points=points, combination=combination,
                static_head="0 m", system_coefficient="1e20 s^2/m^5", speed_ratio=speed, diameter_ratio=trim)
    with localcontext() as ctx:
        ctx.prec = 70
        scale = Decimal.from_float(speed)*Decimal.from_float(trim)
        qmax = Decimal("1e-14")*scale
        hmax = Decimal("1e-9")*scale**2
        if combination == "series":
            hmax *= count
        elif combination == "parallel":
            qmax *= count
        # H=hmax-slope*Q=K*Q^2; rationalized positive quadratic root.
        slope = hmax/qmax
        expected_q = 2*hmax/(slope+(slope*slope+4*Decimal("1e20")*hmax).sqrt())
        expected_h = Decimal("1e20")*expected_q**2
    flow, head = v(result, "operating_flow"), v(result, "operating_head")
    assert flow == pytest.approx(float(expected_q), rel=1e-11, abs=0)
    assert head == pytest.approx(float(expected_h), rel=1e-11, abs=0)
    assert head == pytest.approx(1e20*flow*flow, rel=1e-11, abs=0)
    assert head == pytest.approx(float(hmax)-float(slope)*flow, rel=1e-11, abs=0)
    for pump in result.data["pumps"]:
        assert pump["head_m"] == pytest.approx(1e-9*float(scale)**2-1e5*float(scale)*pump["flow_m3_s"], rel=1e-11, abs=0)


def decimal_theta_max(mach, gamma=1.4):
    """Direct closed-form sin^2(beta_max), evaluated at 80 digits without rationalization."""
    with localcontext() as ctx:
        ctx.prec = 80
        m, g = Decimal.from_float(mach), Decimal.from_float(gamma)
        s = ((g+1)*m*m-4+((g+1)*((g+1)*m**4+8*(g-1)*m*m+16)).sqrt())/(4*g*m*m)
        tangent = 2*((1-s)/s).sqrt()*(m*m*s-1)/(m*m*(g+1-2*s)+2)
        # atan(t)=t-t^3/3+...; all test deflections are below 1e-8 rad.
        return float(sum((-1)**k*tangent**(2*k+1)/Decimal(2*k+1) for k in range(12)))


def decimal_theta_at_beta(mach, beta):
    """Evaluate the original theta-beta-M relation, retaining near-sonic subtraction."""
    with localcontext() as ctx:
        ctx.prec = 80
        m, b, g = map(Decimal.from_float, (mach, beta, 1.4))
        sine = sum((-1)**k*b**(2*k+1)/Decimal(math.factorial(2*k+1)) for k in range(50))
        cosine = sum((-1)**k*b**(2*k)/Decimal(math.factorial(2*k)) for k in range(50))
        tangent = 2*cosine/sine*(m*m*sine*sine-1)/(m*m*(g+2*cosine*cosine-1)+2)
        return float(sum((-1)**k*tangent**(2*k+1)/Decimal(2*k+1) for k in range(12)))


def test_exact_near_sonic_detached_shock_is_refused():
    maximum = decimal_theta_max(1.000000001)
    assert maximum == pytest.approx(2.868876906493068e-14, rel=1e-14, abs=0)
    result = call(compressible, mode="oblique_shock", mach=1.000000001, branch="weak", deflection="1e-12 rad")
    assert not result.ok
    assert result.error.details["param"] == "/deflection"
    assert "detached" in result.error.message


@pytest.mark.parametrize("mach", [math.nextafter(1., 2.), 1.000000001, 1.0000001])
@pytest.mark.parametrize("branch", ["weak", "strong"])
def test_near_sonic_attached_limit_uses_relative_precision(mach, branch):
    maximum = decimal_theta_max(mach)
    below = ok(compressible, mode="oblique_shock", mach=mach, branch=branch, deflection=f"{maximum*.5} rad")
    assert v(below, "maximum_deflection") == pytest.approx(maximum, rel=2e-15, abs=0)
    beta = v(below, "shock_angle")
    assert math.asin(1/mach) < beta < math.pi/2
    # The exported beta has absolute float rounding near pi/2. Propagate that
    # angular resolution relative to the shrinking Mach-angle interval.
    angle_error = 20*math.ulp(beta)/math.sqrt((mach-1)*(mach+1))
    assert decimal_theta_at_beta(mach, beta) == pytest.approx(maximum*.5, rel=angle_error, abs=0)
    # A 1e-10 relative excess is far below the former absolute 1e-12 allowance.
    above = call(compressible, mode="oblique_shock", mach=mach, branch=branch, deflection=f"{maximum*(1+1e-10)} rad")
    assert not above.ok
    assert above.error.details["param"] == "/deflection"
    assert "detached" in above.error.message
    at_limit = ok(compressible, mode="oblique_shock", mach=mach, branch=branch, deflection=f"{maximum} rad")
    assert v(at_limit, "maximum_deflection") == pytest.approx(maximum, rel=2e-15, abs=0)


def test_nozzle_nearly_equal_pressures_against_decimal_parsed_floats():
    result = ok(compressible, mode="nozzle", area="1 cm^2", stagnation_pressure="200000 Pa",
                stagnation_temperature="300 K", back_pressure="199999.99999999997 Pa")
    with localcontext() as ctx:
        ctx.prec = 80
        # Preserve the actual binary inputs, rather than the decimal spelling of pb.
        p0, pb = Decimal.from_float(200000.), Decimal.from_float(float("199999.99999999997"))
        g, gas_r = Decimal.from_float(1.4), Decimal.from_float(287.05)
        ratio = pb/p0
        difference = ratio**(2/g)-ratio**((g+1)/g)
        expected = Decimal.from_float(1e-4)*p0*(2*g/(gas_r*300*(g-1))*difference).sqrt()
    assert v(result, "mass_flow") == pytest.approx(float(expected), rel=2e-15, abs=0)
    assert not result.data["choked"]
