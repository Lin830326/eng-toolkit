"""control_discretize checked against the closed-form discrete equivalents of K/(s + a)."""

import cmath
import math

import numpy as np
import pytest

from eng_toolkit.domains.control.discretize import discretize
from eng_toolkit.domains.control.response import response
from tests.control_testing import fails, ok, table, v

K, A, T = 3.0, 2.0, 0.1
FIRST = dict(numerator="3", denominator="[1, 2]", sample_time="0.1 s")  # G = 3/(s + 2), T = 0.1 s


def dnum(r):
    return r.data["discrete"]["numerator"]["coefficients_descending"]


def dden(r):
    return r.data["discrete"]["denominator"]["coefficients_descending"]


def test_zoh_of_k_over_s_plus_a_is_the_textbook_result():
    # Gd(z) = K (1 - e^{-aT})/a / (z - e^{-aT}):  e^{-0.2} = 0.8187308, numerator 3*(0.1812692)/2 = 0.2719039
    p = math.exp(-A * T)
    r = ok(discretize, **FIRST, method="zoh")
    assert dnum(r) == pytest.approx([K * (1 - p) / A], rel=1e-13) and dden(r) == pytest.approx([1.0, -p], rel=1e-13)
    assert dnum(r)[0] == pytest.approx(0.2719039, abs=1e-7) and dden(r)[1] == pytest.approx(-0.8187308, abs=1e-7)
    assert v(r, "dc_gain_continuous") == pytest.approx(1.5) and v(r, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-13)  # G(0) = 3/2 = Gd(1)
    assert r.data["discrete"]["domain"] == "discrete" and r.data["discrete"]["sample_time_s"] == T
    assert r.data["stability_map"]["stability_preserved"] is True and r.data["stability_map"]["discrete_verdict"] == "stable"
    assert "(1 - z^-1) Z{G(s)/s}" in r.data["formula"]


def test_tustin_of_one_over_s_plus_a_is_the_bilinear_result():
    # s = (2/T)(z-1)/(z+1): K/(s + a) = K T (z + 1)/((2 + a T) z + (a T - 2)) -> monic: K T/(2 + aT) (z + 1)/(z - (2 - aT)/(2 + aT))
    r = ok(discretize, **FIRST, method="tustin")
    g = K * T / (2 + A * T)  # 0.3/2.2 = 0.136364
    assert dnum(r) == pytest.approx([g, g], rel=1e-13) and dden(r) == pytest.approx([1.0, (A * T - 2) / (2 + A * T)], rel=1e-13)
    assert dnum(r)[0] == pytest.approx(0.1363636, abs=1e-7) and dden(r)[1] == pytest.approx(-0.8181818, abs=1e-7)
    assert v(r, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-13)
    assert r.data["discrete_zeros"][0]["real"] == pytest.approx(-1.0, abs=1e-12)  # the zero at z = -1 from the zero at infinity


def test_tustin_prewarp_matches_the_response_exactly_at_the_prewarp_frequency():
    # with s = c (z-1)/(z+1), c = w0/tan(w0 T/2), Gd(e^{j w0 T}) = G(j w0) for any G
    w0, step = 2.0, 0.5
    r = ok(discretize, numerator="1", denominator="[1, 3, 2]", sample_time="0.5 s", method="tustin", prewarp_frequency=w0)
    z = cmath.exp(1j * w0 * step)
    gd = np.polyval(dnum(r), z) / np.polyval(dden(r), z)
    g = 1.0 / ((1j * w0) ** 2 + 3j * w0 + 2)
    assert abs(gd - g) < 1e-13
    assert "w0/tan(w0 T/2)" in r.data["formula"] and r.data["prewarp_frequency_rad_s"] == 2.0
    # without prewarping the match is only approximate
    plain = ok(discretize, numerator="1", denominator="[1, 3, 2]", sample_time="0.5 s", method="tustin")
    gd_plain = np.polyval(dnum(plain), z) / np.polyval(dden(plain), z)
    assert abs(gd_plain - g) > 1e-3


