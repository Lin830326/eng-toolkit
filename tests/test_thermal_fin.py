"""thermal.fin checked against the closed-form fin solutions."""

import math

import pytest

from eng_toolkit.domains.thermal.fins import fin, fin_heat_rate
from tests.thermal_testing import fails, ok, v

# Aluminium-like pin fin: D = 5 mm, L = 50 mm, k = 200 W/mK, h = 25 W/m^2K, base 100 degC, ambient 25 degC (theta_b = 75 K)
#   P = pi D = 0.01570796 m ; Ac = pi D^2/4 = 1.963495e-5 m^2 ; P/Ac = 4/D = 800
#   m = sqrt(h P/(k Ac)) = sqrt(25*800/200) = sqrt(100) = 10 1/m  ->  mL = 0.5
#   sqrt(h P k Ac) = k Ac m = 200 * 1.963495e-5 * 10 = 0.03926991 W/K ;  M = 0.03926991 * 75 = 2.945243 W
PIN = dict(fin_type="pin", diameter="5 mm", length="50 mm", conductivity="200 W/(m*K)", h="25 W/(m^2*K)", base_temperature="100 degC", ambient_temperature="25 degC")
M = 2.945243
TANH_HALF = math.tanh(0.5)  # 0.4621172


def test_infinitely_long_fin_q_equals_sqrt_hpkac_theta():
    r = ok(fin, **PIN, tip="infinite")
    assert math.isclose(v(r, "heat_rate"), M, rel_tol=1e-6)
    assert math.isclose(v(r, "m"), 10.0, rel_tol=1e-12)
    assert math.isclose(v(r, "ml"), 0.5, rel_tol=1e-12)
    assert any("not effectively infinite" in w for w in r.warnings)  # mL = 0.5 < 2.65
    long = ok(fin, **{**PIN, "length": "1 m"}, tip="infinite")  # mL = 10: effectively infinite, no warning
    assert math.isclose(v(long, "heat_rate"), M, rel_tol=1e-6)
    assert not any("not effectively infinite" in w for w in long.warnings)


def test_adiabatic_tip_q_equals_m_tanh_ml_and_efficiency_tanh_ml_over_ml():
    # q = 2.945243 * tanh(0.5) = 2.945243 * 0.4621172 = 1.361047 W ; eta = tanh(0.5)/0.5 = 0.9242343
    # effectiveness = q/(h Ac theta_b) = 1.361047/(25 * 1.963495e-5 * 75) = 1.361047/0.03681553 = 36.9694
    r = ok(fin, **PIN)  # adiabatic is the default
    assert math.isclose(v(r, "heat_rate"), 1.361047, rel_tol=1e-6)
    assert math.isclose(v(r, "efficiency"), 0.9242343, rel_tol=1e-6)
    assert math.isclose(v(r, "efficiency"), TANH_HALF / 0.5, rel_tol=1e-12)
    assert math.isclose(v(r, "effectiveness"), 36.9694, rel_tol=1e-5)
    assert math.isclose(r.data["efficiency_check_tanh_ml_over_ml"], v(r, "efficiency"), rel_tol=1e-12)
    # efficiency from the definition q/(h P L theta_b): 1.361047/(25 * 0.01570796 * 0.05 * 75) = 1.361047/1.472621
    assert math.isclose(v(r, "heat_rate") / (25 * math.pi * 0.005 * 0.05 * 75), v(r, "efficiency"), rel_tol=1e-12)


def test_convective_tip_and_corrected_length_equivalence():
    # beta = h/(mk) = 25/(10*200) = 0.0125 ; q = M (tanh mL + beta)/(1 + beta tanh mL) = 2.945243 * 0.4746172/1.005777 = 1.389835 W
    r = ok(fin, **PIN, tip="convective")
    assert math.isclose(v(r, "heat_rate"), 1.389835, rel_tol=1e-6)
    # corrected length Lc = L + Ac/P = 0.05 + D/4 = 0.05125 m: adiabatic-tip formula on Lc gives 2.945243 tanh(0.5125) = 1.389833 W
    assert math.isclose(r.data["corrected_length_m"], 0.05125, rel_tol=1e-12)
    assert math.isclose(r.data["corrected_length_heat_rate_W"], 2.945243 * math.tanh(0.5125), rel_tol=1e-6)
    assert r.data["corrected_length_relative_difference"] < 1e-5
    # the convective tip carries more heat than the adiabatic tip and its area includes the tip
    assert v(r, "heat_rate") > 1.361047
    assert math.isclose(v(r, "fin_surface_area"), 0.01570796 * 0.05 + 1.963495e-5, rel_tol=1e-6)


