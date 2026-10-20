"""control_analyze checked against hand calculations."""

import math

import pytest

from eng_toolkit.domains.control.analyze import analyze
from tests.control_testing import fails, ok, v


def poles(result):
    return result.data["poles"]


def test_two_real_poles_by_hand():
    # G = 1/(s^2 + 3 s + 2) = 1/((s+1)(s+2)): poles -1, -2 ; DC gain 1/2 ; Routh s^2 [1, 2], s^1 [3], s^0 [(3*2 - 1*0)/3 = 2]
    r = ok(analyze, numerator="[1]", denominator="s^2 + 3 s + 2")
    assert [p["real"] for p in poles(r)] == pytest.approx([-1.0, -2.0], abs=1e-12)
    assert [p["natural_frequency"] for p in poles(r)] == pytest.approx([1.0, 2.0], abs=1e-12)
    assert [p["damping_ratio"] for p in poles(r)] == pytest.approx([1.0, 1.0], abs=1e-12)
    assert [p["time_constant"] for p in poles(r)] == pytest.approx([1.0, 0.5], abs=1e-12)
    assert v(r, "dc_gain") == pytest.approx(0.5, abs=1e-15)
    assert v(r, "order") == 2 and v(r, "relative_degree") == 2 and v(r, "unstable_poles") == 0
    assert v(r, "dominant_natural_frequency") == pytest.approx(1.0, abs=1e-12)  # the slow pole at -1 dominates
    assert v(r, "dominant_time_constant") == pytest.approx(1.0, abs=1e-12)
    assert r.values["dominant_natural_frequency"].unit == "radian / second" and r.values["dominant_time_constant"].unit == "second"
    st = r.data["stability"]
    assert st["verdict"] == "stable" and st["roots_agree"] is True
    assert st["method"]["first_column"] == [1.0, 3.0, 2.0]
    assert st["method"]["array"] == [[1.0, 2.0], [3.0, 0.0], [2.0, 0.0]]
    assert st["method"]["array_agrees_with_cauchy_count"] is True
    assert r.data["minimum_phase"]["minimum_phase"] is True and r.data["system_type"] == 0


def test_underdamped_second_order_wn4_zeta_half():
    # s^2 + 4 s + 16: wn^2 = 16 -> 4 rad/s ; 2 zeta wn = 4 -> zeta = 0.5 ; poles -2 +- j sqrt(16 - 4) = -2 +- j 3.464102 ; tau = 1/2 s
    r = ok(analyze, numerator="16", denominator="[1, 4, 16]")
    p = poles(r)
    assert p[0]["real"] == pytest.approx(-2.0, abs=1e-12) and abs(p[0]["imag"]) == pytest.approx(math.sqrt(12.0), abs=1e-12)
    assert p[0]["natural_frequency"] == pytest.approx(4.0, abs=1e-12)
    assert p[0]["damping_ratio"] == pytest.approx(0.5, abs=1e-12)
    assert p[0]["time_constant"] == pytest.approx(0.5, abs=1e-12)
    assert v(r, "dc_gain") == pytest.approx(1.0, abs=1e-14)
    assert v(r, "dominant_damping_ratio") == pytest.approx(0.5, abs=1e-12)
    assert len(r.data["dominant_pole"]["roots"]) == 2  # the conjugate pair


def test_unstable_cubic_counts_the_right_half_plane_root_exactly():
    # s^3 + 4 s^2 + s - 6 = (s-1)(s+2)(s+3): Routh column 1, 4, (4*1 - 1*(-6))/4 = 2.5, -6 -> one sign change
    r = ok(analyze, numerator="1", denominator="s^3 + 4 s^2 + s - 6")
    st = r.data["stability"]
    assert st["verdict"] == "unstable" and st["right_half_plane"] == 1 and st["left_half_plane"] == 2
    assert st["method"]["first_column"] == [1.0, 4.0, 2.5, -6.0]
    assert v(r, "unstable_poles") == 1
    assert sorted(p["real"] for p in poles(r)) == pytest.approx([-3.0, -2.0, 1.0], abs=1e-10)
    assert v(r, "dc_gain") == pytest.approx(-1.0 / 6.0, rel=1e-14)
    assert [p["stable"] for p in poles(r)] == [False, True, True]


