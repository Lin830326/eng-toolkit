"""Independent circuit, operating-root and boundary checks for induction motors."""

import math
import random

import numpy as np
import pytest

from eng_toolkit.domains.electric.induction_motor import induction_motor
from tests.electric_testing import call, close, ok, table, v


BASE = dict(line_voltage="460 V", frequency="60 Hz", poles=4, r1="0.641 ohm",
            x1="1.106 ohm", xm="26.3 ohm", r2="0.332 ohm", x2="0.464 ohm")
NAMEPLATE = dict(mode="nameplate", line_voltage="460 V", line_current="20 A",
                 frequency="60 Hz", poles=4, power_factor=0.85,
                 output_power="10 hp", speed="1740 rpm", mechanical_loss="300 W", stray_loss="50 W")


def motor(**changes):
    return ok(induction_motor, **(BASE | changes))


def nodal(voltage, r1, x1, xm, r2, x2, slip, rc=None):
    """Solve KVL, internal-node KCL and rotor KVL as three simultaneous equations."""
    ym = 1 / complex(0, xm) + (0 if rc is None else 1 / rc)
    zrotor = complex(r2 / slip, x2)
    matrix = np.array([[complex(r1, x1), 1, 0], [1, -ym, -1], [0, 1, -zrotor]])
    return np.linalg.solve(matrix, np.array([voltage, 0, 0]))


def test_four_pole_hand_calculation():
    result = motor(slip=0.025)
    # Vph=460/sqrt(3), Zm=j26.3. Direct complex divider and parallel impedance.
    # Vth=Vph*j26.3/(.641+j27.406); Zth=(.641+j1.106)*j26.3/(.641+j27.406).
    # ns=120*60/4=1800; speed=.975*1800=1755; omega_s=60*pi.
    # h=hypot(.5899846400,1.0751652979+.464)=1.648366329; sm=.332/h.
    # Tmax=3*254.793615895²/[2*60*pi*(.5899846400+h)].
    # Te(s)=3*254.793615895²*(.332/s)/[60*pi*((.5899846400+.332/s)²+1.5391652979²)].
    # I1=Vph/[.641+j1.106 + 1/(1/(j26.3)+1/(.332/s+j.464))]; pf=Re(I1)/abs(I1).
    # Pshaft=Te(.025)*(.975*60*pi), with no core or fixed losses. Default tolerance 1e-9 relative.
    close(v(result, "synchronous_speed", "rpm"), 1800)
    close(v(result, "speed", "rpm"), 1755)
    close(v(result, "thevenin_voltage"), 254.79361589485663)
    close(v(result, "thevenin_resistance"), 0.5899846400075224)
    close(v(result, "thevenin_reactance"), 1.075165297899906)
    close(v(result, "breakdown_slip"), 0.20141153450165258)
    close(v(result, "breakdown_torque"), 230.80171322024339)
    close(v(result, "electromagnetic_torque"), 70.4576009273836)
    close(v(result, "starting_torque"), 106.56210454695848)
    close(v(result, "starting_line_current"), 144.5276599196106)
    close(v(result, "line_current"), 20.74171043869494)
    close(v(result, "power_factor"), 0.8537098251437755)
    close(v(result, "shaft_power"), 12948.921265587236)


def test_core_and_fixed_losses_balance():
    result = motor(slip=0.04, rc="240 ohm", mechanical_loss="300 W", stray_loss="120 W")
    current, node, rotor = nodal(460 / math.sqrt(3), .641, 1.106, 26.3, .332, .464, .04, 240)
    pin = 3 * (460 / math.sqrt(3)) * current.real
    losses = [3 * abs(current) ** 2 * .641, 3 * abs(node) ** 2 / 240,
              3 * abs(rotor) ** 2 * .332, 300, 120]
    close(v(result, "input_power"), pin)
    for key, value in zip(("stator_copper_loss", "core_loss", "rotor_copper_loss", "mechanical_loss", "stray_loss"), losses):
        close(v(result, key), value)
    close(v(result, "shaft_power"), pin - sum(losses))
    close(v(result, "efficiency"), (pin - sum(losses)) / pin)
    close(v(result, "shaft_torque") * v(result, "speed"), pin - sum(losses))
    close(v(result, "power_balance_residual"), 0, abs=1e-8)
    zm = 1 / complex(1 / 240, -1 / 26.3)
    zth = complex(.641, 1.106) * zm / (complex(.641, 1.106) + zm)
    close(v(result, "thevenin_resistance"), zth.real)
    close(v(result, "thevenin_reactance"), zth.imag)