def test_convective_tip_limits():
    # h -> 0 at the tip is adiabatic; and a tip with h/(mk) = 1 (beta=1) gives q = M exactly (any mL): (t + 1)/(1 + t) = 1
    assert math.isclose(fin_heat_rate("convective", 10.0, 0.05, 200.0, 25.0, 0.01570796, 1.963495e-5, 75.0), 1.389835, rel_tol=1e-6)
    m, k = 10.0, 200.0
    # choose h = m k = 2000 so beta = 1 with the same m: P/Ac fixed by m^2 = hP/(kAc)
    p, ac, h = 0.02, 0.02 * 2000.0 / (200.0 * 100.0), 2000.0  # m^2 = h P/(k Ac) = 2000*0.02/(200*0.002) = 100
    assert math.isclose(math.sqrt(h * p / (k * ac)), m, rel_tol=1e-12)
    q1 = fin_heat_rate("convective", m, 0.07, k, h, p, ac, 10.0)
    assert math.isclose(q1, math.sqrt(h * p * k * ac) * 10.0, rel_tol=1e-12)


def test_prescribed_tip_temperature():
    # theta_L/theta_b = 0.5: q = M (cosh 0.5 - 0.5)/sinh 0.5 = 2.945243 * (1.127626 - 0.5)/0.521095 = 3.547357 W
    r = ok(fin, **PIN, tip="prescribed_temperature", tip_temperature=f"{25 + 37.5} degC")
    assert math.isclose(v(r, "heat_rate"), 3.547357, rel_tol=1e-6)
    # the tip at the adiabatic-fin tip temperature theta_b/cosh(mL) reproduces the adiabatic result:
    #   (cosh x - 1/cosh x)/sinh x = tanh x
    tip = 25 + 75 / math.cosh(0.5)
    same = ok(fin, **PIN, tip="prescribed_temperature", tip_temperature=f"{tip!r} degC")
    assert math.isclose(v(same, "heat_rate"), v(ok(fin, **PIN), "heat_rate"), rel_tol=1e-9)
    fails(fin, "input.missing", "tip_temperature", **PIN, tip="prescribed_temperature")
    odd = ok(fin, **PIN, tip="prescribed_temperature", tip_temperature="200 degC")
    assert any("not between ambient and base" in w for w in odd.warnings)


def test_no_overflow_for_a_very_long_fin():
    # mL = 10 * 1000 would overflow sinh/cosh; the tanh forms stay finite
    r = ok(fin, **{**PIN, "length": "10 m"}, tip="prescribed_temperature", tip_temperature="30 degC")
    assert math.isclose(v(r, "heat_rate"), M, rel_tol=1e-6)  # coth -> 1 and the tip term vanishes
    r2 = ok(fin, **{**PIN, "length": "10 m"}, tip="convective")
    assert math.isclose(v(r2, "heat_rate"), M, rel_tol=1e-6)


def test_rectangular_fin_geometry_and_heat_rate():
    # t = 3 mm, w = 100 mm: P = 2 (0.1 + 0.003) = 0.206 m, Ac = 3e-4 m^2, m = sqrt(25*0.206/(200*3e-4)) = sqrt(85.8333) = 9.264628
    # q (adiabatic, L = 30 mm) = sqrt(25*0.206*200*3e-4) * 75 * tanh(0.2779388) = 0.5559 * 75 * 0.2710... computed below
    r = ok(fin, fin_type="rectangular", thickness="3 mm", width="100 mm", length="30 mm", conductivity="200 W/(m*K)", h="25 W/(m^2*K)", base_temperature="100 degC", ambient_temperature="25 degC")
    m = math.sqrt(25 * 0.206 / (200 * 3e-4))
    assert math.isclose(v(r, "m"), m, rel_tol=1e-12) and math.isclose(m, 9.264628, rel_tol=1e-6)
    q = math.sqrt(25 * 0.206 * 200 * 3e-4) * 75 * math.tanh(m * 0.03)
    assert math.isclose(v(r, "heat_rate"), q, rel_tol=1e-12)
    assert math.isclose(v(r, "biot_fin"), 25 * (1.5e-3) / 200, rel_tol=1e-12)


def test_sign_when_the_base_is_colder_than_the_fluid():
    r = ok(fin, **{**PIN, "base_temperature": "25 degC", "ambient_temperature": "100 degC"})
    assert math.isclose(v(r, "heat_rate"), -1.361047, rel_tol=1e-6)
    assert math.isclose(v(r, "efficiency"), 0.9242343, rel_tol=1e-6)