def test_marginal_and_repeated_axis_roots():
    # 1/(s^2+1): poles +-j, zero row in the Routh array -> two roots on the axis, simple -> marginally stable
    r = ok(analyze, numerator="1", denominator="[1, 0, 1]")
    st = r.data["stability"]
    assert st["verdict"] == "marginally stable" and st["on_imaginary_axis"] == 2 and st["right_half_plane"] == 0
    assert v(r, "dc_gain") == 1.0
    # 1/(s^2+1)^2: the same roots twice -> the response grows like t: unstable
    r = ok(analyze, numerator="1", denominator="(s^2 + 1)^2")
    assert r.data["stability"]["verdict"] == "unstable" and r.data["stability"]["on_imaginary_axis"] == 4
    # an integrator: marginally stable, DC gain infinite, system type 1
    r = ok(analyze, numerator="1", denominator="s (s + 1)")
    assert r.data["stability"]["verdict"] == "marginally stable"
    assert r.data["dc_gain"] == "infinite" and "dc_gain" not in r.values and r.data["system_type"] == 1


def test_zero_at_the_origin_gives_zero_dc_gain_and_cancelling_integrator_gives_finite():
    r = ok(analyze, numerator="s", denominator="s^2 + 3 s + 2")
    assert v(r, "dc_gain") == 0.0
    r = ok(analyze, numerator="s + 3", denominator="s (s + 1)")  # one zero away from the origin; integrator remains
    assert r.data["dc_gain"] == "infinite"
    r = ok(analyze, numerator="[2, 0]", denominator="[1, 0, 4, 0]")  # 2 s / (s^3 + 4 s) -> the common s cancels in the limit: 2/4
    assert v(r, "dc_gain") == pytest.approx(0.5)


def test_nonminimum_phase_zero():
    # (1 - s)/(s^2 + 3 s + 2): zero at +1 -> not minimum phase
    r = ok(analyze, numerator="1 - s", denominator="s^2 + 3 s + 2")
    assert r.data["minimum_phase"]["minimum_phase"] is False and r.data["minimum_phase"]["zeros_outside_stable_region"] == 1
    assert r.data["transfer_function"]["numerator"]["coefficients_descending"] == [-1.0, 1.0]
    assert v(r, "dc_gain") == pytest.approx(0.5)
    r = ok(analyze, numerator="s + 1", denominator="s^2 + 3 s + 2")  # zero at -1: minimum phase (and it cancels a pole)
    assert r.data["minimum_phase"]["minimum_phase"] is True


def test_discrete_model_by_hand():
    # G(z) = (0.5 z + 0.2)/(z^2 - 1.2 z + 0.35): poles 0.7 and 0.5 ; G(1) = 0.7/0.15 = 4.666667
    # time constant of the pole 0.7 with T = 0.1 s: -T/ln(0.7) = 0.1/0.3566749 = 0.280367 s
    r = ok(analyze, numerator="[0.5, 0.2]", denominator="[1, -1.2, 0.35]", domain="discrete", sample_time="0.1 s")
    assert r.data["stability"]["verdict"] == "stable" and r.data["stability"]["method"]["jury_stable"] is True
    assert v(r, "dc_gain") == pytest.approx(0.7 / 0.15, rel=1e-13)
    assert v(r, "dominant_time_constant") == pytest.approx(-0.1 / math.log(0.7), rel=1e-12)
    assert [p["magnitude"] for p in poles(r)] == pytest.approx([0.7, 0.5], abs=1e-12)
    assert poles(r)[0]["equivalent_s"]["real"] == pytest.approx(math.log(0.7) / 0.1, rel=1e-12)
    assert r.data["minimum_phase"]["minimum_phase"] is True  # zero at -0.4
    unstable = ok(analyze, numerator="1", denominator="[1, -1.5]", domain="discrete", sample_time="0.1 s")
    assert unstable.data["stability"]["verdict"] == "unstable" and unstable.data["stability"]["outside"] == 1
    delay = ok(analyze, numerator="1", denominator="[1, 0]", domain="discrete", sample_time="1 s")  # a pure one-sample delay: pole at z = 0
    assert delay.data["stability"]["verdict"] == "stable" and poles(delay)[0]["damping_ratio"] is None
    integrator = ok(analyze, numerator="1", denominator="[1, -1]", domain="discrete", sample_time="1 s")
    assert integrator.data["stability"]["verdict"] == "marginally stable" and integrator.data["dc_gain"] == "infinite"


