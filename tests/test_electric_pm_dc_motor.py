"""Independent SI motor arithmetic and abc-waveform checks."""

import math
import time

import numpy as np
import pytest

from eng_toolkit.domains.electric.pm_dc_motor import pm_dc_motor
from tests.electric_testing import call, close, ok, table, v


DC = dict(voltage="24 V", resistance="2 ohm", torque_constant="0.1 N*m/A")
PMSM = dict(mode="pmsm", voltage="100 V", resistance="0.3 ohm", d_inductance="2 mH",
            q_inductance="3 mH", flux_linkage="0.08 Wb", pole_pairs=3,
            speed="50 rad/s", current_limit="10 A", curve_points=5)


def test_dc_half_stall_power_and_curve():
    r = ok(pm_dc_motor, **DC, torque="0.6 N*m", curve_points=3)
    # I=0.6/0.1=6 A, E=24-2*6=12 V, w=120; Pshaft=72 W.
    for name, expected in {"current": 6, "speed": 120, "shaft_power": 72, "input_power": 144,
                           "copper_loss": 72, "efficiency": .5, "stall_current": 12,
                           "stall_torque": 1.2, "no_load_speed": 240,
                           "maximum_power_torque": .6, "maximum_power_speed": 120}.items():
        close(v(r, name), expected)
    rows = table(r)
    assert len(rows) == 3
    assert rows[0]["efficiency"] == ""  # Zero input power has no defined efficiency.
    close(float(rows[1]["shaft_power_W"]), 72)


def test_dc_fixed_loss_efficiency_and_thermal():
    r = ok(pm_dc_motor, **DC, torque="0.2 N*m", mechanical_loss="8 W",
           thermal_resistance="3 K/W", allowed_temperature_rise="30 K")
    # Iopt=sqrt(8/2)=2 A; E=20 V; converted=40 W; shaft=32 W; efficiency=2/3.
    close(v(r, "maximum_efficiency_current"), 2)
    close(v(r, "maximum_efficiency"), 2/3)
    close(v(r, "efficiency"), 2/3)
    close(v(r, "shaft_torque"), .16)
    close(v(r, "temperature_rise"), 24)
    assert r.data["thermal_check"].startswith("pass")


def test_dc_speed_conversion_and_current_limited_stall():
    r = ok(pm_dc_motor, **DC, speed=f"{120*60/(2*math.pi)} rpm", current_limit="7 A")
    close(v(r, "current"), 6)
    close(v(r, "current_limited_stall_torque"), .7)


def test_fixed_loss_actual_no_load_operating_point():
    # The high-speed root of 0.1*w*(24-.1*w)/2=8 is 120+sqrt(12800).
    omega = 120+math.sqrt(12800)
    r = ok(pm_dc_motor, **DC, mechanical_loss="8 W", speed=f"{omega} rad/s")
    close(v(r, "no_load_speed"), omega)
    close(v(r, "ideal_no_load_speed"), 240)
    close(v(r, "shaft_power"), 0)
    close(v(r, "maximum_efficiency_electromagnetic_torque"), .2)
    close(v(r, "maximum_efficiency_shaft_torque"), .16)


def test_bldc_flat_phase_and_terminal_conventions():
    r = ok(pm_dc_motor, **DC, mode="bldc_trapezoidal", torque="0.6 N*m")
    # Two conducting phases: P=2 Ephase I, so ke_phase=.05 and kt=.10.
    close(v(r, "phase_flat_back_emf_constant"), .05)
    close(v(r, "line_line_flat_back_emf_constant"), .1)
    close(v(r, "phase_rms_current"), 6*math.sqrt(2/3))
    close(v(r, "speed_constant"), 60/(2*math.pi*.1))


