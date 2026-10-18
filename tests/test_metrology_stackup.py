"""metrology_stackup_1d checked against hand calculations.

Reference chain used throughout (closing dimension = gap G = A - B - C, all in mm):

    A  +  50.0   +0.10 / -0.10     housing, adds to the gap
    B  -  20.0   +0.00 / -0.12     bearing, subtracts (unilateral: runs 19.88 .. 20.00)
    C  -  29.5   +0.05 / -0.05     shaft stack, subtracts (runs 29.45 .. 29.55)

Nominal gap          = 50.0 - 20.0 - 29.5                         = 0.50
Worst case (every link at its limit, in the worst direction):
    G_max = A_max - B_min - C_min = 50.10 - 19.88 - 29.45         = 0.77
    G_min = A_min - B_max - C_max = 49.90 - 20.00 - 29.55         = 0.35
Link mid-points: A 50.00, B 19.94, C 29.50 -> mean gap = 50.00 - 19.94 - 29.50 = 0.56
Half-ranges (tolerance about the mid-point): A 0.10, B 0.06, C 0.05
RSS: t = sqrt(0.10^2 + 0.06^2 + 0.05^2) = sqrt(0.0161)            = 0.126886
     G = 0.56 +/- 0.126886 -> 0.433114 .. 0.686886
RSS variance shares: 0.01/0.0161 = 62.11 %, 0.0036/0.0161 = 22.36 %, 0.0025/0.0161 = 15.53 %
Worst-case shares of the span: 0.10/0.21 = 47.62 %, 0.06/0.21 = 28.57 %, 0.05/0.21 = 23.81 %
"""

import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology import stackup
from eng_toolkit.domains.metrology.tools import stackup_1d

CHAIN = [
    {"name": "A", "nominal": "50 mm", "plus": "0.10 mm", "minus": "0.10 mm", "direction": "+"},
    {"name": "B", "nominal": "20 mm", "plus": "0 mm", "minus": "0.12 mm", "direction": "-"},
    {"name": "C", "nominal": "29.5 mm", "tol": "0.05 mm", "direction": "-"},
]
CHAIN_JSON = json.dumps(CHAIN)


def _call(**kwargs) -> ToolResult:
    kwargs.setdefault("chain", CHAIN_JSON)
    fn = getattr(stackup_1d, "fn", stackup_1d)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def _v(r: ToolResult, name: str) -> float:
    return r.values[name].quantity


# --------------------------------------------------------------------------- worst case


def test_worst_case_matches_hand_calc():
    r = _call(method="worst_case")
    assert r.ok, r.error
    assert r.value.unit == "millimeter"
    assert _v(r, "result_nominal") == pytest.approx(0.50, abs=1e-12)
    assert _v(r, "result_min") == pytest.approx(0.35, abs=1e-12)
    assert _v(r, "result_max") == pytest.approx(0.77, abs=1e-12)
    assert _v(r, "result_mean") == pytest.approx(0.56, abs=1e-12)
    assert r.values["result_mean"].uncertainty is None
    assert r.data["method"] == "worst_case"
    shares = {c["name"]: c["share"] for c in r.data["contributions"]}
    assert shares["A"] == pytest.approx(0.10 / 0.21)
    assert shares["B"] == pytest.approx(0.06 / 0.21)
    assert shares["C"] == pytest.approx(0.05 / 0.21)
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.metrology.stackup"
    assert not any("monte" in w.lower() for w in r.warnings)


def test_worst_case_is_the_default_method_and_output_unit_converts():
    r = _call(output_unit="um")
    assert r.ok, r.error
    assert r.data["method"] == "worst_case"
    assert r.values["result_max"].unit == "micrometer"
    assert _v(r, "result_max") == pytest.approx(770.0, abs=1e-9)


