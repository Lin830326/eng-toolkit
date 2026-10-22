"""Textbook arithmetic, unit conversions and conservation against literal equations."""

import math

import pytest

from eng_toolkit.domains.fluids.compressible import compressible
from eng_toolkit.domains.fluids.external_flow import external_flow
from eng_toolkit.domains.fluids.open_channel import open_channel
from eng_toolkit.domains.fluids.pipe_network import pipe_network
from eng_toolkit.domains.fluids.pump_system import pump_system
from eng_toolkit.domains.fluids.surge import surge
from tests.fluids_testing import CHANNEL, EXTERNAL, G, NETWORK, NOZZLE, PUMP, SURGE, close, colebrook_bisection, ok, table, v


def test_mach_two_isentropic_and_normal_shock():
    r = ok(compressible, mach=2)
    # gamma=1.4: t=1+.2*4=1.8, p0/p=1.8^3.5, area=(1.8/1.2)^3/2.
    close(v(r, "stagnation_temperature_ratio"), 1.8)
    close(v(r, "stagnation_pressure_ratio"), 1.8**3.5)
    close(v(r, "area_ratio"), 1.6875)
    s = ok(compressible, mode="normal_shock", mach=2)
    # M2=sqrt(1.8/5.4)=1/sqrt(3); p2/p1=1+(2.8/2.4)*3=4.5.
    close(v(s, "downstream_mach"), 1/math.sqrt(3))
    close(v(s, "pressure_ratio"), 4.5)
    close(v(s, "density_ratio"), 8/3)
    close(v(s, "temperature_ratio"), 27/16)
    close(v(s, "stagnation_pressure_ratio"), (8/3)**3.5*(2.4/10.8)**2.5)


@pytest.mark.parametrize("branch,original", [("subsonic", .25), ("supersonic", 2.)])
def test_area_and_pressure_inverses(branch, original):
    # Direct textbook area equation supplies the independent inverse target.
    ratio = ((1+.2*original**2)/1.2)**3/original
    r = ok(compressible, area_ratio_input=ratio, branch=branch)
    close(v(r, "mach"), original)
    r = ok(compressible, pressure_ratio_input=(1+.2*original**2)**3.5)
    close(v(r, "mach"), original)


def test_fanno_rayleigh_mach_two_and_sonic_limits():
    r = ok(compressible, mode="fanno", mach=2)
    # (1-4)/(1.4*4)+(2.4/2.8)*ln(2.4*4/(2+.4*4)).
    close(v(r, "fanno_parameter"), -3/5.6+2.4/2.8*math.log(8/3))
    close(v(r, "temperature_star_ratio"), 2/3)
    close(v(r, "pressure_star_ratio"), math.sqrt(2/3)/2)
    r = ok(compressible, mode="rayleigh", mach=2)
    # p/p*=2.4/(1+1.4*4)=4/11; T/T*=4*(4/11)^2=64/121.
    close(v(r, "pressure_star_ratio"), 4/11)
    close(v(r, "temperature_star_ratio"), 64/121)
    close(v(r, "stagnation_temperature_star_ratio"), 96/121)
    for mode in ("fanno", "rayleigh"):
        sonic = ok(compressible, mode=mode, mach=1)
        close(v(sonic, "temperature_star_ratio"), 1)
        close(v(sonic, "pressure_star_ratio"), 1)
    near = ok(compressible, mode="fanno", mach=1+1e-8)
    # Taylor leading term 4*(M-1)^2/[gamma*(gamma+1)].
    close(v(near, "fanno_parameter"), 4e-16/(1.4*2.4), rel=1e-6, abs=1e-25)