def test_euler_methods_by_hand():
    # forward: s = (z-1)/T -> K/(s + a) = K T/(z - 1 + aT) = 0.3/(z - 0.8)
    f = ok(discretize, **FIRST, method="forward_euler")
    assert dnum(f) == pytest.approx([0.3], abs=1e-14) and dden(f) == pytest.approx([1.0, -(1 - A * T)], abs=1e-14)
    # backward: s = (z-1)/(T z) -> K T z/((1 + aT) z - 1) = 0.25 z/(z - 0.833333) after dividing by 1.2 : numerator 0.3/1.2 = 0.25
    b = ok(discretize, **FIRST, method="backward_euler")
    assert dnum(b) == pytest.approx([0.25, 0.0], abs=1e-14) and dden(b) == pytest.approx([1.0, -1 / 1.2], abs=1e-14)
    assert v(f, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-13) and v(b, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-13)


def test_forward_euler_can_destabilise_a_stable_system_and_the_map_says_so():
    # pole -a, T = 1.5 s, a = 2: z = 1 - aT = -2 (|z| = 2 > 1) although the continuous pole is stable
    r = ok(discretize, numerator="1", denominator="[1, 2]", sample_time="1.5 s", method="forward_euler")
    pm = r.data["pole_map"][0]
    assert pm["discrete_pole"]["real"] == pytest.approx(-2.0) and pm["magnitude"] == pytest.approx(2.0) and pm["stable_continuous"] is True and pm["stable_discrete"] is False
    assert r.data["stability_map"]["stability_preserved"] is False and r.data["stability_map"]["discrete_verdict"] == "unstable"
    assert v(r, "discrete_unstable_poles") == 1 and any("NOT" in w for w in r.warnings)
    # backward Euler is stable for the same pole: z = 1/(1 + aT) = 0.25
    b = ok(discretize, numerator="1", denominator="[1, 2]", sample_time="1.5 s", method="backward_euler")
    assert b.data["pole_map"][0]["discrete_pole"]["real"] == pytest.approx(0.25) and b.data["stability_map"]["discrete_verdict"] == "stable"
    # backward Euler of an UNSTABLE pole s = 3, T = 1: z = 1/(1 - 3) = -0.5, inside the unit circle: the map turns an unstable system stable
    u = ok(discretize, numerator="1", denominator="[1, -3]", sample_time="1 s", method="backward_euler")
    assert u.data["pole_map"][0]["stable_continuous"] is False and u.data["pole_map"][0]["stable_discrete"] is True
    assert any("unstable but its discretisation is stable" in w for w in u.warnings)


def test_first_order_hold_by_hand():
    # Phi = p = e^{-aT}, Gamma1 = K (1 - p)/a, Gamma2 = K (1/a - (1 - p)/(a^2 T)) ; Gd = Dd + Bd/(z - p), Dd = Gamma2, Bd = Gamma1 + (p - 1) Gamma2
    # -> numerator [Gamma2, Gamma1 - Gamma2] over [1, -p]
    p = math.exp(-A * T)
    g1 = K * (1 - p) / A
    g2 = K * (1 / A - (1 - p) / (A * A * T))
    r = ok(discretize, **FIRST, method="foh")
    assert dnum(r) == pytest.approx([g2, g1 - g2], rel=1e-12) and dden(r) == pytest.approx([1.0, -p], rel=1e-13)
    assert dnum(r)[0] == pytest.approx(0.140481, abs=1e-6) and dnum(r)[1] == pytest.approx(0.131423, abs=1e-6)
    assert v(r, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-12)