def test_randomized_against_independent_complex_nodal_solver():
    rng = random.Random(29374)
    for _ in range(16):
        r1, x1, r2, x2 = [rng.uniform(.1, 1.7) for _ in range(4)]
        xm, rc, voltage = rng.uniform(12, 45), rng.uniform(100, 500), rng.uniform(120, 690)
        s, f = rng.uniform(.01, .6), rng.uniform(30, 100)
        delta = rng.choice([True, False])
        phase_voltage = voltage if delta else voltage / math.sqrt(3)
        i1, node, i2 = nodal(phase_voltage, r1, x1, xm, r2, x2, s, rc)
        result = motor(line_voltage=f"{voltage} V", frequency=f"{f} Hz", connection="delta" if delta else "wye",
                       r1=f"{r1} ohm", x1=f"{x1} ohm", xm=f"{xm} ohm", rc=f"{rc} ohm",
                       r2=f"{r2} ohm", x2=f"{x2} ohm", slip=s, curve_points=3)
        gap = 3 * abs(i2) ** 2 * r2 / s
        pin = 3 * phase_voltage * i1.real
        close(v(result, "line_current"), abs(i1) * (math.sqrt(3) if delta else 1))
        close(v(result, "rotor_current"), abs(i2))
        close(v(result, "input_power"), pin)
        close(v(result, "core_loss"), 3 * abs(node) ** 2 / rc)
        close(v(result, "electromagnetic_torque"), gap / (math.pi * f))
        close(v(result, "power_factor"), i1.real / abs(i1))
        close(v(result, "efficiency"), gap * (1 - s) / pin)


def test_stable_shaft_load_root_includes_losses():
    result = motor(load_torque="70 N*m", mechanical_loss="250 W", stray_loss="40 W", rc="300 ohm")
    s = v(result, "slip")
    _, _, i2 = nodal(460 / math.sqrt(3), .641, 1.106, 26.3, .332, .464, s, 300)
    shaft = (3 * abs(i2) ** 2 * .332 / s * (1 - s) - 290) / (60 * math.pi * (1 - s))
    close(shaft, 70, rel=2e-10)
    assert s < v(result, "maximum_shaft_torque_slip") < v(result, "breakdown_slip")
    assert result.data["operating_stable"]


def test_stable_root_matches_optional_scipy_brentq():
    scipy_optimize = pytest.importorskip("scipy.optimize")
    result = motor(load_torque="90 N*m", mechanical_loss="400 W", rc="300 ohm")

    def residual(s):
        _, _, rotor = nodal(460 / math.sqrt(3), .641, 1.106, 26.3, .332, .464, s, 300)
        torque = 3 * abs(rotor) ** 2 * .332 / s / (60 * math.pi)
        return torque - 400 / (60 * math.pi * (1 - s)) - 90

    expected = scipy_optimize.brentq(residual, 1e-8, .15, xtol=1e-14)
    close(v(result, "slip"), expected, rel=1e-9)


def test_above_breakdown_and_above_shaft_maximum_refused():
    result = motor(mechanical_loss="500 W")
    for torque in (v(result, "breakdown_torque") * 1.01, v(result, "maximum_shaft_torque") * 1.001):
        refused = call(induction_motor, **BASE, mechanical_loss="500 W", load_torque=f"{torque} N*m")
        assert not refused.ok
        assert refused.error.family == "electric.no_solution"
        assert refused.error.details["param"] == "/load_torque"