def test_nozzle_choking_and_unchoked_energy():
    r = ok(compressible, **NOZZLE)
    # 1e-4*200000*sqrt(1.4/(287.05*300))*(2/2.4)^3 = 0.04667 kg/s.
    expected = 20*math.sqrt(1.4/(287.05*300))*(5/6)**3
    close(v(r, "mass_flow"), expected)
    assert r.data["choked"]
    close(v(r, "critical_pressure_ratio"), (5/6)**3.5)
    u = ok(compressible, **{**NOZZLE, "back_pressure": "1.8 bar"})
    # Isentropic T/T0=.9^(2/7); v=sqrt(2*cp*(T0-T)); rho=p/(R*T).
    temperature = 300*.9**(2/7)
    velocity = math.sqrt(2*(1.4*287.05/.4)*(300-temperature))
    close(v(u, "mass_flow"), 1e-4*180000/(287.05*temperature)*velocity)
    assert not u.data["choked"]
    stopped = ok(compressible, **{**NOZZLE, "back_pressure": "2 bar"})
    close(v(stopped, "mass_flow"), 0)


@pytest.mark.parametrize("temperature", ["26.85 degC", "26.85 celsius", "26.85 degree_Celsius", "80.33 degF", "80.33 degree_Fahrenheit"])
def test_absolute_temperature_aliases(temperature):
    r = ok(compressible, **{**NOZZLE, "stagnation_temperature": temperature})
    close(v(r, "mass_flow"), 20*math.sqrt(1.4/(287.05*300))*(5/6)**3)


def test_oblique_beta_theta_and_expansion_energy():
    weak = ok(compressible, mode="oblique_shock", mach=2, deflection="10 deg", branch="weak")
    strong = ok(compressible, mode="oblique_shock", mach=2, deflection="10 deg", branch="strong")
    for r in (weak, strong):
        beta = v(r, "shock_angle")
        # theta-beta-M independently reconstructed from tan(theta).
        close(2/math.tan(beta)*(4*math.sin(beta)**2-1)/(4*(1.4+math.cos(2*beta))+2), math.tan(math.pi/18))
    assert v(weak, "shock_angle") < v(strong, "shock_angle")
    # Published Mach-2 maximum turn ~22.974 degrees; rounded-table tolerance .001 degree.
    close(v(weak, "maximum_deflection", "deg"), 22.974, abs=.001)
    r = ok(compressible, mode="expansion", mach=2, deflection="10 deg")
    m2 = v(r, "downstream_mach")
    close(v(r, "temperature_ratio")*(1+.2*m2*m2), 1.8)
    # nu=sqrt(6)*atan(sqrt((M^2-1)/6))-atan(sqrt(M^2-1)).
    def nu(m):
        return math.sqrt(6)*math.atan(math.sqrt((m*m-1)/6))-math.atan(math.sqrt(m*m-1))
    close(nu(m2)-nu(2), math.pi/18)


def test_single_pipe_darcy_colebrook_and_laminar():
    r = ok(pipe_network, **NETWORK)
    pipe = r.data["pipes"][0]
    # Q=.001, D=.05: v=Q/(pi D^2/4); Re=1000*v*.05/.001; h=f*(10/.05)*v^2/(2g).
    velocity = .001/(math.pi*.05**2/4)
    re = 1000*velocity*.05/.001
    f = colebrook_bisection(re, 0)
    close(pipe["flow_m3_s"], .001)
    close(pipe["darcy_factor"], f)
    close(pipe["headloss_m"], f*200*velocity**2/(2*G), rel=2e-8)
    laminar = ok(pipe_network, **{**NETWORK, "dynamic_viscosity": "1 Pa*s"})
    # Hagen-Poiseuille head =128*mu*L*Q/(rho*g*pi*D^4).
    close(laminar.data["pipes"][0]["headloss_m"], 128*1*10*.001/(1000*G*math.pi*.05**4), rel=1e-8)