def test_pmsm_dq_and_independent_abc_time_domain():
    r = ok(pm_dc_motor, **PMSM, d_current="-2 A", q_current="6 A")
    # vd=.3*(-2)-150*.003*6=-3.3; vq=.3*6+150*(.002*(-2)+.08)=13.2.
    close(v(r, "d_voltage"), -3.3)
    close(v(r, "q_voltage"), 13.2)
    close(v(r, "electromagnetic_torque"), 2.214)
    close(v(r, "copper_loss"), 18)
    close(v(r, "input_power"), 128.7)
    close(v(r, "shaft_power"), 110.7)
    # Independently differentiate physical abc flux waveforms over one revolution.
    theta = np.linspace(0, 2*math.pi, 257, endpoint=False)
    shifts = np.array([0.0, -2*math.pi/3, 2*math.pi/3])[:, None]
    phases = theta[None, :]+shifts
    currents = -2*np.cos(phases)-6*np.sin(phases)
    dt = 1e-7
    def flux(t):
        return .076*np.cos(phases+150*t)-.018*np.sin(phases+150*t)
    voltage = .3*currents+(flux(dt)-flux(-dt))/(2*dt)
    measured_d = (2/3)*np.sum(voltage*np.cos(phases), axis=0)
    measured_q = -(2/3)*np.sum(voltage*np.sin(phases), axis=0)
    assert np.allclose(measured_d, -3.3, rtol=1e-8, atol=1e-8)
    assert np.allclose(measured_q, 13.2, rtol=1e-8, atol=1e-8)
    # Sum of three instantaneous phase powers is constant, 128.7 W.
    close(float(np.mean(np.sum(voltage*currents, axis=0))), 128.7, rel=1e-8)


def test_pmsm_reactive_cancellation_retains_small_real_power():
    r = ok(pm_dc_motor, mode="pmsm", voltage="10000 V", resistance="1e-12 ohm",
           d_inductance="1000 H", q_inductance="1000 H", flux_linkage="1e-12 Wb",
           pole_pairs=1, speed="1 rad/s", current_limit="2 A", d_current="-1 A", q_current="1 A", curve_points=2)
    # Surface PM Pconverted=1.5*p*psi*iq*omega=1.5e-12 W.
    # Copper=1.5*1e-12*(1²+1²)=3e-12 W, hence Pin=4.5e-12 W and eta=1/3.
    # Absolute tolerance must be below these small powers to detect cancellation.
    close(v(r, "input_power"), 4.5e-12, abs=1e-25)
    close(v(r, "copper_loss"), 3e-12, abs=1e-25)
    close(v(r, "shaft_power"), 1.5e-12, abs=1e-25)
    close(v(r, "efficiency"), 1/3)


def test_mtpa_closed_form_and_base_speed():
    r = ok(pm_dc_motor, **{**PMSM, "d_inductance": "1 mH", "q_inductance": "2 mH", "flux_linkage": "0.1 Wb"})
    # d=(psi-sqrt(psi^2+8*(Lq-Ld)^2*I^2))/(4*(Lq-Ld)); I=10 A.
    expected_d = (.1-math.sqrt(.01+.0008))/.004
    expected_q = math.sqrt(100-expected_d**2)
    close(v(r, "mtpa_d_current_at_limit"), expected_d)
    close(v(r, "mtpa_q_current_at_limit"), expected_q)
    close(v(r, "mtpa_current_angle"), math.atan2(expected_q, expected_d))
    base = v(r, "base_speed_at_current_limit")
    vd = .3*expected_d-3*base*.002*expected_q
    vq = .3*expected_q+3*base*(.001*expected_d+.1)
    close(math.hypot(vd, vq), 100/math.sqrt(3))


def test_surface_pm_field_weakening_circle_intersection():
    r = ok(pm_dc_motor, mode="pmsm", voltage=f"{60*math.sqrt(3)} V", resistance="1e-8 ohm",
           d_inductance="1 mH", q_inductance="1 mH", flux_linkage="0.1 Wb",
           pole_pairs=2, speed="600 rad/s", current_limit="100 A", speed_points="0 rad/s; 600 rad/s; 2000 rad/s")
    # At we=1200: voltage circle (id+100)^2+iq^2=50^2, current circle id^2+iq^2=100^2.
    # Subtraction gives id=-87.5, iq=sqrt(2343.75). R=1e-8 perturbs by <1e-6.
    close(v(r, "d_current"), -87.5, rel=1e-7)
    close(v(r, "q_current"), math.sqrt(2343.75), rel=1e-7)
    close(v(r, "electromagnetic_torque"), .3*math.sqrt(2343.75), rel=1e-7)
    for row in table(r):
        if row["within_stated_limits"] == "True":
            assert float(row["phase_peak_voltage_V"]) <= 60*(1+1e-8)
            assert float(row["phase_peak_current_A"]) <= 100*(1+1e-8)