def test_peak_shaft_root_and_high_resistance_motoring_limit():
    baseline = motor(mechanical_loss="500 W")
    peak = v(baseline, "maximum_shaft_torque")
    result = motor(mechanical_loss="500 W", load_torque=f"{peak} N*m")
    close(v(result, "slip"), v(result, "maximum_shaft_torque_slip"))
    high = motor(rotor_resistance_multiplier=20, load_torque="30 N*m")
    assert v(high, "breakdown_slip") > 1
    assert high.data["breakdown_in_motoring_range"] is False
    close(v(high, "motoring_peak_slip"), 1)


def test_rotor_resistance_changes_slip_but_not_breakdown_torque():
    normal = motor()
    doubled = motor(rotor_resistance_multiplier=2)
    close(v(doubled, "breakdown_slip"), 2 * v(normal, "breakdown_slip"))
    close(v(doubled, "breakdown_torque"), v(normal, "breakdown_torque"))
    close(v(doubled, "effective_r2"), .664)


@pytest.mark.parametrize("frequency,voltage,torque_factor,power_factor,region", [
    (30, 230, 1, .5, "constant_torque"), (120, 460, .5, 1, "field_weakening"),
])
def test_vf_scaling_matches_direct_scaled_circuit(frequency, voltage, torque_factor, power_factor, region):
    result = motor(frequency=f"{frequency} Hz", base_frequency="60 Hz")
    ratio = frequency / 60
    direct = motor(frequency=f"{frequency} Hz", line_voltage=f"{voltage} V",
                   x1=f"{1.106 * ratio} ohm", xm=f"{26.3 * ratio} ohm", x2=f"{.464 * ratio} ohm")
    for name in ("electromagnetic_torque", "line_current", "breakdown_torque", "shaft_power"):
        close(v(result, name), v(direct, name))
    close(v(result, "ideal_vf_torque_factor"), torque_factor)
    close(v(result, "ideal_vf_power_multiplier"), power_factor)
    assert result.data["vf"]["region"] == region


def test_speed_units_delta_connection_and_kloss_curve():
    result = motor(speed="1755 rpm", curve_points=201)
    radians = motor(speed=f"{1755 * math.pi / 30} rad/s")
    close(v(result, "slip"), .025)
    close(v(result, "shaft_torque"), v(radians, "shaft_torque"))
    delta = motor(speed="1755 rpm", connection="delta")
    close(v(delta, "line_current"), 3 * v(result, "line_current"))
    close(v(delta, "electromagnetic_torque"), 3 * v(result, "electromagnetic_torque"))
    curve = table(result)
    assert len(curve) == 201
    assert curve[0]["efficiency"] == curve[-1]["efficiency"] == ""
    assert curve[0]["kloss_relative_error"] == ""
    assert result.data["starting"]["efficiency"] is None
    close(float(curve[0]["speed_rpm"]), 1800)
    close(float(curve[-1]["line_current_A"]), v(result, "starting_line_current"))
    for row in curve[1:]:
        s, exact = float(row["slip"]), float(row["electromagnetic_torque_Nm"])
        approx = 2 * v(result, "breakdown_torque") / (s / v(result, "breakdown_slip") + v(result, "breakdown_slip") / s)
        close(float(row["kloss_torque_Nm"]), approx)
        close(float(row["kloss_relative_error"]), (approx - exact) / exact)


def test_nameplate_hp_one_point_match_and_nonunique_fit():
    result = ok(induction_motor, **NAMEPLATE)
    fit = result.data["nameplate_fit"]
    # International mechanical horsepower is 550 ft*lbf/s = 745.6998715822702 W.
    rated_watts = 7456.998715822702
    pin = math.sqrt(3) * 460 * 20 * .85
    pag = (rated_watts + 350) / (1740 / 1800)
    close(v(result, "base_r1"), (pin - pag) / (3 * 20 ** 2))
    close(v(result, "shaft_power"), rated_watts)
    close(v(result, "line_current"), 20)
    close(v(result, "power_factor"), .85)
    close(v(result, "speed", "rpm"), 1740)
    close(v(result, "base_x1"), v(result, "base_x2"))
    close(v(result, "base_xm"), 460 / math.sqrt(3) / (.3 * 20))
    assert fit["nonunique"] is True
    assert any("nonunique" in warning for warning in result.warnings)
    close(fit["output_power_residual_W"], 0, abs=1e-6)
    other = ok(induction_motor, **(NAMEPLATE | {"magnetizing_current_fraction": .15}))
    close(v(other, "shaft_power"), rated_watts)
    assert v(other, "base_xm") != v(result, "base_xm")
    assert v(other, "starting_torque") != pytest.approx(v(result, "starting_torque"))
    assert len(table(result)) == 101