def test_network_symmetry_loop_closure_and_head_datum():
    nodes = "r,0 m,head,20 m;a,0 m,demand,1 L/s;b,0 m,demand,1 L/s;c,0 m,demand,2 L/s"
    pipes = "ra,r,a,10 m,50 mm,0 mm,0;rb,r,b,10 m,50 mm,0 mm,0;ac,a,c,10 m,50 mm,0 mm,0;bc,b,c,10 m,50 mm,0 mm,0;ab,a,b,10 m,50 mm,0 mm,0"
    r = ok(pipe_network, nodes=nodes, pipes=pipes)
    p = {p["id"]: p for p in r.data["pipes"]}
    # Two identical paths each carry 2 L/s, feed a 1 L/s demand, then carry 1 L/s to c; bridge flow zero.
    for name in ("ra", "rb"):
        close(p[name]["flow_m3_s"], .002, rel=1e-8)
    for name in ("ac", "bc"):
        close(p[name]["flow_m3_s"], .001, rel=1e-8)
    close(p["ab"]["flow_m3_s"], 0, abs=1e-10)
    close(p["ra"]["headloss_m"]+p["ac"]["headloss_m"]-p["bc"]["headloss_m"]-p["rb"]["headloss_m"], 0)
    shifted = ok(pipe_network, nodes=nodes.replace("head,20 m", "head,120 m"), pipes=pipes)
    for old, new in zip(r.data["pipes"], shifted.data["pipes"]):
        close(old["flow_m3_s"], new["flow_m3_s"], rel=1e-7, abs=1e-10)
    for old, new in zip(r.data["nodes"], shifted.data["nodes"]):
        close(new["head_m"]-old["head_m"], 100)


def test_hazen_williams_and_matching_darcy_loss():
    # EPANET Table 3.1: kSI=4.727*(.3048)^(4.871-3*1.852), h=kSI*L*Q^1.852/(C^1.852*D^4.871).
    coefficient = 4.727*.3048**(4.871-3*1.852)
    loss = coefficient*100*.01**1.852/(130**1.852*.1**4.871)
    r = ok(pipe_network, nodes="r,0 m,head,20 m;j,0 m,demand,10 L/s", pipes="p,r,j,100 m,0.1 m,130,0", headloss_model="hazen_williams")
    close(r.data["pipes"][0]["headloss_m"], loss, rel=1e-8)
    # Invert Colebrook for the roughness giving this same factor, a consistency check rather than assuming HW=DW.
    velocity = .01/(math.pi*.1**2/4)
    f = loss*2*G*.1/(100*velocity**2)
    re = 1000*velocity*.1/.001
    roughness = .1*3.7*(10**(-1/(2*math.sqrt(f)))-2.51/(re*math.sqrt(f)))
    dw = ok(pipe_network, nodes="r,0 m,head,20 m;j,0 m,demand,10 L/s", pipes=f"p,r,j,100 m,0.1 m,{roughness} m,0")
    close(dw.data["pipes"][0]["headloss_m"], loss, rel=1e-8)


def test_network_pump_polynomial_and_closed_check_valve():
    r = ok(pipe_network, nodes="r,0 m,head,0 m;j,0 m,demand,1 L/s", pumps="p,r,j,poly,20 m,1000 s/m^2,100000 s^2/m^5,yes")
    # H=.02e3-1000*.001-100000*.001^2=18.9 m.
    close(r.data["nodes"][1]["head_m"], 18.9)
    close(r.data["pumps"][0]["flow_m3_s"], .001)
    closed = ok(pipe_network, nodes="a,0 m,head,0 m;b,0 m,head,30 m", pumps="p,a,b,poly,20 m,1000 s/m^2,0 s^2/m^5,yes")
    assert closed.data["pumps"][0]["check_valve_closed"]
    close(closed.data["pumps"][0]["flow_m3_s"], 0)