def test_mixed_input_units_are_converted():
    # 2 in = 50.8 mm ; 0.004 in = 0.1016 mm.  G_max = 50.8 + 0.1016 - 19.88 - 29.45 = 1.5716
    chain = [
        {"name": "A", "nominal": "2 inch", "tol": "0.004 inch", "direction": "+"},
        {"name": "B", "nominal": "20 mm", "plus": "0 mm", "minus": "0.12 mm", "direction": "-"},
        {"name": "C", "nominal": "29.5 mm", "tol": "0.05 mm", "direction": "-"},
    ]
    r = _call(chain=json.dumps(chain))
    assert r.ok, r.error
    assert _v(r, "result_max") == pytest.approx(50.8 + 0.1016 - 19.88 - 29.45, abs=1e-9)


def test_spec_check_for_worst_case_is_a_guarantee():
    inside = _call(lower_limit="0.30 mm", upper_limit="0.80 mm")
    assert inside.data["spec_check"]["within_spec"] is True  # 0.35 .. 0.77 inside 0.30 .. 0.80
    outside = _call(lower_limit="0.40 mm", upper_limit="0.80 mm")
    assert outside.data["spec_check"]["within_spec"] is False  # 0.35 < 0.40
    assert outside.ok


# --------------------------------------------------------------------------- RSS


def test_rss_matches_hand_calc():
    r = _call(method="rss")
    assert r.ok, r.error
    t = math.sqrt(0.0161)
    assert _v(r, "result_nominal") == pytest.approx(0.50, abs=1e-12)
    assert _v(r, "result_mean") == pytest.approx(0.56, abs=1e-12)
    assert _v(r, "result_min") == pytest.approx(0.56 - t, abs=1e-12)
    assert _v(r, "result_max") == pytest.approx(0.56 + t, abs=1e-12)
    assert _v(r, "result_min") == pytest.approx(0.433114, abs=1e-6)
    assert _v(r, "result_max") == pytest.approx(0.686886, abs=1e-6)
    # one-sigma of the closing dimension: tolerances are +/-3 sigma
    assert r.values["result_mean"].uncertainty == pytest.approx(t / 3, abs=1e-12)
    shares = {c["name"]: c["share"] for c in r.data["contributions"]}
    assert shares["A"] == pytest.approx(0.01 / 0.0161)
    assert shares["B"] == pytest.approx(0.0036 / 0.0161)
    assert shares["C"] == pytest.approx(0.0025 / 0.0161)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert any("3 sigma" in w or "3-sigma" in w for w in r.warnings)


def test_rss_is_never_wider_than_worst_case():
    wc, rss = _call(method="worst_case"), _call(method="rss")
    assert _v(rss, "result_max") <= _v(wc, "result_max")
    assert _v(rss, "result_min") >= _v(wc, "result_min")


# --------------------------------------------------------------------------- Monte Carlo


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def test_monte_carlo_normal_matches_the_analytic_probability():
    # Each link ~ Normal(mid-point, half-range/3).  Gap mean 0.56, sigma = 0.126886/3 = 0.0422953.
    # Spec 0.45 .. 0.70:  z_low = (0.45 - 0.56)/0.0422953 = -2.6007 ; z_high = (0.70 - 0.56)/0.0422953 = 3.3099
    # P(out) = Phi(-2.6007) + (1 - Phi(3.3099)) = 0.004647 + 0.000467 = 0.005114
    sigma = math.sqrt(0.0161) / 3
    p_expected = _phi((0.45 - 0.56) / sigma) + 1 - _phi((0.70 - 0.56) / sigma)
    n = 400_000
    r = _call(method="rss", monte_carlo=True, samples=n, seed=7, lower_limit="0.45 mm", upper_limit="0.70 mm")
    assert r.ok, r.error
    p = _v(r, "out_of_spec_probability")
    stderr = math.sqrt(p_expected * (1 - p_expected) / n)
    assert abs(p - p_expected) < 5 * stderr
    assert r.values["out_of_spec_probability"].unit == "dimensionless"
    assert r.values["out_of_spec_probability"].uncertainty == pytest.approx(math.sqrt(p * (1 - p) / n))
    assert _v(r, "mc_mean") == pytest.approx(0.56, abs=5 * sigma / math.sqrt(n))
    assert _v(r, "mc_std") == pytest.approx(sigma, rel=0.01)
    assert _v(r, "probability_below_lower") + _v(r, "probability_above_upper") == pytest.approx(p)
    assert r.data["monte_carlo"]["samples"] == n and r.data["monte_carlo"]["seed"] == 7
    assert r.data["monte_carlo"]["distribution"] == "normal"


