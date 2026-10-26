"""Branch behavior, validity warnings and numerical edge cases."""

import math

import pytest

from eng_toolkit.domains.fluids.compressible import compressible
from eng_toolkit.domains.fluids.external_flow import external_flow
from eng_toolkit.domains.fluids.open_channel import open_channel
from eng_toolkit.domains.fluids.pipe_network import pipe_network
from eng_toolkit.domains.fluids.pump_system import pump_system
from eng_toolkit.domains.fluids.surge import surge
from tests.fluids_testing import CHANNEL, G, NETWORK, NOZZLE, SURGE, call, close, ok, table, v


def test_network_pump_points_and_valve_bypass():
    r = ok(pipe_network, nodes="r,0 m,head,0 m;j,0 m,demand,1 L/s", pumps="p,r,j,points,0 L/s:20 m|2 L/s:10 m,yes")
    # Linear interpolation at half the tabulated flow: H=20+(10-20)/2=15m.
    close(r.data["pumps"][0]["head_gain_m"], 15)
    r = ok(pipe_network, **{**NETWORK, "pipes": NETWORK["pipes"]+";q,r,j,10 m,50 mm,0 mm,0"}, valves="p,closed,0")
    close(r.data["pipes"][0]["flow_m3_s"], 0)
    assert r.data["pipes"][0]["headloss_m"] is None
    close(r.data["pipes"][1]["flow_m3_s"], .001, rel=1e-8)


def test_network_negative_pressure_reversal_and_velocity_warning():
    r = ok(pipe_network, nodes="r,0 m,head,0 m;j,0 m,demand,10 L/s", pipes="p,j,r,100 m,50 mm,0 mm,0", vapour_pressure="2 kPa")
    # Supplied orientation is opposite to the withdrawal flow; v=.01/(pi*.05^2/4)>3.
    close(r.data["pipes"][0]["flow_m3_s"], -.01, rel=1e-8)
    assert any("reverses" in w for w in r.warnings)
    assert any("3 m/s" in w for w in r.warnings)
    assert any("negative gauge" in w for w in r.warnings)
    assert any("cavitation" in w for w in r.warnings)


def test_darcy_transition_is_continuous_and_warned():
    flows = []
    for re in (1999.999, 2000.001, 3000, 3999.999, 4000.001):
        # Re=4Q/(pi D nu); D=.05, nu=1e-6.
        flow = re*math.pi*.05*1e-6/4
        r = ok(pipe_network, **{**NETWORK, "nodes": f"r,0 m,head,20 m;j,0 m,demand,{flow} m^3/s"})
        flows.append(r.data["pipes"][0]["darcy_factor"])
        assert any("transition blend" in w for w in r.warnings) == (2000 <= re < 4000)
    close(flows[0], flows[1], rel=2e-6)
    close(flows[3], flows[4], rel=2e-6)


def test_different_parallel_pumps_and_inactive_check_valve():
    points = "p,0 m^3/s,20 m,0.8;p,0.1 m^3/s,0 m,0.8;q,0 m^3/s,10 m,0.6;q,0.05 m^3/s,0 m,0.6"
    r = ok(pump_system, pump_points=points, combination="parallel", static_head="15 m", system_coefficient="0 s^2/m^5")
    # At 15m, p delivers (20-15)/200=.025m3/s; q is above its 10m shutoff and closes.
    close(v(r, "operating_flow"), .025)
    close(v(r, "efficiency"), .8)
    assert r.data["pumps"][1]["check_valve_closed"]
    both = ok(pump_system, pump_points=points, combination="parallel", static_head="5 m", system_coefficient="0 s^2/m^5")
    # At 5m, flows .075 and .025; weighted hydraulic/shaft ratio is .1/(.075/.8+.025/.6).
    close(v(both, "operating_flow"), .1)
    close(v(both, "efficiency"), .1/(.075/.8+.025/.6))


@pytest.mark.parametrize("slope,boundary,expected", [(0, "1 m", "H2"), (-.001, "1 m", "A2"), (.01, "1 m", "S1"), (.01, "0.4 m", "S2"), (.01, "0.1 m", "S3")])
def test_profile_classes(slope, boundary, expected):
    r = ok(open_channel, **CHANNEL, bed_slope=slope, mode="profile", boundary_depth=boundary, profile_length="0.2 m", profile_steps=10)
    assert r.data["profile_classification"] == expected
    assert len(table(r)) == 11


def test_critical_slope_profile_class():
    # At Q/b=1, yc=(1/g)^(1/3); choose S0=(nQ/(A R^(2/3)))^2 at yc so yn=yc.
    yc = (1/G)**(1/3)
    slope = (.03/(2*yc*(yc/(1+yc))**(2/3)))**2
    r = ok(open_channel, **CHANNEL, bed_slope=slope, mode="profile", boundary_depth="1 m", profile_length="1 m", profile_steps=10)
    assert r.data["profile_classification"] == "C1"


@pytest.mark.parametrize("anchoring,c1", [("expansion_joints", 1), ("restrained", .91), ("upstream_anchor", .85)])
def test_wave_anchoring_cases(anchoring, c1):
    r = ok(surge, **SURGE, anchoring=anchoring)
    # K D/(E e)=.55, a=sqrt(2.2e6/(1+.55*c1)).
    close(v(r, "wave_speed"), math.sqrt(2.2e6/(1+.55*c1)))


