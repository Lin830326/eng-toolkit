"""control_response checked against closed-form time responses derived by hand."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.control.response import response
from tests.control_testing import fails, ok, table, v

WN, ZETA = 4.0, 0.5
SECOND_ORDER = dict(numerator="16", denominator="[1, 4, 16]")


def second_order_step(t, wn=WN, zeta=ZETA):
    """y(t) = 1 - exp(-zeta wn t) (cos wd t + zeta/sqrt(1-zeta^2) sin wd t), wd = wn sqrt(1 - zeta^2)."""
    wd = wn * math.sqrt(1.0 - zeta**2)
    return 1.0 - np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta / math.sqrt(1.0 - zeta**2) * np.sin(wd * t))


def test_second_order_metrics_by_hand():
    # zeta = 0.5, wn = 4: percent overshoot 100 exp(-pi zeta/sqrt(1-zeta^2)) = 100 exp(-1.813799) = 16.3034 %
    # peak time pi/(wn sqrt(1-zeta^2)) = pi/(4*0.8660254) = 0.906900 s ; final value 1 ; steady-state error 0
    r = ok(response, **SECOND_ORDER)
    m = r.data["metrics"]
    assert m["overshoot_percent"] == pytest.approx(100 * math.exp(-math.pi * ZETA / math.sqrt(1 - ZETA**2)), abs=2e-3)
    assert m["overshoot_percent"] == pytest.approx(16.3034, abs=2e-3)
    assert m["peak_time"] == pytest.approx(math.pi / (WN * math.sqrt(1 - ZETA**2)), abs=1e-4)
    assert m["peak_time"] == pytest.approx(0.9069, abs=1e-4)
    assert m["peak_value"] == pytest.approx(1.163034, abs=2e-5)
    assert m["steady_state_value"] == pytest.approx(1.0, abs=1e-12) and m["steady_state_error"] == pytest.approx(0.0, abs=1e-12)
    # 10-90 % rise time: solve y(t) = 0.1 and 0.9 on the closed form by bisection
    def solve(level):
        lo, hi = 0.0, 0.9069
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if second_order_step(np.array(mid)) < level:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
    assert m["rise_time"] == pytest.approx(solve(0.9) - solve(0.1), abs=2e-4)
    # 2 % settling: last time |y - 1| > 0.02 on a fine grid of the closed form
    fine = np.linspace(0.0, 5.0, 500_001)
    outside = np.nonzero(np.abs(second_order_step(fine) - 1.0) > 0.02)[0][-1]
    assert m["settling_time"] == pytest.approx(fine[outside], abs=2e-3)
    assert r.values["peak_time"].unit == "second" and r.values["overshoot"].unit == "percent"
    assert r.data["time_grid"]["end_s"] == pytest.approx(5.0)  # ten times the slowest time constant 1/(zeta wn) = 0.5 s
    assert r.data["time_grid"]["samples"] == 2001


def test_second_order_table_agrees_with_the_closed_form_to_1e_minus_9():
    r = ok(response, **SECOND_ORDER)
    data = table(r, "response.csv")
    assert set(data) == {"time_s", "input", "output"}
    assert len(data["time_s"]) == 2001 and data["time_s"][0] == 0.0
    assert np.all(data["input"] == 1.0)
    assert np.max(np.abs(data["output"] - second_order_step(data["time_s"]))) < 1e-9


def test_first_order_by_hand():
    # G = 1/(tau s + 1), tau = 2 s: y(tau) = 1 - 1/e = 0.632121 ; rise = tau ln 9 = 4.394449 ; settling (2 %) = tau ln 50 = 7.824046
    r = ok(response, numerator="1", denominator="[2, 1]")
    data = table(r, "response.csv")
    at_tau = int(np.argmin(np.abs(data["time_s"] - 2.0)))
    assert data["time_s"][at_tau] == pytest.approx(2.0, abs=1e-12)
    assert data["output"][at_tau] == pytest.approx(1 - 1 / math.e, abs=1e-12)
    m = r.data["metrics"]
    assert m["rise_time"] == pytest.approx(2 * math.log(9), abs=1e-4)
    assert m["settling_time"] == pytest.approx(2 * math.log(50), abs=1e-4)
    assert m["overshoot_percent"] == 0.0
    assert m["steady_state_value"] == pytest.approx(1.0)
    # a different band: 5 % -> tau ln 20 = 5.991465
    r = ok(response, numerator="1", denominator="[2, 1]", settling_tolerance=0.05)
    assert r.data["metrics"]["settling_time"] == pytest.approx(2 * math.log(20), abs=1e-4)


def test_dc_gain_other_than_one_gives_a_steady_state_error():
    # 3/(s + 4): final value 3/4 ; steady-state error of a unit step 1 - 0.75 = 0.25 ; no overshoot
    r = ok(response, numerator="3", denominator="[1, 4]")
    m = r.data["metrics"]
    assert m["steady_state_value"] == pytest.approx(0.75, abs=1e-12) and m["steady_state_error"] == pytest.approx(0.25, abs=1e-12)
    assert m["overshoot_percent"] == 0.0 and m["peak_value"] == pytest.approx(0.75, abs=1e-4)  # the window ends at 10 time constants: 0.75 (1 - e^-10)
    # negative gain: -2/(s+1) settles to -2; the metrics are mirrored (overshoot relative to |final|)
    r = ok(response, numerator="-2", denominator="[1, 1]")
    assert r.data["metrics"]["steady_state_value"] == pytest.approx(-2.0) and r.data["metrics"]["settling_time"] == pytest.approx(math.log(50), abs=1e-4)


def test_impulse_and_ramp_closed_forms():
    # impulse of 1/(s+1): y = exp(-t): peak 1 at t = 0, 2 % settling at ln 50
    r = ok(response, numerator="1", denominator="[1, 1]", input_type="impulse")
    data = table(r, "response.csv")
    assert np.max(np.abs(data["output"] - np.exp(-data["time_s"]))) < 1e-12
    assert r.data["metrics"]["peak_value"] == pytest.approx(1.0) and r.data["metrics"]["peak_time"] == 0.0
    assert r.data["metrics"]["settling_time"] == pytest.approx(math.log(50), abs=1e-2)
    # ramp of 1/(s+1): y = t - 1 + exp(-t) ; error t - y = 1 - exp(-t) -> 1
    r = ok(response, numerator="1", denominator="[1, 1]", input_type="ramp")
    data = table(r, "response.csv")
    assert np.max(np.abs(data["output"] - (data["time_s"] - 1 + np.exp(-data["time_s"])))) < 1e-9
    assert np.allclose(data["input"], data["time_s"])
    assert r.data["metrics"]["steady_state_error_unit_ramp"] == pytest.approx(1.0, abs=1e-3)
    # ramp of a type-1 loop: 1/(s+1) is type 0 above; T = 2/(s+2) -> error t - y -> 1/2 ... DC gain 1 is needed for a finite error, here gain 2/2 = 1: error = 1/Kv-like = 1/2
    r = ok(response, numerator="2", denominator="[1, 2]", input_type="ramp")  # y = t - 1/2 + exp(-2t)/2 -> error 1/2
    assert r.data["metrics"]["steady_state_error_unit_ramp"] == pytest.approx(0.5, abs=1e-3)


def test_partial_fraction_reference_for_order_six():
    # G = 1/((s+1)(s+2)...(s+6)); residue of G(s)/s at s = -i is 1/((-i) prod_{j != i} (j - i)), at s = 0 it is 1/720
    roots = np.arange(1, 7, dtype=float)
    r = ok(response, numerator="1", denominator="(s+1)(s+2)(s+3)(s+4)(s+5)(s+6)")
    data = table(r, "response.csv")
    t = data["time_s"]
    y = np.full_like(t, 1.0 / 720.0)
    for i in roots:
        others = np.prod([j - i for j in roots if j != i])
        y += np.exp(-i * t) / ((-i) * others)
    assert np.max(np.abs(data["output"] - y)) < 1e-9
    assert r.data["metrics"]["steady_state_value"] == pytest.approx(1 / 720, rel=1e-12)


def test_state_space_matches_the_equivalent_transfer_function():
    # controllable canonical form of 16/(s^2 + 4 s + 16): A = [[0, 1], [-16, -4]], B = [0, 1], C = [16, 0]
    tf = ok(response, **SECOND_ORDER)
    ss = ok(response, state_a="[[0, 1], [-16, -4]]", state_b="[0, 1]", state_c="[16, 0]")
    a, b = table(tf, "response.csv"), table(ss, "response.csv")
    assert np.max(np.abs(a["output"] - b["output"])) < 1e-9
    assert ss.data["metrics"]["overshoot_percent"] == pytest.approx(tf.data["metrics"]["overshoot_percent"], abs=1e-6)
    # a column B and a state with direct feed-through: y = x + 2 u with x' = -x + u -> 2 + (1 - e^-t) ... step: y(0) = 2, y(inf) = 3
    r = ok(response, state_a="[[-1]]", state_b="[[1]]", state_c="[[1]]", state_d="2")
    data = table(r, "response.csv")
    assert data["output"][0] == pytest.approx(2.0) and data["output"][-1] == pytest.approx(3.0, abs=1e-3)
    assert np.max(np.abs(data["output"] - (3 - np.exp(-data["time_s"])))) < 1e-9


def test_discrete_first_order_recursion_by_hand():
    # G(z) = 0.1/(z - 0.9): y[k] = 0.9 y[k-1] + 0.1 u[k-1] -> y[k] = 1 - 0.9^k for a unit step ; G(1) = 0.1/0.1 = 1
    r = ok(response, numerator="[0.1]", denominator="[1, -0.9]", domain="discrete", sample_time="0.1 s")
    data = table(r, "response.csv")
    k = np.arange(len(data["output"]))
    assert np.max(np.abs(data["output"] - (1 - 0.9**k))) < 1e-12
    assert np.allclose(data["time_s"], 0.1 * k, atol=1e-12)
    assert r.data["metrics"]["steady_state_value"] == pytest.approx(1.0, abs=1e-12)
    assert r.data["time_grid"]["discretisation"] == "discrete recursion"
    # impulse: the unit pulse -> y[k] = 0.1 * 0.9^(k-1) for k >= 1
    r = ok(response, numerator="[0.1]", denominator="[1, -0.9]", domain="discrete", sample_time="0.1 s", input_type="impulse")
    data = table(r, "response.csv")
    assert data["output"][0] == 0.0 and data["output"][1] == pytest.approx(0.1) and data["output"][3] == pytest.approx(0.1 * 0.81, rel=1e-12)
    # explicit window: 11 samples
    r = ok(response, numerator="[0.1]", denominator="[1, -0.9]", domain="discrete", sample_time="0.1 s", t_final="1 s")
    assert len(table(r, "response.csv")["output"]) == 11


def test_unstable_and_marginal_systems_report_instead_of_failing():
    r = ok(response, numerator="1", denominator="[1, -1]")  # y = exp(t) - 1
    m = r.data["metrics"]
    assert r.data["stability"] == "unstable" and m["settling_time"] == "unbounded" and m["overshoot_percent"] == "unbounded"
    assert m["steady_state_value"] == "unbounded"
    data = table(r, "response.csv")
    assert np.max(np.abs(data["output"] - (np.exp(data["time_s"]) - 1))) / np.exp(data["time_s"][-1]) < 1e-9
    assert "overshoot" not in r.values and "settling_time" not in r.values
    # a divergence far beyond 1e100 is cut, never infinity
    r = ok(response, numerator="1", denominator="[1, -50]", t_final="100 s")
    assert any("diverged" in w for w in r.warnings)
    data = table(r, "response.csv")
    assert np.all(np.isfinite(data["output"])) and np.max(np.abs(data["output"])) < 1e100
    # an undamped oscillator never settles; an integrator drifts
    r = ok(response, numerator="1", denominator="[1, 0, 1]")
    assert r.data["stability"] == "marginally stable" and str(r.data["metrics"]["settling_time"]).startswith("never")
    r = ok(response, numerator="1", denominator="[1, 0]")
    assert str(r.data["metrics"]["settling_time"]).startswith("never")


def test_pure_gain_and_zero_final_value():
    r = ok(response, numerator="5", denominator="2")
    assert r.data["metrics"]["steady_state_value"] == pytest.approx(2.5)
    assert np.all(table(r, "response.csv")["output"] == 2.5)
    # a washout s/(s+1): final value 0 -> overshoot and settling band are undefined, said so
    r = ok(response, numerator="[1, 0]", denominator="[1, 1]")
    assert r.data["metrics"]["overshoot_percent"] is None and "undefined" in r.data["metrics"]["reason"]
    assert r.data["metrics"]["peak_value"] == pytest.approx(1.0, abs=1e-6)  # y = exp(-t)


def test_a_window_too_short_is_reported():
    r = ok(response, numerator="1", denominator="[2, 1]", t_final="6 s")  # tau = 2 s: 3 tau - past the 90 % point (4.39 s) but not inside the 2 % band (7.82 s)
    assert any("settled" in w for w in r.warnings)
    assert r.data["metrics"]["settling_time"] is None and r.data["metrics"]["rise_time"] == pytest.approx(2 * math.log(9), abs=1e-4)


def test_stiff_system_uses_the_fast_pole_for_the_step_and_the_slow_one_for_the_window():
    # (s+1)(s+1000): slowest decay 1 -> 10 s ; fastest 1000 -> 10 samples per time constant -> 100 001 > cap -> 100 000 samples
    r = ok(response, numerator="1000", denominator="(s+1)(s+1000)")
    assert r.data["time_grid"]["samples"] == 100_000 and r.data["time_grid"]["end_s"] == pytest.approx(10.0)
    y = table(r, "response.csv")["output"]
    assert y[-1] == pytest.approx(1.0, abs=1e-4)


def test_refusals():
    fails(response, "input.invalid", "denominator", numerator="1", denominator="[0, 1, 2]")
    fails(response, "input.invalid", "numerator", numerator="[1, 0, 0]", denominator="[1, 1]")  # improper
    fails(response, "input.missing", "numerator|denominator")
    fails(response, "input.out_of_range", "samples", numerator="1", denominator="[1, 1]", samples=1_000_000_000)
    fails(response, "input.invalid", "samples", numerator="1", denominator="[1, 1]", samples=5)
    fails(response, "input.out_of_range", "t_final", numerator="1", denominator="[1, 1]", t_final="1e-12 s")
    fails(response, "input.invalid", "t_final", numerator="1", denominator="[1, 1]", t_final="-5 s")
    fails(response, "units.dimension_mismatch", "t_final", numerator="1", denominator="[1, 1]", t_final="5 m")
    fails(response, "input.invalid", "settling_tolerance", numerator="1", denominator="[1, 1]", settling_tolerance=0.0)
    fails(response, "input.invalid", "input_type", numerator="1", denominator="[1, 1]", input_type="sine")
    fails(response, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", sample_time="0.1 s")  # continuous model with a sample time
    fails(response, "input.missing", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete")
    fails(response, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete", sample_time="-1 s")
    fails(response, "input.out_of_range", "t_final", numerator="1", denominator="[1, -0.5]", domain="discrete", sample_time="1 s", t_final="1000000 s")
    fails(response, "input.invalid", "state_a", numerator="1", denominator="[1, 1]", state_a="[[0]]", state_b="[1]", state_c="[1]")  # both forms
    fails(response, "input.invalid", "state_a", state_a="[[0, 1]]", state_b="[0]", state_c="[1]")  # not square
    fails(response, "input.invalid", "state_b", state_a="[[0, 1], [-1, -1]]", state_b="[[0, 1], [1, 0]]", state_c="[1, 0]")  # MIMO
    fails(response, "input.invalid", "state_c", state_a="[[0, 1], [-1, -1]]", state_b="[0, 1]", state_c="[[1, 0], [0, 1]]")  # MIMO
    fails(response, "input.invalid", "state_a", state_a="[[1e999]]", state_b="[1]", state_c="[1]")
    fails(response, "input.invalid", "state_a", state_a="not json", state_b="[1]", state_c="[1]")
    fails(response, "input.out_of_range", "state_a", state_a="[" + ",".join(["[1]"] * 50) + "]", state_b="[1]", state_c="[1]")