def test_monte_carlo_uniform_has_the_uniform_variance():
    # uniform on [mid - h, mid + h] has variance h^2/3 -> gap sigma = sqrt((0.01+0.0036+0.0025)/3) = 0.073258
    n = 300_000
    r = _call(monte_carlo=True, distribution="uniform", samples=n, seed=3, lower_limit="0.30 mm")
    assert r.ok, r.error
    assert _v(r, "mc_std") == pytest.approx(math.sqrt(0.0161 / 3), rel=0.01)
    assert _v(r, "mc_min") >= 0.35 - 1e-9 and _v(r, "mc_max") <= 0.77 + 1e-9  # never beyond worst case
    assert _v(r, "out_of_spec_probability") == 0.0  # every sample >= 0.35 > 0.30


def test_monte_carlo_is_reproducible_for_a_fixed_seed():
    kw = dict(monte_carlo=True, samples=20_000, lower_limit="0.45 mm", upper_limit="0.70 mm")
    a, b = _call(seed=11, **kw), _call(seed=11, **kw)
    c = _call(seed=12, **kw)
    for name in ("mc_mean", "mc_std", "out_of_spec_probability", "mc_min", "mc_max"):
        assert _v(a, name) == _v(b, name)
    assert _v(a, "mc_mean") != _v(c, "mc_mean")
    # the default seed is fixed too
    assert _v(_call(**kw), "mc_mean") == _v(_call(**kw), "mc_mean")


def test_monte_carlo_without_spec_limits_reports_moments_and_says_why_no_probability():
    r = _call(monte_carlo=True, samples=5_000)
    assert r.ok, r.error
    assert "out_of_spec_probability" not in r.values
    assert any("lower_limit" in w and "upper_limit" in w for w in r.warnings)
    assert "mc_mean" in r.values


# --------------------------------------------------------------------------- error paths


@pytest.mark.parametrize(
    "bad_chain, family",
    [
        ('[{"nominal": "50", "tol": "0.1 mm", "direction": "+"}]', "units.bare_number"),
        ('[{"nominal": "50 mm", "tol": "0.1", "direction": "+"}]', "units.bare_number"),
        ('[{"nominal": "50 mm", "plus": "0", "minus": "0.1 mm", "direction": "+"}]', "units.bare_number"),
        ('[{"nominal": "50 bar", "tol": "0.1 mm", "direction": "+"}]', "units.dimension_mismatch"),
        ('[{"nominal": "50 mm", "tol": "0.1 mm", "direction": "up"}]', "input.invalid"),
        ('[{"nominal": "50 mm", "tol": "0.1 mm"}]', "input.invalid"),  # direction missing
        ('[{"nominal": "50 mm", "direction": "+"}]', "input.invalid"),  # no tolerance at all
        ('[{"nominal": "50 mm", "plus": "0.1 mm", "direction": "+"}]', "input.invalid"),  # plus without minus
        ('[{"nominal": "50 mm", "tol": "0.1 mm", "plus": "0.1 mm", "minus": "0.1 mm", "direction": "+"}]', "input.invalid"),
        ('[{"nominal": "50 mm", "tol": "-0.1 mm", "direction": "+"}]', "input.invalid"),  # negative tolerance
        ('[{"nominal": "50 mm", "plus": "0.1 mm", "minus": "-0.1 mm", "direction": "+"}]', "input.invalid"),
        ('[{"nominal": "50 mm", "tol": "0.1 mm", "direction": "+", "colour": "red"}]', "input.invalid"),  # typo guard
        ("[]", "input.invalid"),
        ("{}", "input.invalid"),
        ('{"nominal": "50 mm"}', "input.invalid"),
        ("not json", "input.invalid"),
        ("", "input.invalid"),
        ('["50 mm"]', "input.invalid"),
    ],
)
def test_bad_chains_are_rejected_with_a_specific_family(bad_chain, family):
    r = _call(chain=bad_chain)
    assert not r.ok
    assert r.error.family == family, r.error
    assert r.warnings == []


