"""mech.beam_bending checked against hand calculations."""

import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.mech.beams import beam_bending

PSI_PA = 6894.757293168
LBF_N = 4.4482216152605
LBF_PER_FT_N_PER_M = 14.5939029372  # 1 lbf/ft in N/m
INCH_LBF_NM = 0.1129848290276167
FT_M = 0.3048


def _call(**kwargs) -> ToolResult:
    fn = getattr(beam_bending, "fn", beam_bending)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    assert result.review_status == "unreviewed"
    return result


def _fails(family: str, param: str | None = None, **kwargs) -> ToolResult:
    r = _call(**kwargs)
    assert r.ok is False and r.value is None
    assert r.error.family == family, r.error
    if param is not None:
        assert r.error.details.get("param") == param, r.error
    assert r.warnings == []
    return r


# ---------------------------------------------------------------- simply supported, uniform load
# L = 4 m, w = 10 kN/m, rectangle 50 x 100 mm (b = 50, h = 100), E = 210 GPa
#   I = b h^3 / 12 = 50 * 1e6 / 12 = 4.166667e6 mm^4 = 4.166667e-6 m^4 ; c = 50 mm ; S = I/c = 83 333 mm^3
#   R = w L / 2 = 20 kN ; V = 20 kN ; M = w L^2 / 8 = 10 * 16 / 8 = 20 kN m
#   sigma = M / S = 20e6 N mm / 83 333 mm^3 = 240 MPa
#   y = 5 w L^4 / (384 E I) = 5 * 10e3 * 256 / (384 * 210e9 * 4.166667e-6) = 12.8e6 / 336e6 = 0.0380952 m = 38.0952 mm  (L/105)
SS_UDL = dict(length="4 m", load="10 kN/m", load_type="udl", width="50 mm", height="100 mm")


def test_simply_supported_udl_matches_hand_calc():
    r = _call(**SS_UDL)
    assert r.ok, r.error
    assert r.value.unit == "megapascal"
    assert math.isclose(r.value.quantity, 240.0, rel_tol=1e-9)
    assert math.isclose(r.values["max_moment"].quantity, 20.0, rel_tol=1e-12)
    assert r.values["max_moment"].unit == "kilonewton * meter"
    assert math.isclose(r.values["max_shear_force"].quantity, 20.0, rel_tol=1e-12)
    assert math.isclose(r.values["reaction_left"].quantity, 20.0, rel_tol=1e-12)
    assert math.isclose(r.values["reaction_right"].quantity, 20.0, rel_tol=1e-12)
    assert math.isclose(r.values["max_deflection"].quantity, 38.0952381, rel_tol=1e-7)
    assert math.isclose(r.values["span_over_deflection"].quantity, 105.0, rel_tol=1e-9)
    assert math.isclose(r.values["section_second_moment"].quantity, 4166666.667, rel_tol=1e-9)
    assert math.isclose(r.values["section_modulus"].quantity, 83333.333, rel_tol=1e-8)
    assert r.data["case"] == "simply_supported/udl"
    assert math.isclose(r.data["max_moment_position_m"], 2.0, rel_tol=1e-12)
    assert any("self-weight ignored" in w for w in r.warnings)


def test_custom_section_gives_the_same_result_as_the_rectangle():
    r = _call(length="4 m", load="10 kN/m", load_type="udl", section="custom", second_moment="4166666.6667 mm^4", extreme_fiber="50 mm")
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 240.0, rel_tol=1e-8)
    assert math.isclose(r.values["max_deflection"].quantity, 38.0952381, rel_tol=1e-7)


def test_unit_conversions_in_and_out():
    # 4 m = 13.123360 ft ; 10 kN/m = 685.21766 lbf/ft ; 210 GPa = 210 000 MPa
    r = _call(
        length=f"{4 / FT_M} ft",
        load=f"{10000 / LBF_PER_FT_N_PER_M} lbf/ft",
        load_type="udl",
        elastic_modulus="210000 MPa",
        width="5 cm",
        height="0.1 m",
    )
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 240.0, rel_tol=1e-8)
    # imperial output: 240 MPa = 34.809 ksi ; 20 kN m = 177 014.9 in lbf ; 20 kN = 4496.18 lbf ; 38.0952 mm = 1.49981 in
    i = _call(**SS_UDL, unit_system="imperial")
    assert i.value.unit == "kip_per_square_inch"
    assert math.isclose(i.value.quantity, 240e6 / PSI_PA / 1000.0, rel_tol=1e-9)
    assert math.isclose(i.values["max_moment"].quantity, 20000.0 / INCH_LBF_NM, rel_tol=1e-9)
    assert math.isclose(i.values["reaction_left"].quantity, 20000.0 / LBF_N, rel_tol=1e-9)
    assert math.isclose(i.values["max_deflection"].quantity, 38.0952381 / 25.4, rel_tol=1e-7)


