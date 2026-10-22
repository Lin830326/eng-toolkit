"""Independent numerical routes; SciPy is optional and used only in tests."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fluids.compressible import compressible
from eng_toolkit.domains.fluids.external_flow import external_flow
from eng_toolkit.domains.fluids.open_channel import open_channel
from eng_toolkit.domains.fluids.pipe_network import pipe_network
from eng_toolkit.domains.fluids.pump_system import pump_system
from eng_toolkit.domains.fluids.surge import surge
from tests.fluids_testing import CHANNEL, G, PUMP, SURGE, close, colebrook_bisection, ok, table, v


@pytest.mark.parametrize("seed", range(8))
def test_random_colebrook_against_independent_factor_bisection(seed):
    rng = np.random.default_rng(seed)
    d, l, velocity, relative = rng.uniform(.05, .4), rng.uniform(10, 100), rng.uniform(.5, 3), rng.uniform(0, .01)
    flow = velocity*math.pi*d*d/4
    r = ok(pipe_network, nodes=f"r,0 m,head,100 m;j,0 m,demand,{flow} m^3/s", pipes=f"p,r,j,{l} m,{d} m,{relative*d} m,2")
    # DW loss evaluated with an independent factor-space bisection, not the tool's Haaland/Newton route.
    f = colebrook_bisection(velocity*d/1e-6, relative)
    close(r.data["pipes"][0]["darcy_factor"], f, rel=1e-8)
    close(r.data["pipes"][0]["headloss_m"], (f*l/d+2)*velocity**2/(2*G), rel=1e-8)


@pytest.mark.parametrize("seed", range(5))
def test_random_network_against_loop_flow_fsolve(seed):
    optimize = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(seed)
    lengths = rng.uniform(20, 100, 5)
    diameter, rough = .1, .00005
    ends = [("a", "b"), ("b", "c"), ("a", "d"), ("d", "c"), ("b", "d")]
    text = ";".join(f"p{k},{i},{j},{l} m,0.1 m,0.05 mm,0" for k, ((i, j), l) in enumerate(zip(ends, lengths)))
    nodes = "a,0 m,head,100 m;b,0 m,demand,2 L/s;c,0 m,demand,3 L/s;d,0 m,demand,1 L/s"

    def flow_vector(x):
        ab, bd = x
        # Continuity eliminates three flows: BC=AB-BD-.002, AD=.006-AB, DC=.005-AB+BD.
        return np.array([ab, ab-bd-.002, .006-ab, .005-ab+bd, bd])

    def loss(q, length):
        speed = q/(math.pi*diameter**2/4)
        re = abs(speed)*diameter/1e-6
        if re == 0:
            return 0.
        if re < 2000:
            f = 64/re
        else:
            f = colebrook_bisection(re, rough/diameter)
            if re < 4000:
                t = (re-2000)/2000
                w = t*t*(3-2*t)
                f = (1-w)*64/re+w*f
        return f*length/diameter*speed*abs(speed)/(2*G)

    def loops(x):
        h = [loss(q, l) for q, l in zip(flow_vector(x), lengths)]
        return [h[0]+h[4]-h[2], h[1]-h[3]-h[4]]

    solution, info, ier, message = optimize.fsolve(loops, [.0035, .0004], full_output=True, xtol=1e-11)
    assert ier == 1, message
    assert max(abs(x) for x in loops(solution)) < 1e-9
    r = ok(pipe_network, nodes=nodes, pipes=text)
    for actual, expected in zip(r.data["pipes"], flow_vector(solution)):
        close(actual["flow_m3_s"], expected, rel=1e-7, abs=1e-11)


def test_hazen_network_against_plain_numpy_loop_newton():
    # h_i=K_i*Q_i*|Q_i|^.852; loop variables preserve continuity exactly at each iteration.
    lengths = np.array([50., 70., 90., 100., 120.])
    # Exact foot/cfs conversion of EPANET's tabulated 4.727 coefficient and 4.871 diameter exponent.
    resistance = (4.727*.3048**(4.871-3*1.852))*lengths/(130**1.852*.1**4.871)
    transform = np.array([[1., 0.], [1., -1.], [-1., 0.], [-1., 1.], [0., 1.]])
    offset = np.array([0., -.02, .06, .05, 0.])
    cycles = np.array([[1., 0., -1., 0., 1.], [0., 1., 0., -1., -1.]])
    x = np.array([.035, .002])
    for _ in range(40):
        flows = transform@x+offset
        losses = resistance*flows*abs(flows)**.852
        residual = cycles@losses
        if max(abs(residual)) < 1e-12:
            break
        derivatives = 1.852*resistance*abs(flows)**.852
        x -= np.linalg.solve((cycles*derivatives)@transform, residual)
    else:
        pytest.fail("independent loop Newton did not converge")
    pipes = ";".join(f"p{k},{i},{j},{l} m,0.1 m,130,0" for k, ((i, j), l) in enumerate(zip([("a", "b"), ("b", "c"), ("a", "d"), ("d", "c"), ("b", "d")], lengths)))
    r = ok(pipe_network, nodes="a,0 m,head,100 m;b,0 m,demand,20 L/s;c,0 m,demand,30 L/s;d,0 m,demand,10 L/s", pipes=pipes, headloss_model="hazen_williams")
    for actual, expected in zip(r.data["pipes"], flows):
        close(actual["flow_m3_s"], expected, rel=1e-7)


def test_darcy_pump_operating_point_against_brentq():
    optimize = pytest.importorskip("scipy.optimize")
    def residual(flow):
        velocity = flow/(math.pi*.1**2/4)
        f = colebrook_bisection(velocity*.1/1e-6, .0005)
        # Supplied pump H=20-200Q; static 5 plus DW of a 20m, 100mm pipe and K=2.
        return 20-200*flow-5-(f*200+2)*velocity**2/(2*G)
    expected = optimize.brentq(residual, .001, .08, xtol=1e-14)
    r = ok(pump_system, pump_points=PUMP["pump_points"], static_head="5 m", pipe_length="20 m", pipe_diameter="0.1 m", roughness="0.05 mm", minor_loss_coefficient=2)
    close(v(r, "operating_flow"), expected, rel=1e-9)


@pytest.mark.parametrize("seed", range(5))
def test_random_trapezoid_depths_against_brentq(seed):
    optimize = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(seed)
    b, z, flow, slope = rng.uniform(1, 4), rng.uniform(.5, 2), rng.uniform(1, 4), rng.uniform(.001, .005)
    def area(y):
        return y*(b+z*y)
    # A=y(b+zy), P=b+2y*sqrt(1+z^2), T=b+2zy; independent positive-depth roots.
    normal = optimize.brentq(lambda y: area(y)*(area(y)/(b+2*y*math.sqrt(1+z*z)))**(2/3)*math.sqrt(slope)/.015-flow, .001, 10)
    critical = optimize.brentq(lambda y: flow**2*(b+2*z*y)/(G*area(y)**3)-1, .001, 10)
    r = ok(open_channel, shape="trapezoidal", width=f"{b} m", side_slope=z, discharge=f"{flow} m^3/s", bed_slope=slope, depth=f"{critical/2} m")
    close(v(r, "normal_depth"), normal)
    close(v(r, "critical_depth"), critical)
    momentum = lambda y: flow**2/(G*area(y))+b*y*y/2+z*y**3/3
    sequent = optimize.brentq(lambda y: momentum(y)-momentum(critical/2), critical, 20)
    close(v(r, "sequent_depth"), sequent)


@pytest.mark.parametrize("boundary,expected", [("1.2 m", "M1"), ("0.6 m", "M2"), ("0.2 m", "M3")])
def test_profile_csv_satisfies_standard_step_energy(boundary, expected):
    r = ok(open_channel, **CHANNEL, mode="profile", boundary_depth=boundary, profile_length="5 m", profile_steps=20)
    assert r.data["profile_classification"] == expected
    rows = table(r)
    for one, two in zip(rows, rows[1:]):
        y1, y2 = float(one["depth_m"]), float(two["depth_m"])
        dx = float(two["x_m"])-float(one["x_m"])
        # Q=2,b=2,n=.015; E=y+1/(2g y^2), Sf=(.015*2/(2y*(y/(1+y))^(2/3)))^2.
        energy = lambda y: y+1/(2*G*y*y)
        sf = lambda y: (.03/(2*y*(y/(1+y))**(2/3)))**2
        close(energy(y2)-energy(y1), .001*dx-.5*(sf(y1)+sf(y2))*dx, abs=1e-9)


@pytest.mark.parametrize("seed", range(6))
def test_random_mach_inverse_and_terminal_velocity_brentq(seed):
    optimize = pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(seed)
    gamma, mach = rng.uniform(1.2, 1.6), rng.uniform(1.1, 8)
    exponent = (gamma+1)/(2*(gamma-1))
    ratio = (2/(gamma+1)*(1+(gamma-1)*mach**2/2))**exponent/mach
    # Brent solves the same conservation-law area equation on the independent supersonic bracket.
    expected = optimize.brentq(lambda m: (2/(gamma+1)*(1+(gamma-1)*m*m/2))**exponent/m-ratio, 1, 50)
    r = ok(compressible, gamma=gamma, area_ratio_input=ratio, branch="supersonic")
    close(v(r, "mach"), expected)
    diameter, solid = rng.uniform(1e-5, 1e-3), rng.uniform(1500, 5000)
    def residual(speed):
        re = 1000*speed*diameter/.001
        cd = 24/re*(1+.15*re**.687)+.42/(1+42500/re**1.16)
        # Drag = buoyant weight: .5*rho*v^2*Cd*pi*d^2/4 = (rho_s-rho)*g*pi*d^3/6.
        return .5*1000*speed*speed*cd*math.pi*diameter**2/4-(solid-1000)*G*math.pi*diameter**3/6
    terminal = optimize.brentq(residual, 1e-10, 20, xtol=1e-14)
    sphere = ok(external_flow, mode="terminal_velocity", length=f"{diameter} m", solid_density=f"{solid} kg/m^3")
    close(v(sphere, "terminal_velocity"), terminal, rel=1e-8)


def test_moc_instantaneous_grid_invariance_and_slow_closure():
    fine = ok(surge, **SURGE, duration="5 s", reservoir_head="200 m", reaches=100)
    coarse = ok(surge, **SURGE, duration="5 s", reservoir_head="200 m", reaches=20)
    # Courant-one instantaneous closure transports the exact Joukowsky characteristic at any grid resolution.
    close(v(fine, "moc_peak_pressure_rise"), v(coarse, "moc_peak_pressure_rise"))
    slow = ok(surge, **{**SURGE, "closure_time": "4 s"}, duration="5 s", reservoir_head="200 m", reaches=100)
    assert 0 < v(slow, "moc_peak_pressure_rise") < v(fine, "moc_peak_pressure_rise")
    close(float(table(slow)[-1]["head_m_at_0m"]), 200)