def test_error_names_the_offending_link():
    bad = json.dumps([CHAIN[0], {"name": "Z", "nominal": "50", "tol": "0.1 mm", "direction": "+"}])
    r = _call(chain=bad)
    assert not r.ok
    assert r.error.details["link_index"] == 1 and r.error.details["link_name"] == "Z"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"method": "monte_carlo"},
        {"method": ""},
        {"output_unit": "kg"},
        {"output_unit": "furlongs-per-fortnight"},
        {"lower_limit": "0.9 mm", "upper_limit": "0.1 mm"},  # lower above upper
        {"lower_limit": "0.5"},  # bare number
        {"lower_limit": "5 bar"},
        {"monte_carlo": True, "samples": 10},  # too few
        {"monte_carlo": True, "samples": 50_000_000},  # too many
        {"monte_carlo": True, "distribution": "triangular"},
    ],
)
def test_bad_options_are_rejected(kwargs):
    r = _call(**kwargs)
    assert not r.ok
    assert r.error.family in ("input.invalid", "units.bare_number", "units.dimension_mismatch", "units.parse_error")


def test_too_many_links_is_rejected():
    chain = json.dumps([CHAIN[2]] * 201)
    r = _call(chain=chain)
    assert not r.ok and r.error.family == "input.invalid"


def test_zero_tolerance_chain_has_zero_width_and_zero_shares():
    chain = json.dumps([{"nominal": "10 mm", "tol": "0 mm", "direction": "+"}, {"nominal": "4 mm", "tol": "0 mm", "direction": "-"}])
    for method in ("worst_case", "rss"):
        r = _call(chain=chain, method=method)
        assert r.ok, r.error
        assert _v(r, "result_min") == _v(r, "result_max") == pytest.approx(6.0)
        assert all(c["share"] == 0 for c in r.data["contributions"])


# --------------------------------------------------------------------------- fit links
#
# A fit link is the clearance of an ISO 286 pair: nominal 0, plus = max clearance,
# minus = -(min clearance).  H7/g6 at 25 mm: clearance 0.007 .. 0.041 mm
#     mid  = 0 + (0.041 + 0.007) / 2 = 0.024      half = (0.041 - 0.007) / 2 = 0.017
# H7/p6 at 25 mm (interference): clearance -0.035 .. -0.001 mm
#     mid  = (-0.001 - 0.035) / 2 = -0.018         half = (-0.001 + 0.035) / 2 = 0.017
# H7/h6 at 25 mm: clearance 0 .. 0.034 mm
#
# Chain 1: A = housing 50 mm +/-0.10 (+), F = H7/g6 at 25 mm (-)
#     nominal = 50 - 0 = 50 ; mean = 50 - 0.024 = 49.976
#     worst case: max = 50.10 - 0.007 = 50.093 ; min = 49.90 - 0.041 = 49.859
#     RSS: t = sqrt(0.10^2 + 0.017^2) = sqrt(0.010289) = 0.101439 ; limits 49.976 -/+ t
# Chain 2: A (+), F = H7/p6 at 25 mm (+)
#     worst case: max = 50.10 - 0.001 = 50.099 ; min = 49.90 - 0.035 = 49.865 ; mean = 50 - 0.018 = 49.982
# Chain 3: A (+), F = H7/h6 at 25 mm (+)
#     worst case: max = 50.10 + 0.034 = 50.134 ; min = 49.90 + 0 = 49.90 (widens the max side only)