# ---------------------------------------------------------------- simply supported, point load
# Rectangle 40 x 80 mm: I = 40 * 80^3 / 12 = 1 706 667 mm^4, c = 40 mm, E I = 210e9 * 1.706667e-6 = 358 400 N m^2
# (a) midspan, P = 10 kN, L = 3 m: R = 5 kN each, M = P L / 4 = 7.5 kN m, sigma = 7.5e6 * 40 / 1 706 667 = 175.78 MPa
#     y = P L^3 / (48 E I) = 10e3 * 27 / (48 * 358 400) = 270 000 / 17 203 200 = 0.0156948 m = 15.6948 mm
# (b) P = 12 kN, L = 4 m, a = 1 m (b = 3 m): R1 = P b / L = 9 kN, R2 = P a / L = 3 kN, M = P a b / L = 9 kN m,
#     sigma = 9e6 * 40 / 1 706 667 = 210.94 MPa ; y = P s (L^2 - s^2)^1.5 / (9 sqrt3 E I L), s = 1 m:
#     (16 - 1)^1.5 = 58.0948 ; y = 12e3 * 58.0948 / (15.5885 * 358 400 * 4) = 697 137 / 22 347 ... = 0.031195 m = 31.195 mm
RECT_40_80 = dict(width="40 mm", height="80 mm")


def test_point_load_at_midspan_is_the_default():
    r = _call(length="3 m", load="10 kN", **RECT_40_80)
    assert r.ok, r.error
    assert math.isclose(r.values["reaction_left"].quantity, 5.0, rel_tol=1e-12)
    assert math.isclose(r.values["reaction_right"].quantity, 5.0, rel_tol=1e-12)
    assert math.isclose(r.values["max_moment"].quantity, 7.5, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 175.78125, rel_tol=1e-9)
    assert math.isclose(r.values["max_deflection"].quantity, 15.694754, rel_tol=1e-7)
    assert math.isclose(r.data["max_moment_position_m"], 1.5, rel_tol=1e-12)
    explicit = _call(length="3 m", load="10 kN", position="1.5 m", **RECT_40_80)
    assert math.isclose(explicit.value.quantity, r.value.quantity, rel_tol=1e-12)


def test_off_centre_point_load_and_mirror_symmetry():
    r = _call(length="4 m", load="12 kN", position="1 m", **RECT_40_80)
    assert r.ok, r.error
    assert math.isclose(r.values["reaction_left"].quantity, 9.0, rel_tol=1e-12)
    assert math.isclose(r.values["reaction_right"].quantity, 3.0, rel_tol=1e-12)
    assert math.isclose(r.values["max_shear_force"].quantity, 9.0, rel_tol=1e-12)
    assert math.isclose(r.values["max_moment"].quantity, 9.0, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 210.9375, rel_tol=1e-9)
    assert math.isclose(r.values["max_deflection"].quantity, 31.195145, rel_tol=1e-6)
    mirror = _call(length="4 m", load="12 kN", position="3 m", **RECT_40_80)
    assert math.isclose(mirror.values["reaction_left"].quantity, 3.0, rel_tol=1e-12)
    assert math.isclose(mirror.values["max_deflection"].quantity, r.values["max_deflection"].quantity, rel_tol=1e-12)


def test_load_at_a_support_gives_zero_moment_and_deflection():
    r = _call(length="4 m", load="12 kN", position="0 m", **RECT_40_80)
    assert r.ok, r.error
    assert r.value.quantity == 0.0 and r.values["max_deflection"].quantity == 0.0
    assert math.isclose(r.values["reaction_left"].quantity, 12.0, rel_tol=1e-12)
    assert "span_over_deflection" not in r.values


