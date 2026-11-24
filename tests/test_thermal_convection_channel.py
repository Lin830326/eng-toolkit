"""thermal_convection natural_channel (Elenbaas / Bar-Cohen-Rohsenow) against a hand computation, and the finned-array warning."""

import math

import pytest

from eng_toolkit.domains.thermal.convection import convection, elenbaas_channel_nusselt
from tests.thermal_testing import fails, ok, v

# air at film temperature (82 + 45)/2 = 63.5 degC = 336.65 K: k 0.0284, rho 1.06, mu 1.97e-5, cp 1007 -> Pr = cp mu/k
AIR = dict(conductivity="0.0284 W/(m*K)", density="1.06 kg/m^3", dynamic_viscosity="1.97e-5 Pa*s", specific_heat="1007 J/(kg*K)")
HOT = dict(surface_temperature="82 degC", ambient_temperature="45 degC", expansion_coefficient="0.00297 1/K")
G = 9.80665


def hand(gap, flow_len, d_t=37.0, beta=0.00297):
    k, rho, mu, cp = 0.0284, 1.06, 1.97e-5, 1007.0
    nu, alpha = mu / rho, k / (rho * cp)
    ra_s = G * beta * d_t * gap**3 / (nu * alpha)  # Ra_S = g beta dT S^3 / (nu alpha)
    ra_star = ra_s * gap / flow_len
    nu_s = (576.0 / ra_star**2 + 2.873 / ra_star**0.5) ** -0.5
    ra_l = G * beta * d_t * flow_len**3 / (nu * alpha)
    s_opt = 2.714 * flow_len / ra_l**0.25
    return ra_star, nu_s * k / gap, s_opt


def test_channel_h_against_hand_computation():
    ra_star, h, s_opt = hand(0.00525, 0.100)
    r = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="5.25 mm", fin_height="24 mm", **AIR, **HOT)
    assert math.isclose(v(r, "rayleigh_modified"), ra_star, rel_tol=1e-6)
    assert math.isclose(v(r, "h"), h, rel_tol=1e-6)
    assert math.isclose(v(r, "optimum_gap"), s_opt * 1e3, rel_tol=1e-6)  # millimetre in the metric system
    assert 3.0 < v(r, "h") < 3.4 and 6.9 < v(r, "optimum_gap") < 7.3
    # at the optimum gap the Nusselt number is the published 1.307
    ra_opt = hand(s_opt, 0.100)[0]
    assert math.isclose(elenbaas_channel_nusselt(ra_opt), 1.307, abs_tol=2e-3)
    assert math.isclose(v(r, "h_at_optimum_gap"), 1.307 * 0.0284 / s_opt, rel_tol=1e-9)
    # the isolated plate h for the same length is about 1.8 times higher: the reason for the channel formula
    assert 1.6 < r.data["h_isolated_plate_same_length_W_per_m2_K"] / v(r, "h") < 2.0


def test_channel_limits_of_the_formula():
    # Ra* -> 0 : fully developed Nu = Ra*/24 ; Ra* -> large : isolated plate 0.5899 Ra*^(1/4)
    assert math.isclose(elenbaas_channel_nusselt(1e-3), 1e-3 / 24.0, rel_tol=1e-4)
    assert math.isclose(elenbaas_channel_nusselt(1e8), 0.5899 * 1e8**0.25, rel_tol=1e-3)


def test_flow_length_not_fin_height_is_what_matters():
    right = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="5 mm", **AIR, **HOT)
    wrong = ok(convection, correlation="natural_channel", length="24 mm", fin_gap="5 mm", **AIR, **HOT)  # fin height used as the flow length
    assert v(wrong, "h") > 1.5 * v(right, "h")


def test_validity_warnings_and_unit_system():
    wide = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="60 mm", **AIR, **HOT)  # Ra* far above 1e5? no: plate-like, check optimum warning
    assert any("optimum" in w for w in wide.warnings)
    far = ok(convection, correlation="natural_channel", length="2 m", fin_gap="0.5 mm", **AIR, **HOT)  # Ra* tiny
    assert any("Ra*" in w and "extrapolation" in w for w in far.warnings)
    short = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="5 mm", fin_height="3 mm", **AIR, **HOT)
    assert any("not a channel" in w for w in short.warnings)
    imperial = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="5 mm", unit_system="imperial", **AIR, **HOT)
    metric = ok(convection, correlation="natural_channel", length="100 mm", fin_gap="5 mm", **AIR, **HOT)
    assert math.isclose(v(imperial, "h"), v(metric, "h") * 0.1761101838, rel_tol=1e-4)


def test_plate_correlation_warns_when_a_fin_gap_is_given():
    plain = ok(convection, correlation="natural_vertical_plate", length="100 mm", **AIR, **HOT)
    assert not any("finned" in w for w in plain.warnings)
    finned = ok(convection, correlation="natural_vertical_plate", length="100 mm", fin_gap="5.25 mm", **AIR, **HOT)
    assert math.isclose(v(finned, "h"), v(plain, "h"), rel_tol=1e-12)  # the value is unchanged, the warning is new
    assert any("finned" in w and "natural_channel" in w for w in finned.warnings)
    assert math.isclose(finned.data["h_channel_for_comparison_W_per_m2_K"], hand(0.00525, 0.1)[1], rel_tol=1e-6)
    height_only = ok(convection, correlation="natural_vertical_plate_laminar", length="100 mm", fin_height="24 mm", **AIR, **HOT)
    assert any("finned" in w for w in height_only.warnings)
    forced = ok(convection, correlation="dittus_boelter", length="10 mm", velocity="5 m/s", fin_gap="5 mm", **AIR)
    assert any("fin_gap" in w for w in forced.warnings)


@pytest.mark.parametrize(
    "extra,family,param",
    [
        ({"fin_gap": ""}, "input.missing", "fin_gap"),
        ({"fin_gap": "0 mm"}, "input.invalid", "fin_gap"),
        ({"fin_gap": "-5 mm"}, "input.invalid", "fin_gap"),
        ({"fin_gap": "5"}, "units.bare_number", "fin_gap"),
        ({"fin_gap": "5 kg"}, "units.dimension_mismatch", "fin_gap"),
        ({"fin_gap": "1e300 m"}, "input.out_of_range", "fin_gap"),
        ({"fin_gap": "5 mm", "fin_height": "5"}, "units.bare_number", "fin_height"),
        ({"fin_gap": "5 mm", "fin_height": "-1 mm"}, "input.invalid", "fin_height"),
    ],
)
def test_channel_refusals(extra, family, param):
    fails(convection, family, param, correlation="natural_channel", length="100 mm", **AIR, **HOT, **extra)