A_LINK = {"name": "housing", "nominal": "50 mm", "tol": "0.10 mm", "direction": "+"}


def _fit_link(fit="H7/g6", nominal="25 mm", direction="-", **extra):
    return {"name": "F", "fit": fit, "nominal": nominal, "direction": direction, **extra}


def _fit_chain(**fit_kwargs) -> str:
    return json.dumps([A_LINK, _fit_link(**fit_kwargs)])


def test_fit_link_worst_case_matches_hand_calc():
    r = _call(chain=_fit_chain())
    assert r.ok, r.error
    assert _v(r, "result_nominal") == pytest.approx(50.0, abs=1e-12)
    assert _v(r, "result_mean") == pytest.approx(49.976, abs=1e-9)
    assert _v(r, "result_max") == pytest.approx(50.093, abs=1e-9)
    assert _v(r, "result_min") == pytest.approx(49.859, abs=1e-9)
    assert r.warnings == _call().warnings  # a fit link adds no warning of its own


def test_fit_link_rss_matches_hand_calc():
    r = _call(chain=_fit_chain(), method="rss")
    assert r.ok, r.error
    t = math.sqrt(0.010289)
    assert _v(r, "result_mean") == pytest.approx(49.976, abs=1e-9)
    assert _v(r, "result_min") == pytest.approx(49.976 - t, abs=1e-9)
    assert _v(r, "result_max") == pytest.approx(49.976 + t, abs=1e-9)
    assert r.values["result_mean"].uncertainty == pytest.approx(t / 3, abs=1e-12)
    shares = {c["name"]: c["share"] for c in r.data["contributions"]}
    assert shares["housing"] == pytest.approx(0.01 / 0.010289)
    assert shares["F"] == pytest.approx(0.000289 / 0.010289)


def test_interference_fit_has_a_negative_plus_and_still_stacks():
    r = _call(chain=_fit_chain(fit="H7/p6", direction="+"))
    assert r.ok, r.error
    assert _v(r, "result_nominal") == pytest.approx(50.0, abs=1e-12)
    assert _v(r, "result_mean") == pytest.approx(49.982, abs=1e-9)
    assert _v(r, "result_max") == pytest.approx(50.099, abs=1e-9)
    assert _v(r, "result_min") == pytest.approx(49.865, abs=1e-9)
    link = r.data["contributions"][1]
    assert link["plus"] == pytest.approx(-0.001, abs=1e-12)
    assert link["minus"] == pytest.approx(0.035, abs=1e-12)


def test_fit_link_with_zero_minimum_clearance_widens_one_side_only():
    r = _call(chain=_fit_chain(fit="H7/h6", direction="+"))
    assert r.ok, r.error
    assert _v(r, "result_max") == pytest.approx(50.134, abs=1e-9)
    assert _v(r, "result_min") == pytest.approx(49.90, abs=1e-9)


def test_fit_link_nominal_in_inches_gives_the_same_limits():
    # 1 inch = 25.4 mm, inside the 18 .. 30 mm size range of the 25 mm case
    r = _call(chain=_fit_chain(nominal="1 inch"))
    assert r.ok, r.error
    assert _v(r, "result_max") == pytest.approx(50.093, abs=1e-9)
    assert _v(r, "result_min") == pytest.approx(49.859, abs=1e-9)


def test_fit_link_respects_the_output_unit():
    r = _call(chain=_fit_chain(), output_unit="um")
    assert r.ok, r.error
    assert r.values["result_max"].unit == "micrometer"
    assert _v(r, "result_max") == pytest.approx(50_093.0, abs=1e-6)
    assert _v(r, "result_min") == pytest.approx(49_859.0, abs=1e-6)
    link = r.data["contributions"][1]
    assert link["plus"] == pytest.approx(41.0, abs=1e-6) and link["minus"] == pytest.approx(-7.0, abs=1e-6)


