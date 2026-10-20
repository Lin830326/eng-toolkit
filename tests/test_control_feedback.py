"""control_feedback checked against hand calculations: error constants, sensitivity peaks, loop polynomials."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.control.feedback import feedback
from eng_toolkit.domains.control.response import response
from tests.control_testing import fails, ok, v


def coeffs(block):
    return block["coefficients_descending"]


def test_type_one_loop_ramp_error_is_one_over_kv():
    # G = 2/(s(s+1)(s+2)), unity feedback, no controller.  Type 1.  Kp = infinity, Kv = lim s L = 2/(1*2) = 1, Ka = 0.
    # ramp error 1/Kv = 1 ; step error 0 ; parabola error infinite.  Closed loop s^3 + 3 s^2 + 2 s + 2: Routh column 1, 3, (3*2 - 2)/3 = 4/3, 2 -> stable
    r = ok(feedback, plant_numerator="2", plant_denominator="s (s+1)(s+2)")
    assert v(r, "system_type") == 1 and "position_error_constant" not in r.values
    assert r.data["error_constants"] == {"Kp": "infinite", "Kv": 1.0, "Ka": 0.0, "definition": r.data["error_constants"]["definition"]}
    assert v(r, "velocity_error_constant") == pytest.approx(1.0, abs=1e-14) and v(r, "acceleration_error_constant") == 0.0
    assert v(r, "ramp_actuating_error") == pytest.approx(1.0, abs=1e-14) and v(r, "ramp_tracking_error") == pytest.approx(1.0, abs=1e-14)
    assert v(r, "step_actuating_error") == 0.0 and v(r, "step_tracking_error") == 0.0
    assert r.data["steady_state_errors"]["parabolic"]["tracking_error"] == "infinite"
    assert r.data["stability"]["verdict"] == "stable" and r.data["stability"]["method"]["first_column"] == [1.0, 3.0, pytest.approx(4 / 3), 2.0]
    assert coeffs(r.data["closed_loop"]["denominator"]) == [1.0, 3.0, 2.0, 2.0]
    assert coeffs(r.data["closed_loop"]["numerator"]) == [2.0]
    assert coeffs(r.data["sensitivity"]["numerator"]) == [1.0, 3.0, 2.0, 0.0]  # S = Ld/P = s (s+1)(s+2)/(s^3 + 3 s^2 + 2 s + 2)
    assert v(r, "closed_loop_dc_gain") == pytest.approx(1.0)


def test_type_zero_loop_step_error_is_one_over_one_plus_kp():
    # G = 5/(s+2): Kp = 5/2 = 2.5, step error 1/(1 + 2.5) = 0.285714 ; Kv = 0 -> ramp error infinite ; T = 5/(s+7), T(0) = 5/7, tracking error 1 - 5/7 = 2/7
    r = ok(feedback, plant_numerator="5", plant_denominator="[1, 2]")
    assert v(r, "system_type") == 0 and v(r, "position_error_constant") == pytest.approx(2.5)
    assert v(r, "step_actuating_error") == pytest.approx(1 / 3.5, rel=1e-14) and v(r, "step_tracking_error") == pytest.approx(2 / 7, rel=1e-14)
    assert v(r, "closed_loop_dc_gain") == pytest.approx(5 / 7, rel=1e-14)
    assert r.data["steady_state_errors"]["ramp"]["tracking_error"] == "infinite" and "ramp_tracking_error" not in r.values
    assert r.data["poles"][0]["real"] == pytest.approx(-7.0)


def test_type_two_loop_parabolic_error_is_one_over_ka():
    # plant 1/s^2 with the controller (s + 2): L = (s+2)/s^2, Ka = lim s^2 L = 2 -> parabola error 1/2 ; Kv infinite ; closed loop s^2 + s + 2 stable
    r = ok(feedback, plant_numerator="1", plant_denominator="[1, 0, 0]", controller_numerator="[1, 2]")
    assert v(r, "system_type") == 2 and v(r, "acceleration_error_constant") == pytest.approx(2.0)
    assert v(r, "parabolic_actuating_error") == pytest.approx(0.5) and v(r, "parabolic_tracking_error") == pytest.approx(0.5)
    assert r.data["error_constants"]["Kv"] == "infinite" and v(r, "ramp_tracking_error") == 0.0


def test_sensor_gain_separates_actuating_and_tracking_error():
    # G = 4/(s+1), H = 0.5: L = 2/(s+1), Kp = 2, actuating error 1/(1 + 2) = 1/3.  T = 4/(s + 3), T(0) = 4/3: y settles at 4/3 of the reference,
    # tracking error r - y = 1 - 4/3 = -1/3.
    r = ok(feedback, plant_numerator="4", plant_denominator="[1, 1]", sensor_numerator="0.5")
    assert v(r, "step_actuating_error") == pytest.approx(1 / 3, rel=1e-14) and v(r, "step_tracking_error") == pytest.approx(-1 / 3, rel=1e-14)
    assert coeffs(r.data["closed_loop"]["denominator"]) == [1.0, 3.0] and coeffs(r.data["closed_loop"]["numerator"]) == [4.0]
    assert coeffs(r.data["open_loop"]["numerator"]) == [2.0] and coeffs(r.data["open_loop"]["denominator"]) == [1.0, 1.0]


def test_dynamic_sensor_and_controller_polynomials():
    # G = 1/(s+1), C = (s+4)/s, H = 1/(0.1 s + 1):  Ln = (s+4), Ld = s (s+1)(0.1 s + 1) = 0.1 s^3 + 1.1 s^2 + s ; P = Ld + Ln
    r = ok(feedback, plant_numerator="1", plant_denominator="s + 1", controller_numerator="s + 4", controller_denominator="s", sensor_numerator="1", sensor_denominator="0.1 s + 1")
    assert coeffs(r.data["open_loop"]["denominator"]) == pytest.approx([0.1, 1.1, 1.0, 0.0])
    assert coeffs(r.data["closed_loop"]["denominator"]) == pytest.approx([0.1, 1.1, 1.0 + 1.0, 4.0])
    assert coeffs(r.data["closed_loop"]["numerator"]) == pytest.approx([0.1, 1.4, 4.0])  # Cn Gn Hd = (s+4)(0.1 s + 1)
    assert v(r, "system_type") == 1


def test_pi_around_a_first_order_plant_gives_the_predicted_second_order_loop():
    # plant K/(T s + 1) = 1/(s + 1) (K = T = 1) with PI Kc (Ti s + 1)/(Ti s), Kc = 1, Ti = 0.25 -> C = (s + 4)/s.
    # characteristic polynomial Ti T s^2 + Ti (1 + K Kc) s + K Kc, divided by Ti T: s^2 + (1 + K Kc)/T s + K Kc/(Ti T) = s^2 + 2 s + 4
    # -> wn = 2 rad/s, zeta = 2/(2*2) ... 2 zeta wn = 2 -> zeta = 0.5.  Closed loop T(s) = (s + 4)/(s^2 + 2 s + 4) (a zero at -4).
    r = ok(feedback, plant_numerator="1", plant_denominator="[1, 1]", controller_numerator="[1, 4]", controller_denominator="[1, 0]")
    assert coeffs(r.data["closed_loop"]["denominator"]) == [1.0, 2.0, 4.0]
    p = r.data["poles"][0]
    assert p["natural_frequency"] == pytest.approx(2.0, abs=1e-12) and p["damping_ratio"] == pytest.approx(0.5, abs=1e-12)
    assert v(r, "system_type") == 1 and v(r, "velocity_error_constant") == pytest.approx(4.0)  # Kv = lim s (s+4)/(s (s+1)) = 4
    assert v(r, "ramp_tracking_error") == pytest.approx(0.25)
    # simulated overshoot of T(s): y(t) = 1 + 2 Re(A exp(p t)), A = (p + 4)/(p (p - conj p)), p = -1 + j sqrt 3  (partial fractions of T(s)/s)
    pole = complex(-1.0, math.sqrt(3.0))
    amp = (pole + 4) / (pole * (pole - pole.conjugate()))
    t = np.linspace(0, 12, 2_400_001)
    y = 1 + 2 * np.real(amp * np.exp(pole * t))
    expected_overshoot = 100 * (y.max() - 1)
    expected_peak_time = t[int(np.argmax(y))]
    assert expected_overshoot == pytest.approx(19.10, abs=0.01)  # the independent partial-fraction value (the zero at -4 raises it above the 16.3 % of zeta = 0.5 alone)
    sim = ok(response, numerator="[1, 4]", denominator="[1, 2, 4]")
    assert sim.data["metrics"]["overshoot_percent"] == pytest.approx(expected_overshoot, abs=2e-3)
    assert sim.data["metrics"]["peak_time"] == pytest.approx(expected_peak_time, abs=2e-3)
    # the pure second-order formula (no zero) would give 100 exp(-pi*0.5/sqrt(0.75)) = 16.3 %: the zero makes it larger
    assert expected_overshoot > 16.3034


def test_sensitivity_peak_of_the_loop_one_over_s_s_plus_one():
    # L = 1/(s(s+1)): |S|^2 = (u^2 + u)/(u^2 - u + 1), u = w^2 ; d/du = 0 -> -2u^2 + 2u + 1 = 0 -> u = (1 + sqrt 3)/2 = 1.366025
    # |S|^2 = (1.866025 + 1.366025)/(1.866025 - 1.366025 + 1) = 3.232051/1.5 = 2.154701 -> Ms = 1.467890 at w = sqrt(u) = 1.168770
    u = (1 + math.sqrt(3)) / 2
    ms = math.sqrt((u * u + u) / (u * u - u + 1))
    assert ms == pytest.approx(1.467890, abs=1e-6)
    r = ok(feedback, plant_numerator="1", plant_denominator="s (s + 1)")
    assert v(r, "sensitivity_peak") == pytest.approx(ms, abs=1e-7)
    assert v(r, "sensitivity_peak_frequency") == pytest.approx(math.sqrt(u), abs=1e-6)
    # guaranteed margins: GM >= Ms/(Ms - 1) = 3.1397, PM >= 2 asin(1/(2 Ms)) = 2 * 19.9 = 39.8 degrees
    assert v(r, "guaranteed_gain_margin") == pytest.approx(ms / (ms - 1), rel=1e-7)
    assert v(r, "guaranteed_phase_margin") == pytest.approx(math.degrees(2 * math.asin(1 / (2 * ms))), abs=1e-5)
    # T = 1/(s^2 + s + 1): zeta = 0.5, wn = 1 -> Mt = 1/(2 zeta sqrt(1 - zeta^2)) = 1.154701
    assert v(r, "complementary_sensitivity_peak") == pytest.approx(1 / (2 * 0.5 * math.sqrt(0.75)), abs=1e-7)


def test_sensitivity_peak_against_a_brute_force_scan_for_a_third_order_loop():
    r = ok(feedback, plant_numerator="2", plant_denominator="s (s+1)(s+2)")
    w = np.logspace(-3, 3, 400_000)
    s = 1j * w
    s_val = np.abs(1.0 / (1.0 + 2.0 / (s * (s + 1) * (s + 2))))
    assert v(r, "sensitivity_peak") == pytest.approx(s_val.max(), rel=1e-6)
    assert v(r, "sensitivity_peak_frequency") == pytest.approx(w[int(np.argmax(s_val))], rel=1e-3)


def test_a_loop_without_peaking_has_sensitivity_peak_one():
    r = ok(feedback, plant_numerator="1", plant_denominator="[1, 0]")  # L = 1/s: S = s/(s+1), |S| < 1 and -> 1
    assert v(r, "sensitivity_peak") == pytest.approx(1.0, abs=1e-9) and "guaranteed_gain_margin" not in r.values


def test_unstable_closed_loop_is_reported_and_errors_are_not_applicable():
    r = ok(feedback, plant_numerator="10", plant_denominator="s (s+1)(s+2)")  # K = 10 > 6: two RHP roots
    assert r.data["stability"]["verdict"] == "unstable" and v(r, "closed_loop_unstable_poles") == 2
    assert r.data["steady_state_errors"]["applicable"] is False
    assert "not applicable" in r.data["steady_state_errors"]["ramp"]["tracking_error"]
    assert "ramp_tracking_error" not in r.values and any("closed loop is unstable" in w for w in r.warnings)
    assert r.data["error_constants"]["Kv"] == 5.0  # the constants themselves are still defined (10/2)


def test_hidden_modes_are_flagged():
    # the controller cancels the plant pole at -1: C G = 2/s, T = 2/(s+2) but the characteristic polynomial keeps the (s+1) factor
    r = ok(feedback, plant_numerator="1", plant_denominator="[1, 1]", controller_numerator="[2, 2]", controller_denominator="[1, 0]")
    assert r.data["numerical_checks"]["near_pole_zero_cancellations"], "T = Cn Gn Hd / P keeps a common factor"
    assert any("pole-zero cancellation" in w for w in r.warnings)
    assert sorted(p["real"] for p in r.data["poles"]) == pytest.approx([-2.0, -1.0])


def test_refusals():
    fails(feedback, "input.invalid", "controller_numerator", plant_numerator="1", plant_denominator="[1, 1]", controller_numerator="[1, 2, 3]")  # C G H = (s^2 + 2 s + 3)/(s + 1): improper
    fails(feedback, "input.invalid", "controller_numerator", plant_numerator="1", plant_denominator="[1]", sensor_numerator="-1")  # P = 1 - 1 = 0
    fails(feedback, "input.invalid", "controller_numerator", plant_numerator="[-1, -2]", plant_denominator="[1, 1]")  # L(infinity) = -1: the leading terms cancel in 1 + L (algebraic loop)
    fails(feedback, "input.invalid", "plant_denominator", plant_numerator="1", plant_denominator="[0, 1]")
    fails(feedback, "input.invalid", "sensor_denominator", plant_numerator="1", plant_denominator="[1, 1]", sensor_denominator="")
    fails(feedback, "input.out_of_range", "open_loop_denominator", plant_numerator="1", plant_denominator="(s+1)^20", controller_denominator="(s+1)^21")
    fails(feedback, "input.invalid", "plant_numerator", plant_numerator="s + sin(s)", plant_denominator="[1, 1]")