# ---------------------------------------------------------------- cantilever
# Circle d = 60 mm: I = pi d^4 / 64 = pi * 1.296e7 / 64 = 636 172.5 mm^4, c = 30 mm, E I = 210e9 * 6.361725e-7 = 133 596.2 N m^2
# (a) tip load P = 2 kN, L = 1.5 m: M = P L = 3 kN m ; sigma = 3e6 * 30 / 636 172.5 = 141.47 MPa ;
#     y = P L^3 / (3 E I) = 2000 * 3.375 / (3 * 133 596.2) = 6750 / 400 788.7 = 0.0168418 m = 16.8418 mm
# (b) P = 2 kN at a = 1 m from the wall, L = 1.5 m: M = P a = 2 kN m ; sigma = 94.31 MPa ;
#     tip y = P a^2 (3L - a) / (6 E I) = 2000 * 1 * 3.5 / (6 * 133 596.2) = 7000 / 801 577.4 = 0.0087328 m = 8.7328 mm
# (c) tube 60 x 50 mm (I = pi (60^4 - 50^4) / 64 = pi * 6.71e6 / 64 = 329 376 mm^4), w = 1 kN/m, L = 2 m:
#     M = w L^2 / 2 = 2 kN m ; sigma = 2e6 * 30 / 329 376 = 182.16 MPa ; E I = 210e9 * 3.29376e-7 = 69 168 N m^2 ;
#     y = w L^4 / (8 E I) = 1000 * 16 / (8 * 69 168) = 16 000 / 553 344 = 0.028915 m = 28.915 mm
def test_cantilever_tip_load():
    r = _call(length="1.5 m", load="2 kN", support="cantilever", section="circle", diameter="60 mm")
    assert r.ok, r.error
    assert math.isclose(r.values["wall_reaction_force"].quantity, 2.0, rel_tol=1e-12)
    assert math.isclose(r.values["wall_moment"].quantity, 3.0, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 141.47106, rel_tol=1e-7)
    assert math.isclose(r.values["max_deflection"].quantity, 16.841793, rel_tol=1e-7)
    assert "reaction_left" not in r.values
    assert r.data["case"] == "cantilever/point"


def test_cantilever_point_load_inside_the_span():
    r = _call(length="1.5 m", load="2 kN", support="cantilever", position="1 m", section="circle", diameter="60 mm")
    assert math.isclose(r.values["wall_moment"].quantity, 2.0, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 94.314, rel_tol=1e-5)
    assert math.isclose(r.values["max_deflection"].quantity, 8.732782, rel_tol=1e-6)


def test_cantilever_uniform_load_on_a_tube():
    r = _call(length="2 m", load="1 kN/m", load_type="udl", support="cantilever", section="tube", diameter="60 mm", inner_diameter="50 mm")
    assert r.ok, r.error
    assert math.isclose(r.values["wall_reaction_force"].quantity, 2.0, rel_tol=1e-12)
    assert math.isclose(r.values["wall_moment"].quantity, 2.0, rel_tol=1e-12)
    assert math.isclose(r.values["section_second_moment"].quantity, 329376.35, rel_tol=1e-7)
    assert math.isclose(r.value.quantity, 182.162, rel_tol=1e-5)
    assert math.isclose(r.values["max_deflection"].quantity, 28.9147, rel_tol=1e-5)


# ---------------------------------------------------------------- warnings
def test_warnings_deep_beam_large_deflection_utilization_and_ignored_position():
    # L = 0.5 m on a 100 mm deep section: span/depth = 5 < 10
    deep = _call(length="0.5 m", load="10 kN", width="50 mm", height="100 mm")
    assert any("span / depth = 5.0" in w and "shear deformation" in w for w in deep.warnings)
    # cantilever d = 40 mm, L = 2 m, P = 5 kN: y = P L^3 / 3EI = 5000 * 8 / (3 * 210e9 * 1.256637e-7) = 0.50525 m = 25 % of L
    # sigma = 10e6 * 20 / 125 663.7 = 1591.5 MPa -> 9.95 x a 160 MPa allowable
    big = _call(length="2 m", load="5 kN", support="cantilever", section="circle", diameter="40 mm", allowable_stress="160 MPa")
    assert math.isclose(big.values["max_deflection"].quantity, 505.2538, rel_tol=1e-6)
    assert any("25.3% of the span" in w for w in big.warnings)
    assert math.isclose(big.values["utilization"].quantity, 1591.549 / 160.0, rel_tol=1e-5)
    assert any("9.95 x the allowable" in w for w in big.warnings)
    ignored = _call(**SS_UDL, position="1 m")
    assert any("'position' is ignored" in w for w in ignored.warnings)
    assert not any("span / depth" in w for w in _call(**SS_UDL).warnings)  # 4000 / 100 = 40