def test_discrete_text_uses_z():
    r = ok(analyze, numerator="0.5 z + 0.2", denominator="z^2 - 1.2 z + 0.35", domain="discrete", sample_time="0.1 s")
    assert r.data["transfer_function"]["denominator"]["coefficients_descending"] == pytest.approx([1.0, -1.2, 0.35])


def test_ill_conditioning_is_reported_not_hidden():
    # a pole at -1 and a zero at -1.0001: an (almost) cancelled mode
    r = ok(analyze, numerator="s + 1.0001", denominator="(s + 1)(s + 2)")
    cancel = r.data["numerical_checks"]["near_pole_zero_cancellations"]
    assert len(cancel) == 1 and cancel[0]["distance"] == pytest.approx(1e-4, rel=1e-3)
    assert any("pole-zero cancellation" in w for w in r.warnings)
    # an exactly cancelled unstable pole: the transfer function looks stable but the loop would be internally unstable
    r = ok(analyze, numerator="s - 1", denominator="(s - 1)(s + 2)")
    assert any("unstable or marginal" in w for w in r.warnings)
    assert r.data["stability"]["verdict"] == "unstable"  # judged on the denominator as given
    # a triple root
    r = ok(analyze, numerator="1", denominator="(s + 1)^3")
    assert r.data["numerical_checks"]["close_root_pairs"] >= 1 and any("repeated or clustered" in w for w in r.warnings)
    # a huge coefficient range
    r = ok(analyze, numerator="1", denominator="[1e-12, 1, 1e12]")
    assert r.data["numerical_checks"]["coefficient_range_ratio"]["denominator"] == pytest.approx(1e24, rel=1e-9)
    assert any("span a ratio" in w for w in r.warnings)


def test_improper_transfer_function_is_analysed_with_a_warning():
    r = ok(analyze, numerator="s^2", denominator="s + 1")
    assert v(r, "relative_degree") == -1 and any("improper" in w for w in r.warnings)


def test_order_forty_is_accepted_and_agreement_flag_is_honest():
    den = "[" + ",".join(["1"] + ["1"] * 40) + "]"  # 1 + s + ... + s^40: all roots on |s| = 1 (not on the axis), order 40
    r = ok(analyze, numerator="1", denominator=den)
    assert v(r, "order") == 40
    assert r.data["stability"]["method"]["name"].startswith("Routh")
    assert r.data["stability"]["right_half_plane"] + r.data["stability"]["left_half_plane"] + r.data["stability"]["on_imaginary_axis"] == 40
    assert any("above about order 15" in w for w in r.warnings)


def test_refusals():
    fails(analyze, "input.invalid", "denominator", numerator="1", denominator="[0, 1, 2]")  # leading zero
    fails(analyze, "input.invalid", "numerator", numerator="[0, 0]", denominator="[1, 2]")  # zero numerator
    fails(analyze, "input.invalid", "denominator", numerator="1", denominator="[]")
    fails(analyze, "input.invalid", "denominator", numerator="1", denominator="[1, 'a']")
    fails(analyze, "input.invalid", "denominator", numerator="1", denominator="[1, NaN]")
    fails(analyze, "input.out_of_range", "denominator", numerator="1", denominator="[1, 1e308]")
    fails(analyze, "input.out_of_range", "denominator", numerator="1", denominator="[" + ",".join(["1"] * 42) + "]")
    fails(analyze, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", domain="continuous", sample_time="0.1 s")
    fails(analyze, "input.missing", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete")
    fails(analyze, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete", sample_time="-0.1 s")
    fails(analyze, "input.invalid", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete", sample_time="0 s")
    fails(analyze, "units.bare_number", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete", sample_time="0.1")
    fails(analyze, "units.dimension_mismatch", "sample_time", numerator="1", denominator="[1, 1]", domain="discrete", sample_time="0.1 m")
    fails(analyze, "input.invalid", "domain", numerator="1", denominator="[1, 1]", domain="laplace")
    fails(analyze, "input.invalid", "denominator", numerator="1", denominator="__import__('os').system('x')")
    fails(analyze, "input.out_of_range", "denominator", numerator="1", denominator="s" * 100_000)
