"""control_frequency checked against closed-form frequency responses and margins."""

import cmath
import math

import numpy as np
import pytest

from eng_toolkit.domains.control.frequency import frequency
from tests.control_testing import fails, ok, table, v

LOOP = dict(denominator="s (s + 1)(s + 2)")  # L(s) = K / (s (s+1)(s+2))


def loop(k):
    return dict(numerator=str(k), **LOOP)


def solve_gain_crossover(k):
    """|L(j w)| = k / (w sqrt(w^2 + 1) sqrt(w^2 + 4)) = 1 by bisection (independent of the tool)."""
    f = lambda w: k / (w * math.sqrt(w * w + 1) * math.sqrt(w * w + 4)) - 1.0
    lo, hi = 1e-6, 1e3
    for _ in range(300):
        mid = math.sqrt(lo * hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def test_margins_of_the_classic_third_order_loop_k_equals_one():
    # phase crossover: -90 - atan(w) - atan(w/2) = -180 -> atan(w) + atan(w/2) = 90 deg -> w * (w/2) = 1 -> w = sqrt(2)
    # |L(j sqrt 2)| = 1/(sqrt2 * sqrt3 * sqrt6) = 1/6 -> GM = 6 (15.563 dB)
    r = ok(frequency, **loop(1))
    assert v(r, "gain_margin") == pytest.approx(6.0, rel=1e-9)
    assert v(r, "gain_margin_db") == pytest.approx(20 * math.log10(6), abs=1e-8)
    assert v(r, "phase_crossover_frequency") == pytest.approx(math.sqrt(2.0), rel=1e-9)
    # gain crossover: solved numerically in the test ; PM = 180 + (-90 - atan w - atan(w/2)) = 90 - atan w - atan(w/2)
    wg = solve_gain_crossover(1.0)
    assert wg == pytest.approx(0.4457, abs=1e-4)
    assert v(r, "gain_crossover_frequency") == pytest.approx(wg, rel=1e-9)
    pm = 90.0 - math.degrees(math.atan(wg) + math.atan(wg / 2))
    assert v(r, "phase_margin") == pytest.approx(pm, abs=1e-7)
    assert pm == pytest.approx(53.41, abs=0.01)
    assert v(r, "delay_margin") == pytest.approx(math.radians(pm) / wg, rel=1e-9)
    assert r.values["phase_margin"].unit == "degree" and r.values["gain_margin_db"].unit == "decibel"
    cl = r.data["closed_loop"]
    assert cl["verdict"] == "stable" and cl["margins_agree_with_pole_count"] is True and cl["open_loop_unstable_poles"] == 0
    assert r.data["dc_gain"] == "infinite" and r.data["integrators"] == 1


@pytest.mark.parametrize("k, verdict", [(3, "stable"), (6, "marginally stable"), (10, "unstable")])
def test_gain_margin_scales_as_six_over_k_and_the_pole_count_follows(k, verdict):
    # GM = 6/K ; closed loop s^3 + 3 s^2 + 2 s + K: Routh s^1 row (3*2 - K)/3 > 0 <=> K < 6
    r = ok(frequency, **loop(k))
    assert v(r, "gain_margin") == pytest.approx(6.0 / k, rel=1e-9)
    assert r.data["closed_loop"]["verdict"] == verdict
    assert v(r, "closed_loop_unstable_poles") == (2.0 if k == 10 else 0.0)  # K = 10: two roots in the right half plane
    if k != 6:  # at K = 6 the loop is exactly marginal and GM = 1 to rounding: a margin reader may go either way
        assert r.data["closed_loop"]["margins_agree_with_pole_count"] is True


def test_bode_table_by_independent_complex_arithmetic():
    r = ok(frequency, **loop(1), frequency_min=0.01, frequency_max=100.0, points=50)
    data = table(r, "bode.csv")
    assert list(data) == ["frequency_rad_s", "magnitude_db", "phase_deg"] and len(data["frequency_rad_s"]) == 50
    assert data["frequency_rad_s"][0] == pytest.approx(0.01) and data["frequency_rad_s"][-1] == pytest.approx(100.0)
    ratios = data["frequency_rad_s"][1:] / data["frequency_rad_s"][:-1]
    assert np.allclose(ratios, ratios[0])  # logarithmic grid
    for w, mag, ph in zip(data["frequency_rad_s"], data["magnitude_db"], data["phase_deg"]):
        value = 1.0 / (1j * w * (1j * w + 1) * (1j * w + 2))
        assert mag == pytest.approx(20 * math.log10(abs(value)), abs=1e-9)
        assert ph == pytest.approx(-90.0 - math.degrees(math.atan(w) + math.atan(w / 2)), abs=1e-9)  # unwrapped, not the principal value


def test_phase_is_anchored_for_repeated_integrators_and_unwrapped_through_a_resonance():
    # 1/s^3: -270 degrees at every frequency (the principal angle would say +90)
    r = ok(frequency, numerator="1", denominator="s^3", frequency_min=0.1, frequency_max=10.0, points=20)
    assert np.allclose(table(r, "bode.csv")["phase_deg"], -270.0, atol=1e-9)
    r = ok(frequency, numerator="1", denominator="s^2", frequency_min=0.1, frequency_max=10.0, points=20)
    assert np.allclose(table(r, "bode.csv")["phase_deg"], -180.0, atol=1e-9)
    # 1/(s^2 + 0.2 s + 1) then a lag: the phase falls monotonically from 0 to -270 across the resonance
    r = ok(frequency, numerator="1", denominator="(s^2 + 0.2 s + 1)(s + 1)", frequency_min=0.01, frequency_max=100.0, points=2000)
    ph = table(r, "bode.csv")["phase_deg"]
    assert ph[0] == pytest.approx(0.0, abs=1.5) and ph[-1] == pytest.approx(-270.0, abs=1.5) and np.all(np.diff(ph) < 1e-9)
    # negative DC gain -1/(s+1): phase starts at +180 - the tool's convention (arg of a negative real number is pi)
    r = ok(frequency, numerator="-1", denominator="[1, 1]", frequency_min=0.001, frequency_max=100.0, points=20)
    assert table(r, "bode.csv")["phase_deg"][0] == pytest.approx(180.0 - math.degrees(math.atan(0.001)), abs=1e-6)


def test_second_order_peak_bandwidth_and_resonance_by_hand():
    # wn = 4, zeta = 0.5.  |G(0)| = 1.  Resonant frequency wn sqrt(1 - 2 zeta^2) = 4 sqrt(0.5) = 2.828427 rad/s
    # resonant peak Mr = 1/(2 zeta sqrt(1 - zeta^2)) = 1.154701 = 1.249387 dB
    # -3 dB bandwidth wn sqrt(1 - 2 zeta^2 + sqrt(4 zeta^4 - 4 zeta^2 + 2)) = 4 sqrt(0.5 + sqrt(1.25)) = 5.088079 rad/s
    # |G| = 1 at w = wn (|G| = 1 where (16 - w^2)^2 + 16 w^2 = 256 -> w^4 = 16 w^2) with phase -90 -> PM = 90 degrees
    r = ok(frequency, numerator="16", denominator="[1, 4, 16]")
    assert v(r, "dc_gain") == pytest.approx(1.0) and v(r, "dc_gain_db") == pytest.approx(0.0, abs=1e-12)
    assert v(r, "resonant_frequency") == pytest.approx(4 * math.sqrt(0.5), rel=1e-7)
    assert v(r, "resonant_peak_db") == pytest.approx(20 * math.log10(1 / (2 * 0.5 * math.sqrt(0.75))), abs=1e-6)
    assert v(r, "bandwidth") == pytest.approx(4 * math.sqrt(0.5 + math.sqrt(1.25)), rel=1e-9)
    assert v(r, "gain_crossover_frequency") == pytest.approx(4.0, rel=1e-9) and v(r, "phase_margin") == pytest.approx(90.0, abs=1e-7)
    assert v(r, "peak_magnitude_db") == pytest.approx(v(r, "resonant_peak_db"), abs=0.02)  # the table's peak (grid, 2 % spacing)
    assert "gain_margin" not in r.values and "no_phase_crossover" not in r.data
    assert "never crosses" in r.data["phase_crossover_note"]


def test_first_order_bandwidth_and_no_resonance():
    # 1/(tau s + 1), tau = 0.5: bandwidth 1/tau = 2 rad/s
    r = ok(frequency, numerator="1", denominator="[0.5, 1]")
    assert v(r, "bandwidth") == pytest.approx(2.0, rel=1e-9)
    assert "resonant_peak_db" not in r.values and "no peak" in r.data["resonance_note"]


def test_conditionally_stable_loop_margins_mislead_and_the_pole_count_is_right():
    # L = 2 (s+3)^2 / s^3.  At w = 3: angle = 2*45 - 270 = -180 exactly, |L| = 2*18/27 = 4/3 -> GM = 0.75 (-2.499 dB): below 1
    # gain crossover (|L| = 1): 2 (w^2 + 9) = w^3 -> w = 3.4834 ; PM = 180 + 2 atan(w/3) - 270 = 8.53 degrees (positive)
    # closed loop s^3 + 2 s^2 + 12 s + 18: Routh column 1, 2, (2*12 - 18)/2 = 3, 18 -> all positive: STABLE.
    # A margin reader (GM < 1 means unstable) would say "unstable": the loop is stable for K > 1.5 only, lowering the gain destabilises it.
    r = ok(frequency, numerator="2 (s+3)^2", denominator="s^3")
    assert v(r, "gain_margin") == pytest.approx(0.75, rel=1e-9) and v(r, "phase_crossover_frequency") == pytest.approx(3.0, rel=1e-9)
    assert v(r, "phase_margin") == pytest.approx(180 + 2 * math.degrees(math.atan(3.4834 / 3)) - 270, abs=2e-3)
    cl = r.data["closed_loop"]
    assert cl["verdict"] == "stable" and cl["stability"]["method"]["first_column"] == [1.0, 2.0, 3.0, 18.0]
    assert cl["verdict_by_margins"] == "unstable" and cl["margins_agree_with_pole_count"] is False
    assert any("margins say 'unstable' but the closed loop is stable" in w for w in r.warnings)
    # the same loop with K = 1 (below 1.5): really unstable (Routh s^1 row (6K - 9)/K < 0): margins agree
    r = ok(frequency, numerator="(s+3)^2", denominator="s^3")
    assert r.data["closed_loop"]["verdict"] == "unstable" and r.data["closed_loop"]["margins_agree_with_pole_count"] is True


def test_open_loop_unstable_plant_margins_mislead_the_other_way():
    # L = 0.5/(s - 1): |L| <= 0.5 for all w, so there is NO gain crossover; Im L = 0 only at w = 0 (L = -0.5 there, not a crossing).
    # A margin reader finds nothing wrong ("infinite margins").  The closed loop s - 1 + 0.5 = s - 0.5 has a root at +0.5: UNSTABLE.
    r = ok(frequency, numerator="0.5", denominator="[1, -1]")
    cl = r.data["closed_loop"]
    assert cl["verdict"] == "unstable" and cl["open_loop_unstable_poles"] == 1 and cl["verdict_by_margins"] == "stable"
    assert cl["margins_agree_with_pole_count"] is False and any("margins say 'stable'" in w for w in r.warnings)
    # with K = 2: closed loop s + 1 stable ; |L| = 1 at w = sqrt(3), angle -180 + atan(sqrt 3) = -120 -> PM = 60: they agree
    r = ok(frequency, numerator="2", denominator="[1, -1]")
    assert r.data["closed_loop"]["verdict"] == "stable" and r.data["closed_loop"]["margins_agree_with_pole_count"] is True
    assert v(r, "gain_crossover_frequency") == pytest.approx(math.sqrt(3.0), rel=1e-9) and v(r, "phase_margin") == pytest.approx(60.0, abs=1e-7)


def test_closed_loop_check_can_be_switched_off():
    r = ok(frequency, **loop(1), closed_loop_check=False)
    assert "closed_loop" not in r.data and "closed_loop_unstable_poles" not in r.values


def test_discrete_integrator_loop_by_hand():
    # L(z) = 1/(z - 1), T = 0.5 s: |L| = 1/(2 sin(w T/2)) = 1 -> sin(w T/2) = 1/2 -> w T/2 = pi/6 -> w = 4 pi/6 = 2.094395 rad/s
    # angle of 1/(e^{j th} - 1) = -(th/2 + 90 deg) with th = w T = 60 deg -> -120 deg -> PM = 60 degrees.  Closed loop z - 1 + 1 = z: pole at 0.
    r = ok(frequency, numerator="1", denominator="[1, -1]", domain="discrete", sample_time="0.5 s")
    assert v(r, "gain_crossover_frequency") == pytest.approx(4 * math.pi / 6, rel=1e-9)
    assert v(r, "phase_margin") == pytest.approx(60.0, abs=1e-7)
    assert r.data["closed_loop"]["verdict"] == "stable" and r.data["nyquist_rad_s"] == pytest.approx(math.pi / 0.5)
    data = table(r, "bode.csv")
    assert data["frequency_rad_s"][-1] == pytest.approx(math.pi / 0.5)  # up to the Nyquist frequency
    for w, mag, ph in zip(data["frequency_rad_s"][::40], data["magnitude_db"][::40], data["phase_deg"][::40]):
        z = cmath.exp(1j * w * 0.5)
        assert mag == pytest.approx(20 * math.log10(abs(1 / (z - 1))), abs=1e-9)
        assert ph == pytest.approx(-(w * 0.5 / 2 * 180 / math.pi + 90.0), abs=1e-9)
    # first-order ZOH-like model 0.1/(z - 0.9), T = 0.1: at w = 5 rad/s z = e^{j 0.5}: |L| = 0.1/|z - 0.9|
    r = ok(frequency, numerator="0.1", denominator="[1, -0.9]", domain="discrete", sample_time="0.1 s", frequency_min=5.0, frequency_max=5.0000001, points=10)
    row = table(r, "bode.csv")
    z = cmath.exp(0.5j)
    assert row["magnitude_db"][0] == pytest.approx(20 * math.log10(0.1 / abs(z - 0.9)), abs=1e-6)
    assert v(r, "dc_gain") == pytest.approx(1.0, abs=1e-12)
    assert r.data["closed_loop"]["stability"]["method"]["jury_stable"] is True  # closed loop z - 0.8


def test_refusals():
    fails(frequency, "input.invalid", "numerator", numerator="[1, 0, 0]", denominator="[1, 1]")  # improper
    fails(frequency, "input.invalid", "denominator", numerator="1", denominator="[0, 1]")
    fails(frequency, "input.out_of_range", "points", numerator="1", denominator="[1, 1]", points=5)
    fails(frequency, "input.out_of_range", "points", numerator="1", denominator="[1, 1]", points=10**9)
    fails(frequency, "input.out_of_range", "frequency_max", numerator="1", denominator="[1, 1]", frequency_max=1e12)
    fails(frequency, "input.out_of_range", "frequency_min", numerator="1", denominator="[1, 1]", frequency_min=1e-12)
    fails(frequency, "input.invalid", "frequency_min", numerator="1", denominator="[1, 1]", frequency_min=float("nan"))
    fails(frequency, "input.invalid", "frequency_max", numerator="1", denominator="[1, 1]", frequency_max=float("inf"))
    fails(frequency, "input.invalid", "frequency_min", numerator="1", denominator="[1, 1]", frequency_min=10.0, frequency_max=1.0)
    fails(frequency, "input.out_of_range", "frequency_max", numerator="1", denominator="[1, -0.5]", domain="discrete", sample_time="1 s", frequency_max=10.0)  # above pi/T
    fails(frequency, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", sample_time="1 s")