# ---------------------------------------------------------------- invalid input
@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"support": "fixed_fixed"}, "input.invalid", "support"),
        ({"load_type": "triangular"}, "input.invalid", "load_type"),
        ({"section": "ibeam"}, "input.invalid", "section"),
        ({"length": "0 m"}, "input.invalid", "length"),
        ({"length": "-4 m"}, "input.invalid", "length"),
        ({"length": "4"}, "units.bare_number", "length"),
        ({"length": "4 kg"}, "units.dimension_mismatch", "length"),
        ({"length": "1e9 m"}, "input.out_of_range", "length"),
        ({"load": "0 kN/m"}, "input.invalid", "load"),
        ({"load": "10 kN"}, "units.dimension_mismatch", "load"),  # udl needs a force per length
        ({"load": "10"}, "units.bare_number", "load"),
        ({"load": "1e15 kN/m"}, "input.out_of_range", "load"),
        ({"elastic_modulus": "210"}, "units.bare_number", "elastic_modulus"),
        ({"elastic_modulus": "210 mm"}, "units.dimension_mismatch", "elastic_modulus"),
        ({"elastic_modulus": "0 GPa"}, "input.invalid", "elastic_modulus"),
        ({"elastic_modulus": "1e9 GPa"}, "input.out_of_range", "elastic_modulus"),
        ({"width": ""}, "input.missing", "width"),
        ({"height": "0 mm"}, "input.invalid", "height"),
        ({"height": "-100 mm"}, "input.invalid", "height"),
        ({"height": "100 N"}, "units.dimension_mismatch", "height"),
        ({"height": "1e9 mm"}, "input.out_of_range", "height"),
        ({"allowable_stress": "160"}, "units.bare_number", "allowable_stress"),
        ({"allowable_stress": "160 mm"}, "units.dimension_mismatch", "allowable_stress"),
        ({"unit_system": "cubits"}, "input.invalid", "unit_system"),
    ],
)
def test_beam_bending_invalid_inputs(override, family, param):
    _fails(family, param, **{**SS_UDL, **override})


@pytest.mark.parametrize(
    ("kwargs", "family", "param"),
    [
        # point load: a force per length is the wrong dimension
        (dict(length="3 m", load="10 kN/m", **RECT_40_80), "units.dimension_mismatch", "load"),
        (dict(length="3 m", load="10 kN", position="4 m", **RECT_40_80), "input.out_of_range", "position"),
        (dict(length="3 m", load="10 kN", position="-1 m", **RECT_40_80), "input.invalid", "position"),
        (dict(length="3 m", load="10 kN", position="1.5", **RECT_40_80), "units.bare_number", "position"),
        (dict(length="3 m", load="10 kN", position="1 N", **RECT_40_80), "units.dimension_mismatch", "position"),
        (dict(length="3 m", load="10 kN", section="circle"), "input.missing", "diameter"),
        (dict(length="3 m", load="10 kN", section="tube", diameter="40 mm"), "input.missing", "inner_diameter"),
        (dict(length="3 m", load="10 kN", section="tube", diameter="40 mm", inner_diameter="40 mm"), "input.invalid", "inner_diameter"),
        (dict(length="3 m", load="10 kN", section="tube", diameter="40 mm", inner_diameter="50 mm"), "input.invalid", "inner_diameter"),
        (dict(length="3 m", load="10 kN", section="custom", extreme_fiber="50 mm"), "input.missing", "second_moment"),
        (dict(length="3 m", load="10 kN", section="custom", second_moment="1e6 mm^4"), "input.missing", "extreme_fiber"),
        (dict(length="3 m", load="10 kN", section="custom", second_moment="1e6 mm^2", extreme_fiber="50 mm"), "units.dimension_mismatch", "second_moment"),
    ],
)
def test_beam_bending_invalid_point_and_section_inputs(kwargs, family, param):
    _fails(family, param, **kwargs)