def test_published_hardy_cross_two_loop_example():
    # Swamee & Sharma, Design of Water Supply Pipe Networks (2008), example 3.4, pp.56-60.
    # Published five-iteration flows are .223,.223,.192,.192,.182 m^3/s (rounded; sum .597, nominal demand .6).
    # Three paths have resistances K1+K2, K3+K4, K5; common h=[.6/sum(1/sqrt(Kpath))]^2.
    resistance = [6528.54, 33050.74, 49576.12, 4352.36, 59491.34]
    paths = [resistance[0]+resistance[1], resistance[2]+resistance[3], resistance[4]]
    head = (.6/sum(1/math.sqrt(k) for k in paths))**2
    branch = [math.sqrt(head/k) for k in paths]
    expected = [branch[0], branch[0], branch[1], branch[1], branch[2]]
    # Reconstruct Colebrook-compatible pipes with the book's f=.02 at the exact solution.
    # D=.1m; L=K*pi^2*g*D^5/(8f); invert Colebrook: eps/D=3.7*(10^(-1/(2sqrt(f)))-2.51/(Re sqrt(f))).
    # This preserves the published resistances, without adding a fixed-friction override to the tool.
    edges = [("a", "b"), ("b", "c"), ("d", "c"), ("a", "d"), ("a", "c")]
    rows = []
    for i, (k, flow, (start, end)) in enumerate(zip(resistance, expected, edges)):
        length = k*math.pi**2*G*.1**5/(8*.02)
        re = 4*flow/(math.pi*.1*1e-6)
        rough = .1*3.7*(10**(-1/(2*math.sqrt(.02)))-2.51/(re*math.sqrt(.02)))
        rows.append(f"p{i},{start},{end},{length} m,0.1 m,{rough} m,0")
    r = ok(pipe_network, nodes="a,0 m,head,3000 m;b,0 m,demand,0 m^3/s;c,0 m,demand,0.6 m^3/s;d,0 m,demand,0 m^3/s", pipes=";".join(rows))
    for p, exact, published in zip(r.data["pipes"], expected, [.223, .223, .192, .192, .182]):
        close(p["flow_m3_s"], exact, rel=1e-8)
        close(p["flow_m3_s"], published, rel=0, abs=.002)
    close(r.data["nodes"][0]["reservoir_supply_m3_s"], .6, rel=1e-8)


def test_pump_system_operating_point_power_efficiency_and_npsh():
    r = ok(pump_system, **PUMP, vapour_pressure="2325 Pa", suction_elevation="2 m", suction_loss="0.5 m", npsh_required="3 m", rotational_speed="60 rpm")
    # H_p=20-200Q, H_s=5+1000Q^2; positive root=(-200+sqrt(100000))/2000.
    flow = (-200+math.sqrt(100000))/2000
    head = 20-200*flow
    efficiency = .6+2*flow
    close(v(r, "operating_flow"), flow)
    close(v(r, "operating_head"), head)
    close(v(r, "hydraulic_power"), 1000*G*flow*head)
    close(v(r, "shaft_power"), 1000*G*flow*head/efficiency)
    close(v(r, "efficiency"), efficiency)
    # (101325-2325)/(1000g)+2-.5; margin subtracts 3 m.
    close(v(r, "npsh_available"), 99/G+1.5)
    close(v(r, "npsh_margin"), 99/G-1.5)
    close(v(r, "specific_speed"), 2*math.pi*math.sqrt(flow)/(G*head)**.75)


@pytest.mark.parametrize("combination,head_intercept,head_slope", [("series", 40, 400), ("parallel", 20, 100)])
def test_identical_pump_combinations(combination, head_intercept, head_slope):
    points = PUMP["pump_points"]+";"+PUMP["pump_points"].replace("p,", "q,")
    r = ok(pump_system, **{**PUMP, "pump_points": points}, combination=combination)
    # Combined linear head H0-sQ meets 5+1000Q^2; stable quadratic root.
    flow = 2*(head_intercept-5)/(head_slope+math.sqrt(head_slope**2+4000*(head_intercept-5)))
    close(v(r, "operating_flow"), flow)
    close(v(r, "hydraulic_power"), v(r, "shaft_power")*v(r, "efficiency"))


def test_pump_affinity_scales_operating_point():
    base = ok(pump_system, **{**PUMP, "static_head": "0 m"})
    scaled = ok(pump_system, **{**PUMP, "static_head": "0 m"}, speed_ratio=1.2, diameter_ratio=.95)
    # With Hstatic=0 and the same K, Q scales s*d, H scales (s*d)^2, P scales (s*d)^3.
    factor = 1.2*.95
    close(v(scaled, "operating_flow")/v(base, "operating_flow"), factor)
    close(v(scaled, "operating_head")/v(base, "operating_head"), factor**2)
    close(v(scaled, "shaft_power")/v(base, "shaft_power"), factor**3)