def test_matched_pole_zero():
    # K/(s + a): pole -> e^{-aT}, no finite zero, relative degree 1 -> 0 extra zeros ; gain from Gd(1) = G(0): K/a * (1 - p)
    p = math.exp(-A * T)
    r = ok(discretize, **FIRST, method="matched")
    assert dden(r) == pytest.approx([1.0, -p], rel=1e-13) and dnum(r) == pytest.approx([K * (1 - p) / A], rel=1e-13)
    # (s + 1)/(s^2 + 3 s + 2) = 1/(s + 2) with a (numerically exact) cancellation is not needed; use a real zero: (s+3)/((s+1)(s+2)), T = 0.5
    #   poles e^{-0.5}, e^{-1}; zero e^{-1.5}; relative degree 1 -> no extra; DC: G(0) = 3/2
    r = ok(discretize, numerator="s + 3", denominator="(s+1)(s+2)", sample_time="0.5 s", method="matched")
    assert sorted(x["real"] for x in r.data["discrete_poles"]) == pytest.approx(sorted([math.exp(-0.5), math.exp(-1.0)]), abs=1e-13)
    assert r.data["discrete_zeros"][0]["real"] == pytest.approx(math.exp(-1.5), abs=1e-13)
    assert v(r, "dc_gain_discrete") == pytest.approx(1.5, rel=1e-12)
    # relative degree 2 adds one zero at z = -1 (relative degree - 1)
    r = ok(discretize, numerator="1", denominator="(s+1)(s+2)", sample_time="0.5 s", method="matched")
    assert [z["real"] for z in r.data["discrete_zeros"]] == pytest.approx([-1.0], abs=1e-12)
    # an integrator: the gain is matched in magnitude at a low frequency instead of DC
    r = ok(discretize, numerator="1", denominator="[1, 0]", sample_time="0.5 s", method="matched")
    assert any("magnitude" in w for w in r.warnings)


def test_zoh_poles_are_exponentials_of_the_continuous_poles():
    r = ok(discretize, numerator="1", denominator="(s+1)(s+2)", sample_time="0.5 s", method="zoh")
    assert sorted(x["real"] for x in r.data["discrete_poles"]) == pytest.approx(sorted([math.exp(-0.5), math.exp(-1.0)]), abs=1e-13)
    assert all(abs(row["discrete_pole"]["real"] - [math.exp(-0.5), math.exp(-1.0)][i]) < 1e-13 for i, row in enumerate(r.data["pole_map"]))
    # complex poles: s = -2 +- j 3.464102 -> z = e^{-2T} (cos(3.464102 T) +- j sin(...)), T = 0.1
    r = ok(discretize, numerator="16", denominator="[1, 4, 16]", sample_time="0.1 s", method="zoh")
    z = cmath.exp(complex(-2.0, math.sqrt(12.0)) * 0.1)
    got = sorted(r.data["discrete_poles"], key=lambda d: -d["imag"])[0]
    assert complex(got["real"], got["imag"]) == pytest.approx(z, abs=1e-13)
    # a ZOH model has a 'sinc' magnitude and a w T/2 phase lag relative to the bare G(jw): the comparison table says so
    assert "phase lag" in r.data["frequency_response_comparison"]["note"]


def test_zoh_discretisation_reproduces_the_continuous_step_response_at_the_samples():
    # step invariance: the ZOH model's step response equals the continuous step response y(t) = 1/2 - e^-t + e^-2t/2 at t = k T
    cont = ok(response, numerator="1", denominator="(s+1)(s+2)", t_final="5 s", samples=11)
    disc = ok(discretize, numerator="1", denominator="(s+1)(s+2)", sample_time="0.5 s", method="zoh")
    sim = ok(response, numerator=str(dnum(disc)), denominator=str(dden(disc)), domain="discrete", sample_time="0.5 s", t_final="5 s")
    yc, yd = table(cont, "response.csv")["output"], table(sim, "response.csv")["output"]
    t = np.arange(len(yd)) * 0.5
    assert np.max(np.abs(yd - (0.5 - np.exp(-t) + 0.5 * np.exp(-2 * t)))) < 1e-9
    assert np.max(np.abs(yc - (0.5 - np.exp(-table(cont, "response.csv")["time_s"]) + 0.5 * np.exp(-2 * table(cont, "response.csv")["time_s"])))) < 1e-9


def test_difference_equation_and_pole_zero_text():
    r = ok(discretize, **FIRST, method="forward_euler")
    de = r.data["difference_equation"]
    # 0.3/(z - 0.8): y[k] = 0.8 y[k-1] + 0.3 u[k-1]
    assert de["b"] == pytest.approx([0.0, 0.3]) and de["a"] == pytest.approx([1.0, -0.8])
    assert de["text"].startswith("y[k] = 0.3 u[k-1]") and "0.8 y[k-1]" in de["text"]
    t = ok(discretize, **FIRST, method="tustin").data["difference_equation"]
    assert len(t["b"]) == 2 and t["b"][0] == pytest.approx(t["b"][1])