def test_fin_array_overall_surface_efficiency():
    # N = 10 adiabatic-tip pins on a base with 0.002 m^2 unfinned: A_f = P L = 7.853982e-4 each, A_t = 10 A_f + 0.002 = 9.853982e-3
    #   eta_o = 1 - (10 A_f/A_t)(1 - eta_f) = 1 - 0.797037 * 0.0757657 = 0.939612
    #   q_t = eta_o h A_t theta_b = 0.939612 * 25 * 9.853982e-3 * 75 = 17.36047 W = N q_f + h A_b theta_b = 13.61047 + 3.75
    r = ok(fin, **PIN, fin_count=10, base_unfinned_area="0.002 m^2")
    assert math.isclose(v(r, "array_overall_surface_efficiency"), 0.939612, rel_tol=1e-6)
    assert math.isclose(v(r, "array_total_area"), 9.853982e-3, rel_tol=1e-6)
    assert math.isclose(v(r, "array_heat_rate"), 17.36047, rel_tol=1e-6)
    assert math.isclose(v(r, "array_heat_rate"), 10 * v(r, "heat_rate") + 25 * 0.002 * 75, rel_tol=1e-9)
    assert r.data["array"]["relative_difference"] < 1e-12
    # no base area: eta_o equals eta_f
    only = ok(fin, **PIN, fin_count=4)
    assert math.isclose(v(only, "array_overall_surface_efficiency"), v(only, "efficiency"), rel_tol=1e-12)
    assert math.isclose(v(only, "array_heat_rate"), 4 * v(only, "heat_rate"), rel_tol=1e-12)


def test_imperial_units():
    metric = ok(fin, **PIN)
    imperial = ok(
        fin,
        fin_type="pin", diameter="0.19685039 inch", length="1.9685039 inch", conductivity="115.5779 Btu/(hour*foot*degF)",
        h="4.40295 Btu/(hour*foot^2*degF)", base_temperature="212 degF", ambient_temperature="77 degF", unit_system="imperial",
    )
    assert math.isclose(v(imperial, "efficiency"), v(metric, "efficiency"), rel_tol=1e-4)
    assert math.isclose(v(imperial, "heat_rate"), v(metric, "heat_rate") * 3.412141156, rel_tol=1e-4)


@pytest.mark.parametrize(
    "overrides, family, param",
    [
        ({"length": "0 mm"}, "input.invalid", "length"),
        ({"length": "-5 mm"}, "input.invalid", "length"),
        ({"length": "1e308 m"}, "input.out_of_range", "length"),
        ({"conductivity": "-1 W/(m*K)"}, "input.invalid", "conductivity"),
        ({"conductivity": "200 W/m"}, "units.dimension_mismatch", "conductivity"),
        ({"h": "0 W/(m^2*K)"}, "input.invalid", "h"),
        ({"h": "25"}, "units.bare_number", "h"),
        ({"diameter": ""}, "input.missing", "diameter"),
        ({"diameter": "0 mm"}, "input.invalid", "diameter"),
        ({"base_temperature": "25 degC"}, "input.invalid", "base_temperature|ambient_temperature"),
        ({"base_temperature": "75 delta_degC"}, "input.invalid", "base_temperature"),
        ({"ambient_temperature": "-300 degC"}, "input.invalid", "ambient_temperature"),
        ({"tip": "free"}, "input.invalid", "tip"),
        ({"fin_type": "annular"}, "input.invalid", "fin_type"),
        ({"fin_count": 0, "base_unfinned_area": "0.1 m^2"}, "input.invalid", "fin_count"),
        ({"fin_count": 3, "base_unfinned_area": "-1 m^2"}, "input.invalid", "base_unfinned_area"),
    ],
)
def test_refused_inputs(overrides, family, param):
    fails(fin, family, param, **{**PIN, **overrides})


def test_rectangular_needs_thickness_and_width():
    fails(fin, "input.missing", "thickness|width", fin_type="rectangular", length="30 mm", conductivity="200 W/(m*K)", h="25 W/(m^2*K)", base_temperature="100 degC", ambient_temperature="25 degC", width="100 mm")


def test_biot_and_effectiveness_warnings():
    thick = ok(fin, **{**PIN, "diameter": "100 mm", "conductivity": "0.5 W/(m*K)", "h": "100 W/(m^2*K)"})
    assert any("Biot" in w for w in thick.warnings)
    poor = ok(fin, **{**PIN, "conductivity": "0.05 W/(m*K)"})
    assert any("effectiveness" in w and "below 2" in w for w in poor.warnings)
