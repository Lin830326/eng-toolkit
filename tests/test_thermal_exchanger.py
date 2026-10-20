"""thermal.exchanger checked against the textbook NTU relations and hand calculations."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.thermal.exchanger import bowman_f, effectiveness, eps_limit, exchanger, lmtd, ntu_for
from eng_toolkit.units import UnitsError
from tests.thermal_testing import fails, ok, v

KINDS = ("parallel", "counter", "cross_unmixed", "cross_cmax_mixed", "cross_cmin_mixed", "shell_tube")


def base(hot_c: float, cold_c: float, **extra):
    """Hot 100 degC, cold 20 degC; capacity rates given directly through 1 kg/s x cp (J/(kg K)) = C W/K."""
    return dict(
        hot_inlet_temperature="100 degC",
        cold_inlet_temperature="20 degC",
        hot_mass_flow="1 kg/s",
        hot_cp=f"{hot_c} J/(kg*K)",
        cold_mass_flow="1 kg/s",
        cold_cp=f"{cold_c} J/(kg*K)",
        **extra,
    )


# ---------------------------------------------------------------------------------------------------- relations
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("ntu", [0.1, 1.0, 3.0, 10.0])
def test_cr_zero_gives_one_minus_exp_minus_ntu_for_every_arrangement(kind, ntu):
    # Cr = 0: e = 1 - exp(-NTU).  NTU = 1: 1 - 0.367879 = 0.632121 ; NTU = 3: 0.950213 ; NTU = 0.1: 0.0951626
    assert math.isclose(effectiveness(kind, ntu, 0.0), 1.0 - math.exp(-ntu), rel_tol=1e-13)
    # and the limit Cr -> 0 is continuous
    assert math.isclose(effectiveness(kind, ntu, 1e-9), 1.0 - math.exp(-ntu), rel_tol=1e-6)


@pytest.mark.parametrize("ntu", [0.25, 1.0, 2.0, 7.0])
def test_counterflow_cr_one_is_ntu_over_one_plus_ntu(ntu):
    # NTU = 2: 2/3 ; NTU = 1: 1/2 ; NTU = 0.25: 0.25/1.25 = 0.2
    assert math.isclose(effectiveness("counter", ntu, 1.0), ntu / (1.0 + ntu), rel_tol=1e-14)
    # the general formula, evaluated one ulp-ish away from Cr = 1, agrees (no 0/0 blow-up near Cr = 1)
    assert math.isclose(effectiveness("counter", ntu, 1.0 - 1e-12), ntu / (1.0 + ntu), rel_tol=1e-9)


@pytest.mark.parametrize("cr", [0.2, 0.5, 1.0])
@pytest.mark.parametrize("ntu", [0.5, 2.0])
def test_parallel_flow_formula(cr, ntu):
    # e = (1 - exp(-NTU (1 + Cr))) / (1 + Cr): Cr = 0.5, NTU = 1.5 -> (1 - exp(-2.25))/1.5 = 0.894601/1.5 = 0.596401
    assert math.isclose(effectiveness("parallel", ntu, cr), (1.0 - math.exp(-ntu * (1.0 + cr))) / (1.0 + cr), rel_tol=1e-13)
    assert math.isclose(effectiveness("parallel", 1.5, 0.5), 0.596401, rel_tol=1e-6)
    assert math.isclose(eps_limit("parallel", cr), 1.0 / (1.0 + cr), rel_tol=1e-15)


def test_counterflow_general_formula():
    # Cr = 0.5, NTU = 1.5: exp(-0.75) = 0.4723666 ; e = (1 - 0.4723666)/(1 - 0.5*0.4723666) = 0.5276334/0.7638167 = 0.690785
    assert math.isclose(effectiveness("counter", 1.5, 0.5), 0.690785, rel_tol=1e-6)


def test_shell_and_tube_formula():
    # Cr = 0.5, NTU = 1: w = sqrt(1.25) = 1.118034 ; z = w/2 = 0.559017 ; tanh z = 0.507250 -> coth = 1.971 ;
    #   e = 2/(1.5 + 1.118034*1.971) = 2/3.704 = 0.53994
    assert math.isclose(effectiveness("shell_tube", 1.0, 0.5), 0.539940, rel_tol=1e-5)
    # limit NTU -> infinity: 2/(1 + Cr + sqrt(1 + Cr^2)) = 2/(1.5 + 1.118034) = 0.763932
    assert math.isclose(eps_limit("shell_tube", 0.5), 2.0 / (1.5 + math.sqrt(1.25)), rel_tol=1e-14)
    assert math.isclose(effectiveness("shell_tube", 400.0, 0.5), eps_limit("shell_tube", 0.5), rel_tol=1e-12)


def test_crossflow_one_fluid_mixed_formulas():
    # Cr = 0.5, NTU = 1.   Cmax mixed: (1 - exp(-0.5 (1 - e^-1)))/0.5 = (1 - exp(-0.316060))/0.5 = 0.271 / 0.5 = 0.541969
    #                      Cmin mixed: 1 - exp(-(1 - exp(-0.5))/0.5) = 1 - exp(-0.786939) = 0.544764
    assert math.isclose(effectiveness("cross_cmax_mixed", 1.0, 0.5), 0.541969, rel_tol=1e-6)
    assert math.isclose(effectiveness("cross_cmin_mixed", 1.0, 0.5), 0.544764, rel_tol=1e-6)


def test_crossflow_both_unmixed_series_hand_value():
    # NTU = 1, Cr = 1: e = (1/1) sum_n P(n,1)^2 with the Poisson tails P(n,1) =
    #   0.632121, 0.264241, 0.080301, 0.018988, 0.003660, 0.000594, 0.0000832 -> squares sum to
    #   0.399577 + 0.069820 + 0.006448 + 0.000361 + 0.0000134 + 0.00000035 = 0.476220
    assert math.isclose(effectiveness("cross_unmixed", 1.0, 1.0), 0.476222, abs_tol=2e-6)


def _cell_model(n: int, ntu: float, cr: float) -> float:
    """Independent check: N x N cells (hot stream = Cmin, cap 1; cold cap 1/Cr), each a mixed-mixed element; first-order in 1/N."""
    c1, c2 = 1.0 / n, 1.0 / (cr * n)
    conductance = 1.0 / (1.0 / c1 + 1.0 / c2 + n * n / ntu)
    cold = np.zeros(n)
    removed = 0.0
    for _ in range(n):
        hot = 1.0
        for j in range(n):
            q = (hot - cold[j]) * conductance
            hot -= q / c1
            cold[j] += q / c2
        removed += 1.0 - hot
    return removed / n


@pytest.mark.parametrize("ntu, cr", [(1.0, 1.0), (2.0, 0.5), (3.0, 0.8)])
def test_crossflow_both_unmixed_against_a_numerical_cell_model(ntu, cr):
    richardson = 2.0 * _cell_model(200, ntu, cr) - _cell_model(100, ntu, cr)
    assert math.isclose(effectiveness("cross_unmixed", ntu, cr), richardson, abs_tol=1e-4)
    # the common closed-form approximation 1 - exp[(NTU^0.22/Cr)(exp(-Cr NTU^0.78) - 1)] is within 2 % of the series
    approx = 1.0 - math.exp((ntu**0.22 / cr) * (math.exp(-cr * ntu**0.78) - 1.0))
    assert abs(approx - effectiveness("cross_unmixed", ntu, cr)) < 0.02


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("cr", [0.0, 0.3, 1.0])
def test_inverse_relations_round_trip(kind, cr):
    for ntu in (0.05, 0.8, 2.5, 6.0):
        eps = effectiveness(kind, ntu, cr)
        assert math.isclose(ntu_for(kind, eps, cr), ntu, rel_tol=1e-9)


def test_inverse_beyond_the_limit_raises():
    from eng_toolkit.domains.thermal.exchanger import Infeasible

    with pytest.raises(Infeasible):
        ntu_for("parallel", 0.6, 1.0)  # parallel flow with Cr = 1 cannot exceed 0.5
    with pytest.raises(UnitsError):
        ntu_for("cross_unmixed", 1.0 - 1e-13, 0.5)  # needs NTU above the 100 the series covers


def test_lmtd_function_handles_the_equal_difference_limit():
    # (80 - 20)/ln(80/20) = 60/1.386294 = 43.2808 ; equal differences -> the difference itself
    assert math.isclose(lmtd(80.0, 20.0), 60.0 / math.log(4.0), rel_tol=1e-14)
    assert lmtd(30.0, 30.0) == 30.0
    assert math.isclose(lmtd(30.0, 30.0 * (1 + 1e-9)), 30.0, rel_tol=1e-8)
    assert math.isclose(lmtd(30.0, 30.0 * (1 + 1e-5)), 30.0 * (1 + 5e-6), rel_tol=1e-9)


def test_bowman_correction_factor_hand_values():
    # R = 1, P = 0.5: F = [sqrt2 P/(1-P)] / ln{[2 - P(2 - sqrt2)]/[2 - P(2 + sqrt2)]} = 1.414214/ln(1.707107/0.292893) = 1.414214/1.762747 = 0.80228
    assert math.isclose(bowman_f(0.5, 1.0), 0.802278, abs_tol=2e-6)
    assert math.isclose(bowman_f(0.5, 1.0 + 1e-7), bowman_f(0.5, 1.0), rel_tol=1e-6)
    assert bowman_f(1e-9, 0.7) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------------------------------- tool: rating
def test_counterflow_equal_capacity_rates_rating():
    # C = 1000 W/K each, UA = 2000 W/K -> NTU = 2, Cr = 1, e = 2/3 ; Q = (2/3) 1000 (100 - 20) = 53 333.3 W
    # Th,out = 100 - 53.333 = 46.667 ; Tc,out = 20 + 53.333 = 73.333 ; both end differences are 26.667 K, so LMTD = 26.667 and Q = UA LMTD = 53 333.3
    r = ok(exchanger, arrangement="counterflow", **base(1000, 1000, ua="2000 W/K"))
    assert math.isclose(v(r, "ntu"), 2.0, rel_tol=1e-12)
    assert math.isclose(v(r, "effectiveness"), 2.0 / 3.0, rel_tol=1e-12)
    assert math.isclose(v(r, "duty"), 160000.0 / 3.0, rel_tol=1e-12)
    assert math.isclose(v(r, "hot_outlet_temperature"), 46.666667, abs_tol=1e-5)
    assert math.isclose(v(r, "cold_outlet_temperature"), 73.333333, abs_tol=1e-5)
    assert math.isclose(v(r, "lmtd"), 26.666667, abs_tol=1e-5)
    assert math.isclose(v(r, "lmtd") * 2000.0, v(r, "duty"), rel_tol=1e-9)
    assert any("temperature cross" in w for w in r.warnings)  # cold outlet above hot outlet: fine in counterflow, but flagged
    assert v(r, "lmtd_correction_factor") == 1.0


def test_parallel_flow_rating_and_lmtd_route():
    # Hot C = 2000 (2 kg/s x 1000), cold C = 1000, UA = 1500: Cmin = 1000, Cr = 0.5, NTU = 1.5, e = 0.596401
    #   Q = 0.596401 * 1000 * 80 = 47 712.04 W ; Th,out = 100 - 47712.04/2000 = 76.14398 ; Tc,out = 20 + 47.71204 = 67.71204
    #   dT1 = 80, dT2 = 76.14398 - 67.71204 = 8.43194 ; LMTD = (80 - 8.43194)/ln(80/8.43194) = 71.56806/2.249990 = 31.80803 ; UA LMTD = 47 712.0
    args = base(2000, 1000, ua="1500 W/K")
    r = ok(exchanger, arrangement="parallel", **args)
    assert math.isclose(v(r, "effectiveness"), 0.596401, rel_tol=1e-6)
    assert math.isclose(v(r, "duty"), 47712.04, rel_tol=1e-7)
    assert math.isclose(v(r, "hot_outlet_temperature"), 76.14398, abs_tol=1e-5)
    assert math.isclose(v(r, "cold_outlet_temperature"), 67.71204, abs_tol=1e-5)
    assert math.isclose(v(r, "lmtd"), 31.80803, rel_tol=1e-6)
    assert math.isclose(v(r, "lmtd") * 1500.0, v(r, "duty"), rel_tol=1e-9)
    assert not any("temperature cross" in w for w in r.warnings)  # impossible in parallel flow


def test_counterflow_general_rating_numbers():
    # same flows, Cr = 0.5, NTU = 1.5: e = 0.690785 ; Q = 55 262.83 W ; Th,out = 72.36858 ; Tc,out = 75.26283
    #   dT1 = 100 - 75.26283 = 24.73717 ; dT2 = 72.36858 - 20 = 52.36858 ; LMTD = 27.63141/ln(52.36858/24.73717) = 36.84189
    args = base(2000, 1000, ua="1500 W/K")
    r = ok(exchanger, arrangement="counterflow", **args)
    assert math.isclose(v(r, "duty"), 55262.83, rel_tol=1e-7)
    assert math.isclose(v(r, "hot_outlet_temperature"), 72.36858, abs_tol=1e-5)
    assert math.isclose(v(r, "cold_outlet_temperature"), 75.26283, abs_tol=1e-5)
    assert math.isclose(v(r, "lmtd"), 36.84189, rel_tol=1e-6)


@pytest.mark.parametrize(
    "arrangement, eps",
    [("shell_tube", 0.539940), ("crossflow_hot_mixed", 0.541969), ("crossflow_cold_mixed", 0.544764)],
)
def test_other_arrangements_rating_at_cr_half_ntu_one(arrangement, eps):
    # hot C = 2000 (the mixed stream of crossflow_hot_mixed is Cmax), cold C = 1000 = Cmin, UA = 1000 -> NTU = 1, Cr = 0.5
    args = base(2000, 1000, ua="1000 W/K")
    r = ok(exchanger, arrangement=arrangement, **args)
    assert math.isclose(v(r, "effectiveness"), eps, abs_tol=2e-6)
    assert math.isclose(v(r, "duty"), eps * 1000 * 80, rel_tol=1e-5)


def test_crossflow_mixed_fluid_role_follows_the_capacity_rates():
    # swap the capacity rates: now the HOT stream is Cmin (1000) and the cold is Cmax (2000), so 'hot_mixed' means Cmin mixed
    args = base(1000, 2000, ua="1000 W/K")
    hot_mixed = ok(exchanger, arrangement="crossflow_hot_mixed", **args)
    cold_mixed = ok(exchanger, arrangement="crossflow_cold_mixed", **args)
    assert math.isclose(v(hot_mixed, "effectiveness"), 0.544764, abs_tol=2e-6)  # Cmin mixed
    assert math.isclose(v(cold_mixed, "effectiveness"), 0.541969, abs_tol=2e-6)  # Cmax mixed


def test_crossflow_unmixed_rating_reports_f_derived_from_ntu():
    args = base(2000, 1000, ua="1000 W/K")
    r = ok(exchanger, arrangement="crossflow_unmixed", **args)
    # NTU = 1, Cr = 0.5: series value; F = Q/(UA LMTD_counterflow) is below 1
    assert 0.9 < v(r, "lmtd_correction_factor") < 1.0
    assert "not an independent check" in r.data["lmtd_route"]["correction_factor_source"]


# ---------------------------------------------------------------------------------------------------- tool: NTU route == LMTD route
@pytest.mark.parametrize("arrangement", ["parallel", "counterflow", "shell_tube"])
@pytest.mark.parametrize("hot_c, cold_c", [(1000, 1000), (2000, 1000), (600, 1500), (4000, 250)])
@pytest.mark.parametrize("ua", ["300 W/K", "1500 W/K", "2500 W/K"])
def test_lmtd_route_and_ntu_route_agree_to_1e_9(arrangement, hot_c, cold_c, ua):
    r = ok(exchanger, arrangement=arrangement, **base(hot_c, cold_c, ua=ua))
    assert r.data["lmtd_route"]["relative_difference_to_ntu_route"] < 1e-9
    q, f = v(r, "duty"), v(r, "lmtd_correction_factor")
    # independent: Q = UA F LMTD with the LMTD recomputed here from the returned outlet temperatures
    th_o, tc_o = v(r, "hot_outlet_temperature"), v(r, "cold_outlet_temperature")
    d1, d2 = (100 - 20, th_o - tc_o) if arrangement == "parallel" else (100 - tc_o, th_o - 20)
    lm = d1 if math.isclose(d1, d2, rel_tol=1e-12) else (d1 - d2) / math.log(d1 / d2)
    assert math.isclose(float(ua.split()[0]) * f * lm, q, rel_tol=1e-9)
    # energy balance: hot-side duty equals cold-side duty
    assert math.isclose(hot_c * (100 - th_o), cold_c * (tc_o - 20), rel_tol=1e-9)
    assert r.data["energy_balance"]["relative_difference"] < 1e-12


def test_lmtd_cross_check_is_skipped_with_a_warning_when_an_end_difference_vanishes():
    # C = 4000 / 250, UA = 4000 -> NTU = 16, parallel flow: the cold outlet equals the hot outlet to ~1e-7 of the span
    r = ok(exchanger, arrangement="parallel", **base(4000, 250, ua="4000 W/K"))
    assert r.data["lmtd_route"]["relative_difference_to_ntu_route"] is None
    assert "lmtd" not in r.values and any("LMTD cross-check was skipped" in w for w in r.warnings)
    assert math.isclose(v(r, "effectiveness"), 1.0 / (1.0 + 0.0625), rel_tol=1e-6)  # the NTU route itself is fine


# ---------------------------------------------------------------------------------------------------- tool: sizing
def test_sizing_counterflow_is_the_inverse_of_the_rating():
    # duty 160000/3 W with C = 1000/1000 -> e = 2/3, Cr = 1 -> NTU = e/(1 - e) = 2 -> UA = 2000 W/K ; area for U = 500 W/m^2K is 4 m^2
    r = ok(exchanger, arrangement="counterflow", **base(1000, 1000, duty=f"{160000.0 / 3.0} W", overall_u="500 W/(m^2*K)"))
    assert math.isclose(v(r, "ua"), 2000.0, rel_tol=1e-12)
    assert math.isclose(v(r, "area"), 4.0, rel_tol=1e-12)
    assert math.isclose(v(r, "ntu"), 2.0, rel_tol=1e-12)
    assert r.data["mode"] == "sizing" and r.data["lmtd_route"]["relative_difference_to_ntu_route"] < 1e-9


@pytest.mark.parametrize("arrangement", ["parallel", "counterflow", "shell_tube", "crossflow_unmixed", "crossflow_hot_mixed", "crossflow_cold_mixed"])
def test_rating_then_sizing_round_trip(arrangement):
    rated = ok(exchanger, arrangement=arrangement, **base(1500, 900, ua="1200 W/K"))
    sized = ok(exchanger, arrangement=arrangement, **base(1500, 900, duty=f"{v(rated, 'duty')!r} W"))
    assert math.isclose(v(sized, "ua"), 1200.0, rel_tol=1e-8)
    assert math.isclose(v(sized, "cold_outlet_temperature"), v(rated, "cold_outlet_temperature"), abs_tol=1e-8)


def test_sizing_hot_stream_as_cmin_gives_the_same_ua_when_streams_swap():
    # swapping which stream is hot/cold changes nothing in the physics: C_hot = 700, C_cold = 1400, Q = 28 000 W
    a = ok(exchanger, arrangement="shell_tube", **base(700, 1400, duty="28 kW"))
    assert math.isclose(v(a, "capacity_rate_ratio"), 0.5, rel_tol=1e-12)
    # e = 28000/(700*80) = 0.5 ; NTU = ... verify by re-rating
    b = ok(exchanger, arrangement="shell_tube", **base(700, 1400, ua=f"{v(a, 'ua')!r} W/K"))
    assert math.isclose(v(b, "duty"), 28000.0, rel_tol=1e-9)


def test_temperature_cross_in_shell_and_tube_is_warned():
    # shell and tube, Cr = 1, NTU = 2: e = 0.55681, Th,out = 55.455, Tc,out = 64.545 -> the cold outlet is above the hot outlet
    r = ok(exchanger, arrangement="shell_tube", **base(1000, 1000, ua="2000 W/K"))
    assert math.isclose(v(r, "effectiveness"), 0.556810, abs_tol=2e-6)
    assert math.isclose(v(r, "hot_outlet_temperature"), 55.4552, abs_tol=1e-3)
    assert any("temperature cross" in w and "correction factor" in w for w in r.warnings)
    assert r.data["temperature_cross"] is True


def test_units_imperial_inputs_and_outputs():
    # 1 kg/s = 7936.64 lb/h ; 1000 J/(kg K) = 0.2388459 Btu/(lb degF) (degF in a rate is a difference)
    metric = ok(exchanger, arrangement="counterflow", **base(1000, 1000, ua="2000 W/K"))
    args = dict(
        arrangement="counterflow",
        hot_inlet_temperature="212 degF",
        cold_inlet_temperature="68 degF",
        hot_mass_flow="7936.6414 lb/hour",
        hot_cp="0.2388459 Btu/(lb*degF)",
        cold_mass_flow="7936.6414 lb/hour",
        cold_cp="0.2388459 Btu/(lb*degF)",
        ua="3791.26795 Btu/(hour*degF)",
        unit_system="imperial",
    )
    r = ok(exchanger, **args)
    # hot 212 degF = 100 degC, cold 68 degF = 20 degC ; 2000 W/K = 2000*3600/1055.056/1.8 = 3791.268 Btu/(h degF)
    assert math.isclose(v(r, "effectiveness"), v(metric, "effectiveness"), rel_tol=1e-5)
    assert r.values["hot_outlet_temperature"].unit == "degree_Fahrenheit"
    assert math.isclose(v(r, "hot_outlet_temperature"), 46.666667 * 1.8 + 32.0, abs_tol=1e-2)
    assert any("temperature difference" in w for w in r.warnings)


# ---------------------------------------------------------------------------------------------------- refusals
def test_impossible_duty_is_refused_with_the_limit():
    # Cmin = 1000, dT = 80 K -> at most 80 kW: asking for 80 kW or more would put an outlet beyond the other inlet
    f = fails(exchanger, "input.invalid", "duty", arrangement="counterflow", **base(1000, 1000, duty="80 kW"))
    assert math.isclose(f.error.details["max_duty_W"], 80000.0, rel_tol=1e-12)
    fails(exchanger, "input.invalid", "duty", arrangement="counterflow", **base(1000, 1000, duty="200 kW"))


def test_duty_beyond_what_the_arrangement_can_reach_is_refused():
    # parallel flow, Cr = 1: e <= 1/(1+Cr) = 0.5, so at most 40 kW of the 80 kW thermodynamic maximum; 50 kW is infeasible,
    # counterflow can do it
    f = fails(exchanger, "input.invalid", "duty", arrangement="parallel", **base(1000, 1000, duty="50 kW"))
    assert math.isclose(f.error.details["max_duty_W"], 40000.0, rel_tol=1e-12)
    assert ok(exchanger, arrangement="counterflow", **base(1000, 1000, duty="50 kW")).ok
    fails(exchanger, "input.out_of_range", "duty", arrangement="crossflow_unmixed", **base(1000, 1000, duty="79.9999999 kW"))


def test_crossflow_series_limit_for_large_rating_ntu():
    fails(exchanger, "input.out_of_range", "ntu", arrangement="crossflow_unmixed", **base(1000, 1000, ua="500000 W/K"))
    assert ok(exchanger, arrangement="counterflow", **base(1000, 1000, ua="5e9 W/K")).ok  # closed forms have no limit
    assert ok(exchanger, arrangement="shell_tube", **base(1000, 1000, ua="5e9 W/K")).ok


@pytest.mark.parametrize(
    "overrides, family, param",
    [
        ({"hot_inlet_temperature": "20 degC"}, "input.invalid", "hot_inlet_temperature|cold_inlet_temperature"),
        ({"hot_inlet_temperature": "10 degC"}, "input.invalid", "hot_inlet_temperature|cold_inlet_temperature"),
        ({"hot_inlet_temperature": "100 delta_degC"}, "input.invalid", "hot_inlet_temperature"),
        ({"cold_inlet_temperature": "20 kg"}, "units.dimension_mismatch", "cold_inlet_temperature"),
        ({"hot_mass_flow": "0 kg/s"}, "input.invalid", "hot_mass_flow"),
        ({"hot_mass_flow": "-1 kg/s"}, "input.invalid", "hot_mass_flow"),
        ({"hot_mass_flow": "1 m"}, "units.dimension_mismatch", "hot_mass_flow"),
        ({"hot_mass_flow": "1e308 kg/s"}, "input.out_of_range", "hot_mass_flow"),
        ({"hot_cp": "-5 J/(kg*K)"}, "input.invalid", "hot_cp"),
        ({"hot_cp": "1000 J/kg"}, "units.dimension_mismatch", "hot_cp"),
        ({"cold_cp": "1e308 J/(kg*K)"}, "input.out_of_range", "cold_cp"),
        ({"ua": "-1 W/K"}, "input.invalid", "ua"),
        ({"ua": "0 W/K"}, "input.invalid", "ua"),
        ({"ua": "5 W"}, "units.dimension_mismatch", "ua"),
        ({"ua": "1e308 W/K"}, "input.out_of_range", "ua"),
        ({"arrangement": "heat_pipe"}, "input.invalid", "arrangement"),
    ],
)
def test_bad_inputs_are_refused(overrides, family, param):
    args = {"arrangement": "counterflow", **base(1000, 1000, ua="2000 W/K")}
    args.update(overrides)
    fails(exchanger, family, param, **args)


def test_over_and_under_specified_rating_and_sizing():
    for extra in (
        {},  # neither
        {"ua": "1000 W/K", "duty": "10 kW"},  # both
        {"ua": "1000 W/K", "area": "2 m^2"},
        {"duty": "10 kW", "area": "2 m^2"},
        {"overall_u": "500 W/(m^2*K)"},  # U alone
        {"area": "2 m^2"},
    ):
        fails(exchanger, "input.invalid", "ua|overall_u|area|duty", arrangement="counterflow", **base(1000, 1000, **extra))
    r = ok(exchanger, arrangement="counterflow", **base(1000, 1000, overall_u="400 W/(m^2*K)", area="5 m^2"))  # rating through U x A = 2000 W/K
    assert math.isclose(v(r, "ua"), 2000.0, rel_tol=1e-12)