def test_moc_locations_and_matching_courant_input():
    a = math.sqrt(2.2e6/(1+.55*.91))
    # dx=1000/20=50m; Courant-one dt=50/a. Requested 211m rounds to 200m.
    r = ok(surge, **SURGE, duration="2 s", reservoir_head="200 m", reaches=20, locations="211 m;1000 m", time_step=f"{50/a} s")
    assert r.data["locations_m"] == [200, 1000]
    close(r.data["courant"], 1)
    assert set(table(r)[0]) == {"time_s", "head_m_at_200m", "head_m_at_1000m"}


def test_neutral_and_stopped_sphere_are_finite():
    neutral = ok(external_flow, mode="terminal_velocity", length="1 mm", solid_density="1000 kg/m^3")
    close(v(neutral, "terminal_velocity"), 0)
    stopped = ok(external_flow, length="1 mm", velocity="0 m/s")
    close(v(stopped, "drag_force"), 0)
    assert "drag_coefficient" not in stopped.values


def test_stokes_omission_and_explicit_turbulent_plate():
    r = ok(external_flow, mode="terminal_velocity", length="1 mm", solid_density="2000 kg/m^3")
    assert "stokes_terminal_velocity" not in r.values
    assert any("Stokes terminal estimate omitted" in w for w in r.warnings)
    plate = ok(external_flow, shape="flat_plate", length="1 m", area="1 m^2", velocity="1 m/s", plate_regime="turbulent")
    # Re=1e6 and a fully tripped plate omit the 1742/Re mixed correction.
    close(v(plate, "drag_coefficient"), .074/1e6**.2)


@pytest.mark.parametrize("raw", ["287 J/kg/degC", "287 J/kg/degree_Celsius", "287 J/kg/degF", "287 J/kg/degree_Fahrenheit"])
def test_gas_constant_refuses_offset_temperature_aliases(raw):
    r = call(compressible, **{**NOZZLE, "gas_constant": raw})
    assert not r.ok
    assert r.error.details["param"] == "/gas_constant"


@pytest.mark.parametrize("tool,args,param,raw", [
    (compressible, {"mach": 2}, "gamma", 1e308),
    (open_channel, CHANNEL, "manning_n", 1e308),
    (surge, SURGE, "initial_velocity", "100000 m/s"),
    (pipe_network, NETWORK, "max_iterations", 101),
    (surge, {**SURGE, "duration": "2 s", "reservoir_head": "200 m"}, "locations", ";".join("0 m" for _ in range(21))),
])
def test_additional_magnitude_and_resource_refusals(tool, args, param, raw):
    assert not call(tool, **{**args, param: raw}).ok


def test_sonic_expansion_series_preserves_small_angle():
    epsilon = 1e-8
    r = ok(compressible, mode="expansion", mach=1+epsilon, deflection="0 rad")
    # nu near M=1: 4sqrt(2)/(3(gamma+1))*(M-1)^1.5; leading-term relative error O(epsilon).
    expected = 4*math.sqrt(2)/(3*2.4)*epsilon**1.5
    close(v(r, "prandtl_meyer_angle"), expected, rel=1e-6, abs=1e-22)


def test_zero_efficiency_at_shutoff_is_accepted_and_only_there():
    # UAT F1: datasheets list 0 % at Q = 0.  Curve: head 20 - 200 Q, efficiency 0 -> 0.8 over 0..0.1 m3/s (eta = 8 Q), system 5 + 1000 Q^2.
    points = "p,0 m^3/s,20 m,0;p,0.1 m^3/s,0 m,0.8"
    r = ok(pump_system, pump_points=points, static_head="5 m", system_coefficient="1000 s^2/m^5")
    flow = (-200+math.sqrt(100000))/2000  # root of 20 - 200 Q = 5 + 1000 Q^2
    head = 20-200*flow
    close(v(r, "operating_flow"), flow)
    close(v(r, "efficiency"), 8*flow)
    close(v(r, "shaft_power"), 1000*G*flow*head/(8*flow))
    assert r.data["pumps"][0]["efficiency"] == pytest.approx(8*flow)
    # a closed pump (above its shut-off head) in a parallel set has no power and no division by its zero shut-off efficiency
    both = ok(pump_system, pump_points=points+";q,0 m^3/s,10 m,0;q,0.05 m^3/s,0 m,0.6", combination="parallel", static_head="15 m", system_coefficient="0 s^2/m^5")
    assert both.data["pumps"][1]["check_valve_closed"] and v(both, "efficiency") == pytest.approx(8*0.025)
    # zero is not allowed at an operating point, and the other bounds stay
    for bad in ("p,0 m^3/s,20 m,0.6;p,0.05 m^3/s,10 m,0;p,0.1 m^3/s,0 m,0.8", "p,0 m^3/s,20 m,0.6;p,0.1 m^3/s,0 m,0.005", "p,0 m^3/s,20 m,-0.1;p,0.1 m^3/s,0 m,0.8"):
        r = call(pump_system, pump_points=bad, static_head="5 m", system_coefficient="1000 s^2/m^5")
        assert not r.ok and r.error.details["param"].startswith("/pump_points/")
