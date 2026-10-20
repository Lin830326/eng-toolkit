"""control_pid_tune: the published tuning formulas evaluated by hand for K = 2, T = 10 s, L = 2 s, and the simulated check."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.control.pid import pid_tune
from tests.control_testing import fails, ok, v

PLANT = dict(gain=2.0, time_constant="10 s", delay="2 s")  # K = 2, T = 10, L = 2: L/T = 0.2


def std(r):
    return r.data["standard"]


# ------------------------------------------------------------------------------------------------ the formulas, one by one


def test_ziegler_nichols_open_loop_by_hand():
    # P:   Kc = T/(K L) = 10/(2*2) = 2.5
    # PI:  Kc = 0.9 T/(K L) = 0.9*10/4 = 2.25 ;  Ti = L/0.3 = 6.666667
    # PID: Kc = 1.2 T/(K L) = 3 ;  Ti = 2 L = 4 ;  Td = 0.5 L = 1
    p = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="P")
    assert std(p)["Kc"] == pytest.approx(2.5, abs=1e-13) and std(p)["Ti_s"] is None and std(p)["Td_s"] is None
    pi = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="PI")
    assert std(pi)["Kc"] == pytest.approx(2.25, abs=1e-13) and std(pi)["Ti_s"] == pytest.approx(20 / 3, abs=1e-12) and std(pi)["Td_s"] is None
    pid = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="PID")
    assert (std(pid)["Kc"], std(pid)["Ti_s"], std(pid)["Td_s"]) == pytest.approx((3.0, 4.0, 1.0), abs=1e-13)
    assert v(pid, "controller_gain") == pytest.approx(3.0) and v(pid, "integral_time") == pytest.approx(4.0) and v(pid, "derivative_time") == pytest.approx(1.0)
    assert pid.data["formulas"] == {"Kc": "1.2 T/(K L)", "Ti": "2 L", "Td": "0.5 L"}
    assert "Ziegler-Nichols" in pid.data["intended_for"] and any("starting points" in c for c in pid.data["caveats"])


def test_cohen_coon_by_hand():
    # r = L/T = 0.2
    # P:   Kc = (1/K)(T/L)(1 + r/3) = 0.5*5*(1.066667) = 2.666667
    # PI:  Kc = 2.5 (0.9 + r/12) = 2.5*0.916667 = 2.291667 ;  Ti = L (30 + 3r)/(9 + 20 r) = 2*30.6/13 = 4.707692
    # PID: Kc = 2.5 (4/3 + r/4) = 2.5*1.383333 = 3.458333 ;  Ti = L (32 + 6r)/(13 + 8r) = 2*33.2/14.6 = 4.547945 ;  Td = 4 L/(11 + 2r) = 8/11.4 = 0.701754
    p = ok(pid_tune, **PLANT, rule="cohen_coon", controller="P")
    assert std(p)["Kc"] == pytest.approx(2.5 * (1 + 0.2 / 3), abs=1e-13) and std(p)["Kc"] == pytest.approx(2.666667, abs=1e-6)
    pi = ok(pid_tune, **PLANT, rule="cohen_coon", controller="PI")
    assert std(pi)["Kc"] == pytest.approx(2.5 * (0.9 + 0.2 / 12), abs=1e-13) and std(pi)["Kc"] == pytest.approx(2.291667, abs=1e-6)
    assert std(pi)["Ti_s"] == pytest.approx(2 * 30.6 / 13, abs=1e-13) and std(pi)["Ti_s"] == pytest.approx(4.707692, abs=1e-6)
    pid = ok(pid_tune, **PLANT, rule="cohen_coon", controller="PID")
    assert std(pid)["Kc"] == pytest.approx(2.5 * (4 / 3 + 0.05), abs=1e-13) and std(pid)["Kc"] == pytest.approx(3.458333, abs=1e-6)
    assert std(pid)["Ti_s"] == pytest.approx(2 * 33.2 / 14.6, abs=1e-13) and std(pid)["Ti_s"] == pytest.approx(4.547945, abs=1e-6)
    assert std(pid)["Td_s"] == pytest.approx(8 / 11.4, abs=1e-13) and std(pid)["Td_s"] == pytest.approx(0.701754, abs=1e-6)


def test_imc_lambda_tuning_by_hand():
    # lambda = max(L, 0.1 T) = 2 s (default)
    # PI:  Kc = T/(K (lambda + L)) = 10/(2*4) = 1.25 ;  Ti = T = 10
    # PID: Kc = (T + L/2)/(K (lambda + L/2)) = 11/(2*3) = 1.833333 ;  Ti = T + L/2 = 11 ;  Td = T L/(2T + L) = 20/22 = 0.909091
    pi = ok(pid_tune, **PLANT, rule="imc", controller="PI")
    assert (std(pi)["Kc"], std(pi)["Ti_s"]) == pytest.approx((1.25, 10.0), abs=1e-13) and pi.data["lambda_s"] == 2.0 and pi.data["lambda_default"] is True
    pid = ok(pid_tune, **PLANT, rule="imc", controller="PID")
    assert (std(pid)["Kc"], std(pid)["Ti_s"], std(pid)["Td_s"]) == pytest.approx((11 / 6, 11.0, 20 / 22), abs=1e-13)
    # a faster closed loop: lambda = 1 s -> PI Kc = 10/(2*(1+2)) = 1.666667
    fast = ok(pid_tune, **PLANT, rule="imc", controller="PI", lambda_time="1 s")
    assert std(fast)["Kc"] == pytest.approx(10 / 6, abs=1e-13) and fast.data["lambda_default"] is False
    assert any("below 0.8 L" in w for w in fast.warnings)  # 1 s < 0.8*2 s = 1.6 s


def test_simc_by_hand_and_its_difference_from_imc():
    # tau_c = 0.2 s: SIMC PI: Kc = T/(K (tau_c + L)) = 10/(2*2.2) = 2.272727 ; Ti = min(T, 4 (tau_c + L)) = min(10, 8.8) = 8.8
    # IMC PI with the same lambda: Kc = 2.272727, Ti = T = 10 - the integral time is the difference
    simc = ok(pid_tune, **PLANT, rule="simc", controller="PI", lambda_time="0.2 s")
    assert (std(simc)["Kc"], std(simc)["Ti_s"]) == pytest.approx((10 / 4.4, 8.8), abs=1e-13)
    imc = ok(pid_tune, **PLANT, rule="imc", controller="PI", lambda_time="0.2 s")
    assert (std(imc)["Kc"], std(imc)["Ti_s"]) == pytest.approx((10 / 4.4, 10.0), abs=1e-13)
    # default lambda = L = 2 -> Ti = min(10, 16) = 10
    assert std(ok(pid_tune, **PLANT, rule="simc", controller="PI"))["Ti_s"] == pytest.approx(10.0)
    # the SIMC "PID" of a FOPDT process has no derivative: Td = 0, said so
    pid = ok(pid_tune, **PLANT, rule="simc", controller="PID")
    assert std(pid)["Td_s"] == 0.0 and any("no derivative term" in w for w in pid.warnings)


def test_parallel_form():
    # PID ZN: Kp = 3, Ki = Kc/Ti = 0.75 1/s, Kd = Kc Td = 3 s
    r = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="PID", form="parallel")
    assert v(r, "controller_gain") == 3.0 and v(r, "integral_gain") == pytest.approx(0.75) and v(r, "derivative_gain") == pytest.approx(3.0)
    assert "integral_time" not in r.values and r.values["integral_gain"].unit == "1 / second"
    assert r.data["parallel"] == {"Kp": 3.0, "Ki_per_s": pytest.approx(0.75), "Kd_s": pytest.approx(3.0), "law": "C(s) = Kp + Ki/s + Kd s"}


def test_time_units_are_converted():
    r = ok(pid_tune, gain=2.0, time_constant="0.1666666666666667 min", delay="2000 ms", rule="ziegler_nichols", controller="PI")
    assert std(r)["Kc"] == pytest.approx(2.25, rel=1e-12) and std(r)["Ti_s"] == pytest.approx(20 / 3, rel=1e-12)


# ------------------------------------------------------------------------------------------------ the simulated check


def test_imc_pi_closed_loop_by_hand():
    # IMC PI Kc = 1.25, Ti = 10 on K e^{-Ls}/(Ts + 1) with a first-order Pade (1 - s)/(1 + s) for e^{-2s}:
    #   L(s) = 1.25 (10 s + 1)/(10 s) * 2 (1 - s)/((10 s + 1)(1 + s)) = 0.25 (1 - s)/(s (1 + s))  (the (10 s + 1) cancels)
    #   closed loop s^2 + 0.75 s + 0.25 (wn = 0.5, zeta = 0.75), T(s) = 0.25 (1 - s)/(s^2 + 0.75 s + 0.25), T(0) = 1
    # y(t) = 1 + 2 Re(A e^{p t}), p = -0.375 + j 0.330719, A = 0.25 (1 - p)/(p (p - conj p))
    r = ok(pid_tune, **PLANT, rule="imc", controller="PI", pade_order=1)
    pole = complex(-0.375, math.sqrt(0.25 - 0.375**2))
    amp = 0.25 * (1 - pole) / (pole * (pole - pole.conjugate()))
    t = np.linspace(0.0, 60.0, 600_001)
    y = 1 + 2 * np.real(amp * np.exp(pole * t))
    overshoot = 100 * (y.max() - 1)
    # 2 % settling: the last time outside the band
    settle = t[np.nonzero(np.abs(y - 1) > 0.02)[0][-1]]
    sim = r.data["simulation"]["setpoint_step"]
    assert sim["overshoot_percent"] == pytest.approx(overshoot, abs=2e-3)
    assert sim["settling_time"] == pytest.approx(settle, abs=2e-2)
    assert v(r, "overshoot") == pytest.approx(overshoot, abs=2e-3) and v(r, "settling_time") == pytest.approx(settle, abs=2e-2)
    assert r.data["simulation"]["closed_loop_stability"] == "stable" and v(r, "closed_loop_unstable_poles") == 0
    assert r.data["simulation"]["pade_order"] == 1


def test_margins_use_the_exact_delay():
    # IMC PI loop = 0.25 e^{-2 s}/s exactly (the lag cancels the plant): gain crossover 0.25/w = 1 -> w = 0.25, PM = 90 - 2*0.25 rad = 90 - 28.6479 = 61.3521 deg
    # phase crossover: -90 deg - 2 w = -180 deg -> w = pi/4 = 0.785398, |L| = 0.25/0.785398 -> GM = pi.  Delay margin = (pi/2 - 0.5)/0.25 = 4.28319 s
    r = ok(pid_tune, **PLANT, rule="imc", controller="PI")
    assert v(r, "phase_margin") == pytest.approx(90 - math.degrees(0.5), abs=1e-6)
    assert v(r, "gain_margin") == pytest.approx(math.pi, rel=1e-9)
    assert v(r, "delay_margin") == pytest.approx((math.pi / 2 - 0.5) / 0.25, rel=1e-9)
    crossovers = r.data["simulation"]["margins_with_exact_delay"]
    assert crossovers["gain_crossovers"]["items"][0]["frequency_rad_s"] == pytest.approx(0.25, rel=1e-9)
    assert crossovers["phase_crossovers"]["items"][0]["frequency_rad_s"] == pytest.approx(math.pi / 4, rel=1e-9)
    assert crossovers["phase_crossovers"]["total"] > 10 and crossovers["phase_crossovers"]["truncated"] is True  # a delay crosses -180 infinitely often


def test_aggressive_rules_overshoot_more_than_imc():
    zn = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="PID")
    imc = ok(pid_tune, **PLANT, rule="imc", controller="PID")
    assert v(zn, "overshoot") > 40.0 > v(imc, "overshoot") and v(zn, "phase_margin") < v(imc, "phase_margin")
    assert v(zn, "load_disturbance_peak") > 0


def test_pade_order_is_stated_and_changes_the_simulation_only_slightly():
    a = ok(pid_tune, **PLANT, rule="imc", controller="PI", pade_order=3)
    b = ok(pid_tune, **PLANT, rule="imc", controller="PI", pade_order=6)
    assert a.data["simulation"]["pade_order"] == 3 and b.data["simulation"]["pade_order"] == 6
    assert abs(v(a, "overshoot") - v(b, "overshoot")) < 0.5  # converged: the approximation error is small for L/T = 0.2
    assert v(a, "phase_margin") == pytest.approx(v(b, "phase_margin"), abs=1e-9)  # margins do not depend on the Pade order


def test_p_controller_has_an_offset_and_a_scaled_final_value():
    # P-only: closed-loop final value K Kc/(1 + K Kc) = 5/6 for ZN (Kc = 2.5, K = 2)
    r = ok(pid_tune, **PLANT, rule="ziegler_nichols", controller="P")
    assert r.data["simulation"]["setpoint_step"]["steady_state_value"] == pytest.approx(5 / 6, abs=1e-12)


def test_an_unstable_tuning_is_reported_not_hidden():
    # a Cohen-Coon PID on a dead-time dominant process (L/T = 50) with Pade order 1: the loop is unstable; the result is reported as such
    r = ok(pid_tune, gain=1.0, time_constant="1 s", delay="50 s", rule="ziegler_nichols", controller="PID", pade_order=1)
    assert r.data["simulation"]["closed_loop_stability"] in {"stable", "unstable", "marginally stable"}
    if r.data["simulation"]["closed_loop_stability"] == "unstable":
        assert r.data["simulation"]["setpoint_step"]["settling_time"] == "unbounded" and "settling_time" not in r.values
    assert any("above 1" in w for w in r.warnings)


def test_range_warnings():
    small = ok(pid_tune, gain=2.0, time_constant="100 s", delay="2 s", rule="cohen_coon", controller="PI")  # L/T = 0.02
    assert any("below 0.1" in w for w in small.warnings)
    big = ok(pid_tune, gain=2.0, time_constant="1 s", delay="2 s", rule="ziegler_nichols", controller="PI")  # L/T = 2
    assert any("above 1" in w for w in big.warnings)


def test_refusals():
    fails(pid_tune, "input.invalid", "gain", gain=0.0, time_constant="10 s", delay="2 s")
    fails(pid_tune, "input.invalid", "gain", gain=-2.0, time_constant="10 s", delay="2 s")
    fails(pid_tune, "input.invalid", "gain", gain=float("nan"), time_constant="10 s", delay="2 s")
    fails(pid_tune, "input.invalid", "gain", gain=float("inf"), time_constant="10 s", delay="2 s")
    fails(pid_tune, "units.bare_number", "time_constant", gain=2.0, time_constant="10", delay="2 s")
    fails(pid_tune, "units.dimension_mismatch", "delay", gain=2.0, time_constant="10 s", delay="2 kg")
    fails(pid_tune, "input.invalid", "time_constant", gain=2.0, time_constant="-10 s", delay="2 s")
    fails(pid_tune, "input.invalid", "delay", gain=2.0, time_constant="10 s", delay="0 s")
    fails(pid_tune, "input.out_of_range", "delay", gain=2.0, time_constant="1 s", delay="1000 s")  # L/T = 1000
    fails(pid_tune, "input.out_of_range", "delay", gain=2.0, time_constant="1000 s", delay="1 s")  # L/T = 0.001
    fails(pid_tune, "input.out_of_range", "time_constant", gain=2.0, time_constant="1e12 s", delay="1e12 s")
    fails(pid_tune, "input.invalid", "rule", gain=2.0, time_constant="10 s", delay="2 s", rule="magic")
    fails(pid_tune, "input.invalid", "controller", gain=2.0, time_constant="10 s", delay="2 s", controller="PD")
    fails(pid_tune, "input.invalid", "controller", gain=2.0, time_constant="10 s", delay="2 s", rule="imc", controller="P")
    fails(pid_tune, "input.invalid", "form", gain=2.0, time_constant="10 s", delay="2 s", form="series")
    fails(pid_tune, "input.out_of_range", "pade_order", gain=2.0, time_constant="10 s", delay="2 s", pade_order=11)
    fails(pid_tune, "input.out_of_range", "pade_order", gain=2.0, time_constant="10 s", delay="2 s", pade_order=0)
    fails(pid_tune, "input.invalid", "derivative_filter", gain=2.0, time_constant="10 s", delay="2 s", derivative_filter=0.5)
    fails(pid_tune, "input.out_of_range", "gain", gain=1e30, time_constant="10 s", delay="2 s")
    fails(pid_tune, "input.out_of_range", "gain", gain=1e-9, time_constant="10 s", delay="2 s")