def test_fit_link_monte_carlo_is_centred_on_the_fit_mid_point():
    n = 200_000
    sigma = math.sqrt(0.010289) / 3
    r = _call(chain=_fit_chain(), monte_carlo=True, samples=n, seed=5)
    assert r.ok, r.error
    assert _v(r, "mc_mean") == pytest.approx(49.976, abs=5 * sigma / math.sqrt(n))
    assert _v(r, "mc_std") == pytest.approx(sigma, rel=0.02)


def test_contributions_say_where_each_band_came_from():
    r = _call(chain=_fit_chain())
    manual, fit = r.data["contributions"]
    assert manual["kind"] == "dimension" and "fit" not in manual
    assert fit["kind"] == "fit" and fit["fit"] == "H7/g6"
    assert fit["nominal"] == 0.0
    assert fit["plus"] == pytest.approx(0.041, abs=1e-12) and fit["minus"] == pytest.approx(-0.007, abs=1e-12)
    assert fit["direction"] == "-"
    assert _call().data["contributions"][0]["kind"] == "dimension"


def test_fit_link_name_defaults_like_any_link():
    chain = json.dumps([A_LINK, {"fit": "H7/g6", "nominal": "25 mm", "direction": "-"}])
    r = _call(chain=chain)
    assert r.ok, r.error
    assert r.data["contributions"][1]["name"] == "link2"


@pytest.mark.parametrize(
    "link, family",
    [
        (_fit_link(tol="0.1 mm"), "input.invalid"),
        (_fit_link(plus="0.1 mm", minus="0.1 mm"), "input.invalid"),
        (_fit_link(plus="0.1 mm"), "input.invalid"),
        (_fit_link(fit="H7/q6"), "input.unknown_fit_code"),
        (_fit_link(fit="H7"), "input.unknown_fit_code"),
        (_fit_link(fit="H7/t6", nominal="10 mm"), "input.undefined_class"),
        (_fit_link(nominal="2 mm"), "input.out_of_range"),
        (_fit_link(nominal="501 mm"), "input.out_of_range"),
        (_fit_link(nominal="25"), "units.bare_number"),
        (_fit_link(nominal="25 bar"), "units.dimension_mismatch"),
        ({"name": "F", "fit": "H7/g6", "direction": "-"}, "input.invalid"),
        (_fit_link(fit=7), "input.invalid"),
        (_fit_link(fit=None), "input.invalid"),
        (_fit_link(direction="up"), "input.invalid"),
        (_fit_link(colour="red"), "input.invalid"),
    ],
)
def test_bad_fit_links_keep_their_error_family(link, family):
    r = _call(chain=json.dumps([A_LINK, link]))
    assert not r.ok
    assert r.error.family == family, r.error
    assert r.error.details["link_index"] == 1 and r.error.details["link_name"] == "F"
    assert r.error.message.startswith("link 2 (F): ")
    assert r.warnings == []


def test_manual_links_still_cannot_have_a_negative_side():
    chain = json.dumps([{"nominal": "50 mm", "plus": "-0.01 mm", "minus": "0.1 mm", "direction": "+"}])
    r = _call(chain=chain)
    assert not r.ok and r.error.family == "input.invalid"


def test_link_rejects_an_empty_band():
    with pytest.raises(ValueError, match="empty"):
        stackup.Link(name="bad", nominal=0.0, plus=-0.01, minus=0.005, sign=1)
    stackup.Link(name="point", nominal=0.0, plus=-0.005, minus=0.005, sign=1)


def test_band_formulas_hold_for_a_band_that_excludes_its_nominal():
    link = stackup.Link(name="F", nominal=0.0, plus=0.041, minus=-0.007, sign=-1)
    assert link.mid == pytest.approx(0.024)
    assert link.half == pytest.approx(0.017)
    wc = stackup.worst_case([link])
    assert wc.minimum == pytest.approx(-0.041) and wc.maximum == pytest.approx(-0.007)