def test_channel_normal_critical_alternate_and_jump():
    r = ok(open_channel, **CHANNEL)
    y = v(r, "normal_depth")
    # Rectangular b=2: A=2y, P=2+2y, Q=(1/.015)*A*(A/P)^(2/3)*sqrt(.001)=2.
    close((2*y)*((2*y)/(2+2*y))**(2/3)*math.sqrt(.001)/.015, 2)
    # q=Q/b=1; yc=(1/g)^(1/3).
    close(v(r, "critical_depth"), (1/G)**(1/3))
    alternate = v(r, "alternate_depth")
    close(alternate+1/(2*G*alternate**2), v(r, "specific_energy"))
    jump = ok(open_channel, **CHANNEL, depth="0.2 m")
    fr2 = 1/(G*.2**3)
    y2 = .2/2*(math.sqrt(1+8*fr2)-1)
    close(v(jump, "sequent_depth"), y2)
    close(v(jump, "jump_energy_loss"), (y2-.2)**3/(4*y2*.2))


@pytest.mark.parametrize("shape,width,side,expected_area,expected_perimeter,expected_top", [("rectangular", "2 m", 0, 2, 4, 2), ("trapezoidal", "2 m", 1, 3, 2+2*math.sqrt(2), 4), ("triangular", "", 1, 1, 2*math.sqrt(2), 2), ("circular", "", 0, math.pi/2, math.pi, 2)])
def test_channel_section_geometry(shape, width, side, expected_area, expected_perimeter, expected_top):
    # At y=1, rectangle/trapezoid/triangle use A=y(b+zy), P=b+2y sqrt(1+z^2); D=2 circle is half full.
    r = ok(open_channel, shape=shape, width=width, side_slope=side, diameter="2 m" if shape == "circular" else "", depth="1 m", mode="uniform")
    close(v(r, "area"), expected_area)
    close(v(r, "wetted_perimeter"), expected_perimeter)
    close(v(r, "top_width"), expected_top)


def test_channel_circular_dual_normal_depth_and_profile_csv():
    # Select Q at y=.98D; low and high roots have identical Manning conveyance.
    upper = ok(open_channel, shape="circular", diameter="1 m", mode="uniform", depth="0.98 m", normal_branch="upper")
    close(v(upper, "normal_depth"), .98, rel=1e-7)
    low = ok(open_channel, shape="circular", diameter="1 m", discharge=f"{v(upper, 'discharge')} m^3/s")
    assert v(low, "normal_depth") < v(upper, "maximum_discharge_depth") < .98
    assert any("0.82" in w for w in upper.warnings)
    # At normal depth Sf=S0; standard-step energy difference balances bed drop exactly.
    base = ok(open_channel, **CHANNEL)
    y = v(base, "normal_depth")
    profile = ok(open_channel, **CHANNEL, mode="profile", boundary_depth=f"{y} m", profile_length="100 m", profile_steps=10)
    for row in table(profile):
        close(float(row["depth_m"]), y, rel=1e-8)
    assert len(table(profile)) == 11


def test_joukowsky_michaud_and_instantaneous_moc():
    r = ok(surge, **SURGE, duration="4 s", reservoir_head="200 m", reaches=20)
    # c1=1-.3^2=.91; a=sqrt(2.2e9/1000/(1+2.2e9*.5/(2e11*.01)*.91)).
    a = math.sqrt(2.2e6/(1+.55*.91))
    close(v(r, "wave_speed"), a)
    close(v(r, "joukowsky_pressure_rise"), 1000*a)
    close(v(r, "critical_closure_time"), 2000/a)
    close(v(r, "oscillation_period"), 4000/a)
    close(v(r, "moc_peak_pressure_rise"), 1000*a)
    close(r.data["instantaneous_comparison"]["ratio"], 1)
    rows = table(r)
    # The reservoir boundary stays 200 m throughout, and the first valve sample rises a*v/g.
    close(float(rows[1]["head_m_at_1000m"]), 200+a/G)
    for row in rows:
        close(float(row["head_m_at_0m"]), 200)
    slow = ok(surge, **{**SURGE, "closure_time": "10 s"})
    close(v(slow, "slow_closure_pressure_rise"), 2*1000*1000*1/10)