def test_nameplate_delta_fit_and_frequency_scaling():
    base = ok(induction_motor, **(NAMEPLATE | {"connection": "delta"}))
    close(v(base, "line_current"), 20)
    close(v(base, "power_factor"), .85)
    altered = ok(induction_motor, **(NAMEPLATE | {"base_frequency": "60 Hz", "frequency": "30 Hz"}))
    close(v(altered, "applied_line_voltage"), 230)
    close(v(altered, "speed", "rpm"), 870)


@pytest.mark.parametrize("changes,pointer", [
    ({"line_voltage": "1e308 V"}, "/line_voltage"),
    ({"line_voltage": "nan V"}, "/line_voltage"),
    ({"line_voltage": "460 A"}, "/line_voltage"),
    ({"line_voltage": "460 V; __import__('os')"}, "/line_voltage"),
    ({"line_voltage": "1 " + "V" * 200}, "/line_voltage"),
    ({"r1": "0 ohm"}, "/r1"), ({"r1": "-1 ohm"}, "/r1"),
    ({"xm": "1e-100 ohm"}, "/xm"), ({"frequency": "60 rad/s"}, "/frequency"),
    ({"frequency": "1e308 Hz"}, "/frequency"), ({"base_frequency": "0 Hz"}, "/base_frequency"),
    ({"speed": "1800 rpm"}, "/speed"), ({"speed": "1900 rpm"}, "/speed"),
    ({"speed": "1740 Hz"}, "/speed"), ({"speed": "0 rpm"}, "/speed"),
    ({"speed": "1750 rpm", "slip": .05}, "/speed"),
    ({"load_torque": "50 N*m", "slip": .05}, "/load_torque"),
    ({"load_torque": "0 N*m"}, "/load_torque"),
    ({"slip": 1}, "/slip"), ({"slip": -1}, "/slip"),
    ({"slip": float("nan")}, "/slip"), ({"slip": float("inf")}, "/slip"),
    ({"slip": 1e308}, "/slip"), ({"slip": True}, "/slip"),
    ({"mechanical_loss": "100 MW"}, "/slip"), ({"stray_loss": "1 VA"}, "/stray_loss"),
    ({"poles": 3}, "/poles"), ({"poles": 4.5}, "/poles"),
    ({"curve_points": 202}, "/curve_points"), ({"curve_points": 1e308}, "/curve_points"),
    ({"connection": "mesh"}, "/connection"), ({"rotor_resistance_multiplier": 0}, "/rotor_resistance_multiplier"),
    ({"power_factor": .8}, "/mode"),
])
def test_invalid_equivalent_inputs_are_structured(changes, pointer):
    result = call(induction_motor, **(BASE | changes))
    assert not result.ok
    assert result.error.details["param"] == pointer


@pytest.mark.parametrize("changes,pointer", [
    ({"output_power": "100 hp"}, "/output_power"),
    ({"output_power": "10 kVA"}, "/output_power"),
    ({"output_power": ""}, "/output_power"),
    ({"line_current": ""}, "/line_current"), ({"speed": ""}, "/speed"),
    ({"power_factor": 0}, "/power_factor"), ({"power_factor": 1}, "/power_factor"),
    ({"magnetizing_current_fraction": .9, "output_power": "14 hp"}, "/magnetizing_current_fraction"),
    ({"r1": ".1 ohm"}, "/mode"), ({"rc": "100 ohm"}, "/mode"),
])
def test_invalid_nameplate_inputs_are_structured(changes, pointer):
    result = call(induction_motor, **(NAMEPLATE | changes))
    assert not result.ok
    assert result.error.details["param"] == pointer