def test_second_order_tustin_by_hand():
    # G = 1/(s^2 + 3 s + 2), T = 1: s = 2 (z-1)/(z+1): numerator (z+1)^2, denominator 4 (z-1)^2 + 6 (z-1)(z+1) + 2 (z+1)^2
    #   = 4z^2 - 8z + 4 + 6z^2 - 6 + 2z^2 + 4z + 2 = 12 z^2 - 4 z + 0  -> Gd = (z^2 + 2z + 1)/(12 z^2 - 4 z) = (1/12)(z^2 + 2z + 1)/(z^2 - z/3)
    r = ok(discretize, numerator="1", denominator="[1, 3, 2]", sample_time="1 s", method="tustin")
    assert dnum(r) == pytest.approx([1 / 12, 2 / 12, 1 / 12], abs=1e-14) and dden(r) == pytest.approx([1.0, -1 / 3, 0.0], abs=1e-14)
    assert r.data["stability_map"]["discrete_verdict"] == "stable"


def test_sampling_warnings():
    r = ok(discretize, numerator="1", denominator="[1, 100]", sample_time="0.1 s", method="zoh")  # |p| T = 10
    assert any("long compared with the fastest dynamics" in w for w in r.warnings)


def test_pure_gain_and_high_order():
    r = ok(discretize, numerator="5", denominator="2", sample_time="1 s", method="zoh")
    assert dnum(r) == [2.5] and dden(r) == [1.0]
    for method in ("zoh", "foh", "tustin", "forward_euler", "backward_euler", "matched"):
        r = ok(discretize, numerator="1", denominator="(s+1)^3 (s+2)", sample_time="0.2 s", method=method)
        assert r.data["discrete"]["denominator"]["order"] == 4
        assert v(r, "dc_gain_discrete") == pytest.approx(v(r, "dc_gain_continuous"), rel=1e-9 if method not in {"matched"} else 1e-6)


def test_refusals():
    fails(discretize, "input.invalid", "numerator", numerator="[1, 0, 0]", denominator="[1, 1]", sample_time="0.1 s")  # improper
    fails(discretize, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", sample_time="-0.1 s")
    fails(discretize, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", sample_time="0 s")
    fails(discretize, "units.bare_number", "sample_time", numerator="1", denominator="[1, 1]", sample_time="0.1")
    fails(discretize, "input.out_of_range", "sample_time", numerator="1", denominator="[1, 1]", sample_time="1e-12 s")
    fails(discretize, "input.out_of_range", "sample_time", numerator="1", denominator="[1, 1]", sample_time="1e12 s")
    fails(discretize, "input.out_of_range", "sample_time", numerator="1", denominator="[1, 1]", sample_time="1" + "0" * 5000 + " s")
    fails(discretize, "input.invalid", "method", numerator="1", denominator="[1, 1]", sample_time="0.1 s", method="magic")
    fails(discretize, "input.invalid", "prewarp_frequency", numerator="1", denominator="[1, 1]", sample_time="0.1 s", method="zoh", prewarp_frequency=2.0)
    fails(discretize, "input.out_of_range", "prewarp_frequency", numerator="1", denominator="[1, 1]", sample_time="0.1 s", method="tustin", prewarp_frequency=40.0)  # above pi/T = 31.4
    fails(discretize, "input.invalid", "prewarp_frequency", numerator="1", denominator="[1, 1]", sample_time="0.1 s", method="tustin", prewarp_frequency=-1.0)
    fails(discretize, "input.invalid", "prewarp_frequency", numerator="1", denominator="[1, 1]", sample_time="0.1 s", method="tustin", prewarp_frequency=float("nan"))
    fails(discretize, "input.invalid", "numerator", numerator="z + 1", denominator="[1, 1]", sample_time="0.1 s")  # a continuous model is written in s
    fails(discretize, "input.invalid", "denominator", numerator="1", denominator="[0, 1]", sample_time="0.1 s")