def test_terminal_sphere_stokes_limit_and_rising():
    r = ok(external_flow, mode="terminal_velocity", length="1 um", solid_density="2000 kg/m^3")
    # vt=g*d^2*(2000-1000)/(18*.001)=5.44814e-7 m/s; correlation correction is <1e-5 here.
    expected = G*1e-12*1000/.018
    close(v(r, "terminal_velocity"), expected, rel=1e-5)
    close(v(r, "stokes_terminal_velocity"), expected)
    rising = ok(external_flow, mode="terminal_velocity", length="1 um", solid_density="500 kg/m^3")
    close(v(rising, "terminal_velocity"), -expected/2, rel=1e-5)
    assert rising.data["direction"] == "rising"


def test_external_sphere_cylinder_and_plate_correlations():
    r = ok(external_flow, **EXTERNAL)
    # Re=1e4; Cd=24/1e4*(1+.15*1e4^.687)+.42/(1+42500/1e4^1.16).
    cd = 24/1e4*(1+.15*1e4**.687)+.42/(1+42500/1e4**1.16)
    close(v(r, "drag_force"), .5*1000*cd*math.pi*.01**2/4)
    cylinder = ok(external_flow, shape="cylinder", length="10 mm", velocity="1 m/s", area="0.1 m^2")
    close(v(cylinder, "drag_coefficient"), 1+10/1e4**(2/3))
    plate = ok(external_flow, shape="flat_plate", length="1 m", velocity="0.1 m/s", area="2 m^2")
    # Re=1e5; Blasius Cf=1.328/sqrt(Re), delta=5L/sqrt(Re).
    close(v(plate, "drag_coefficient"), 1.328/math.sqrt(1e5))
    close(v(plate, "boundary_layer_thickness"), 5/math.sqrt(1e5))
    mixed = ok(external_flow, shape="flat_plate", length="1 m", velocity="1 m/s", area="2 m^2")
    close(v(mixed, "drag_coefficient"), .074/1e6**.2-1742/1e6)


def test_imperial_inches_gpm_psi_ft_cst_and_bar():
    # Unit identities: 1 in=.0254 m; 1 US gal=0.003785411784 m^3; 1 ft=.3048 m; 1 cSt=1e-6 m^2/s.
    r = ok(pipe_network, nodes="r,0 ft,head,100 ft;j,0 ft,demand,10 gpm", pipes="p,r,j,10 ft,2 inch,0 inch,0", dynamic_viscosity="", kinematic_viscosity="1 cSt")
    close(r.data["pipes"][0]["flow_m3_s"], 10*.003785411784/60, rel=1e-8)
    velocity = (10*.003785411784/60)/(math.pi*(2*.0254)**2/4)
    close(r.data["pipes"][0]["reynolds_number"], velocity*(2*.0254)/1e-6, rel=1e-8)
    psi = ok(compressible, **{**NOZZLE, "stagnation_pressure": "29.007547546 psi"})
    close(v(psi, "mass_flow"), 20*math.sqrt(1.4/(287.05*300))*(5/6)**3, rel=1e-9)
    imperial = ok(open_channel, mode="uniform", width="10 ft", depth="2 ft", bed_slope=.001, manning_n=.02)
    # Exact conversion gives coefficient (1/.3048)^(1/3)=1.4859186, commonly rounded 1.486.
    expected_cfs = (1/.3048)**(1/3)/.02*20*(20/14)**(2/3)*math.sqrt(.001)
    close(v(imperial, "discharge", "ft^3/s"), expected_cfs)