def test_requested_torque_in_field_weakening():
    r = ok(pm_dc_motor, **{**PMSM, "speed": "500 rad/s", "voltage": "100 V", "current_limit": "50 A"}, torque="4 N*m")
    close(v(r, "electromagnetic_torque"), 4, rel=1e-6)
    assert v(r, "phase_peak_voltage") <= 100/math.sqrt(3)*(1+1e-8)


def test_zero_torque_low_speed_has_zero_current_and_undefined_efficiency():
    r = ok(pm_dc_motor, **PMSM, torque="0 N*m")
    close(v(r, "phase_peak_current"), 0)
    close(v(r, "input_power"), 0)
    assert r.data["efficiency"] is None


def test_salient_envelope_against_independent_constrained_optimizer():
    optimize = pytest.importorskip("scipy.optimize")
    params = {**PMSM, "speed": "300 rad/s", "current_limit": "25 A", "voltage": "100 V"}
    r = ok(pm_dc_motor, **params)
    # Independent two-variable SLSQP uses literal phase equations and separate bounds.
    def objective(x):
        return -4.5*(.08-.001*x[0])*x[1]
    def voltage_margin(x):
        return 10000/3-(.3*x[0]-900*.003*x[1])**2-(.3*x[1]+900*(.002*x[0]+.08))**2
    candidates = []
    for initial in ([-20, 5], [-10, 5], [0, 5]):
        answer = optimize.minimize(objective, initial, method="SLSQP", bounds=[(-25, 25), (0, 25)],
                                   constraints=[{"type": "ineq", "fun": lambda x: 625-x[0]**2-x[1]**2},
                                                {"type": "ineq", "fun": voltage_margin}],
                                   options={"ftol": 1e-10, "maxiter": 300})
        if answer.success:
            candidates.append(-answer.fun)
    assert candidates
    close(v(r, "envelope_torque"), max(candidates), rel=1e-6)


@pytest.mark.parametrize("change", [
    {"voltage": "1e308 V"}, {"voltage": "1e-999 V"}, {"resistance": "0 ohm"},
    {"torque_constant": "0 N*m/A"}, {"speed": "1 Hz"}, {"voltage": "24"},
    {"torque": "0.5 N*m", "speed": "1 rad/s"}, {"back_emf_constant": "0.2 V*s/rad"},
    {"current_limit": "1 A", "torque": "0.6 N*m"}, {"voltage_limit": "12 V"},
    {"thermal_resistance": "1 K/W", "allowed_temperature_rise": "25 degC"},
    {"thermal_resistance": "1 K/W", "allowed_temperature_rise": "25 degree_Celsius"},
    {"mechanical_loss": "100 W"}, {"mechanical_loss": "1 W", "speed": "0 rad/s"},
    {"d_current": "0 A"}, {"curve_points": 101},
])
def test_dc_refusals(change):
    assert not call(pm_dc_motor, **{**DC, **change}).ok


@pytest.mark.parametrize("change", [
    {"d_current": "0 A"}, {"d_current": "0 A", "q_current": "100 A"},
    {"current_limit": ""}, {"speed": ""}, {"torque_constant": "1 N*m/A"},
    {"pole_pairs": 0}, {"d_inductance": "0 H"}, {"torque": "10000 N*m"},
    {"speed_points": ";".join(["1 rad/s"]*101)},
])
def test_pmsm_refusals(change):
    assert not call(pm_dc_motor, **{**PMSM, **change}).ok


def test_hostile_quantity_is_bounded():
    start = time.perf_counter()
    assert not call(pm_dc_motor, **{**DC, "voltage": "1"*6000+" V"}).ok
    assert time.perf_counter()-start < 1
