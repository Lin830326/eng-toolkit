"""The arithmetic of a DIMENSION entity: what it measures, which DIMLFAC applies, and the text a viewer shows.  Hand-calculated, no engine."""

import math

import pytest

from eng_toolkit.domains.drawing import dimensions as D

COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def ray(angle_deg, length=10.0):
    return (length * math.cos(math.radians(angle_deg)), length * math.sin(math.radians(angle_deg)), 0.0)


# --------------------------------------------------------------------------- what is measured


def test_a_horizontal_linear_dimension_measures_the_x_distance_only():
    m = D.measure("linear", {13: (0, 0, 0), 14: (100, 30, 0)}, rotation_deg=0.0)
    assert m.value == pytest.approx(100.0) and m.unit == "length"


def test_a_vertical_one_measures_the_y_distance_only():
    assert D.measure("linear", {13: (0, 0, 0), 14: (100, 30, 0)}, rotation_deg=90.0).value == pytest.approx(30.0)


def test_a_rotated_one_projects_onto_its_direction():
    expected = 100 * math.cos(math.radians(45)) + 30 * math.sin(math.radians(45))  # 91.9239
    assert D.measure("linear", {13: (0, 0, 0), 14: (100, 30, 0)}, rotation_deg=45.0).value == pytest.approx(expected)


def test_an_aligned_dimension_measures_the_straight_distance():
    assert D.measure("aligned", {13: (0, 0, 0), 14: (100, 30, 0)}).value == pytest.approx(math.hypot(100, 30))


def test_diameter_and_radius_are_the_distance_between_the_two_definition_points():
    assert D.measure("diameter", {10: (10, 0, 0), 15: (-10, 0, 0)}).value == pytest.approx(20.0)
    assert D.measure("radius", {10: (0, 0, 0), 15: (7, 0, 0)}).value == pytest.approx(7.0)


def test_an_ordinate_dimension_measures_one_coordinate_of_the_feature_from_the_origin():
    pts = {10: (5, 5, 0), 13: (25, 45, 0)}
    assert D.measure("ordinate", pts, x_type=True).value == pytest.approx(20.0)
    assert D.measure("ordinate", pts, x_type=False).value == pytest.approx(40.0)


@pytest.mark.parametrize(("arc_deg", "expected"), [(15, 30.0), (105, 150.0), (195, 30.0), (285, 150.0)])
def test_a_two_line_angle_is_the_sector_that_holds_the_dimension_arc(arc_deg, expected):
    # line 1 along +x, line 2 at 30 degrees, both through the origin; the four sectors are 30, 150, 30, 150 degrees
    pts = {13: (0, 0, 0), 14: (10, 0, 0), 15: (0, 0, 0), 10: (10 * COS30, 10 * SIN30, 0), 16: ray(arc_deg, 6.0)}
    m = D.measure("angular", pts)
    assert m.unit == "angle" and m.value == pytest.approx(expected)


def test_a_two_line_angle_of_lines_that_do_not_pass_through_their_defining_points_uses_the_intersection():
    # line 1: y = 0, defined by far-away points; line 2: x = 5, so they cross at (5, 0): the sector above and right is 90
    pts = {13: (20, 0, 0), 14: (30, 0, 0), 15: (5, 10, 0), 10: (5, 20, 0), 16: (9, 4, 0)}
    assert D.measure("angular", pts).value == pytest.approx(90.0)


def test_a_three_point_angle_runs_counterclockwise_from_the_first_point_to_the_second():
    pts = {15: (0, 0, 0), 13: (10, 0, 0), 14: (0, 10, 0), 10: ray(45, 6.0)}
    assert D.measure("angular_3point", pts).value == pytest.approx(90.0)
    swapped = {15: (0, 0, 0), 13: (0, 10, 0), 14: (10, 0, 0), 10: ray(225, 6.0)}
    assert D.measure("angular_3point", swapped).value == pytest.approx(270.0)


def test_parallel_lines_have_no_angle_and_say_so():
    pts = {13: (0, 0, 0), 14: (10, 0, 0), 15: (0, 5, 0), 10: (10, 5, 0), 16: (5, 2, 0)}
    assert D.measure("angular", pts).value is None


def test_a_missing_definition_point_is_a_failure_to_measure_not_a_crash():
    assert D.measure("linear", {13: (0, 0, 0)}).value is None


def test_the_dimension_type_names_come_from_the_low_four_bits_of_group_70():
    assert [D.kind_of(code) for code in (0, 1, 2, 3, 4, 5, 6)] == ["linear", "aligned", "angular", "diameter", "radius", "angular_3point", "ordinate"]
    assert D.kind_of(32 + 1) == "aligned" and D.kind_of(64 + 6) == "ordinate" and D.kind_of(128 + 0) == "linear"
    assert D.kind_of(7) == "unknown(7)"
    assert D.is_ordinate_x(64 + 6) is True and D.is_ordinate_x(6) is False


# --------------------------------------------------------------------------- DIMLFAC


def test_the_entitys_own_dstyle_override_wins_over_its_dimension_style():
    f = D.resolve_dimlfac(override=2.0, style=0.5, paper_space=False)
    assert (f.factor, f.source) == (2.0, "entity DSTYLE override")


def test_without_an_override_the_dimension_style_decides():
    f = D.resolve_dimlfac(override=None, style=0.5, paper_space=False)
    assert (f.factor, f.source) == (0.5, "dimension style")


def test_with_neither_it_is_one():
    f = D.resolve_dimlfac(override=None, style=None, paper_space=False)
    assert (f.factor, f.source) == (1.0, "default")


def test_a_negative_factor_applies_in_paper_space_as_its_absolute_value_and_not_in_model_space():
    paper = D.resolve_dimlfac(override=-2.0, style=None, paper_space=True)
    model = D.resolve_dimlfac(override=-2.0, style=None, paper_space=False)
    assert paper.factor == 2.0 and model.factor == 1.0
    assert "negative" in paper.note and "negative" in model.note


def test_a_zero_factor_is_invalid_and_is_read_as_one():
    f = D.resolve_dimlfac(override=0.0, style=None, paper_space=False)
    assert f.factor == 1.0 and "zero" in f.note


def test_dimlfac_scales_lengths_but_not_angles():
    assert D.displayed(100.0, "length", 2.0) == 200.0
    assert D.displayed(30.0, "angle", 2.0) == 30.0


# --------------------------------------------------------------------------- the text


def style(**kw):
    return D.TextStyle(**kw)


@pytest.mark.parametrize(
    ("value", "kw", "expected"),
    [
        (200.0, dict(dec=2, zin=0), "200.00"),
        (200.0, dict(dec=2, zin=8), "200"),
        (12.5, dict(dec=2, zin=8), "12.5"),
        (0.5, dict(dec=2, zin=12), ".5"),
        (0.5, dict(dec=2, zin=0), "0.50"),
        (12.3, dict(dec=1, zin=0, rnd=0.5), "12.5"),
        (12.5, dict(dec=1, zin=0, dsep=","), "12,5"),
        (1234.5678, dict(dec=3, zin=0), "1234.568"),
        (-0.0001, dict(dec=2, zin=0), "0.00"),
        (200.0, dict(dec=0, zin=0), "200"),
    ],
)
def test_the_value_is_rounded_and_zero_suppressed_as_the_style_says(value, kw, expected):
    assert D.format_value(value, "length", style(**kw)) == expected


def test_a_prefix_and_suffix_text_wraps_the_value_at_the_placeholder():
    assert D.format_value(200.0, "length", style(dec=0, post="<> mm")) == "200 mm"
    assert D.format_value(200.0, "length", style(dec=0, post="~<>H7")) == "~200H7"


def test_a_suffix_without_a_placeholder_is_appended():
    assert D.format_value(200.0, "length", style(dec=0, post="mm")) == "200mm"


def test_diameter_and_radius_get_their_symbols():
    assert D.format_value(10.0, "length", style(dec=0), kind="diameter") == "Ø10"
    assert D.format_value(7.0, "length", style(dec=0), kind="radius") == "R7"


def test_an_angle_is_printed_in_degrees():
    assert D.format_value(30.0, "angle", style(dec=2, adec=0)) == "30°"
    assert D.format_value(30.5, "angle", style(dec=2, adec=1, zin=8)) == "30.5°"


def test_a_text_unit_format_that_is_not_decimal_is_not_computed():
    assert D.format_value(25.4, "length", style(dec=2, lunit=5)) is None  # fractional
    assert D.format_value(25.4, "length", style(dec=2, lunit=4)) is None  # architectural


@pytest.mark.parametrize(
    ("override", "formatted", "expected", "hidden"),
    [
        (None, "200", "200", False),
        ("", "200", "200", False),
        ("<>", "200", "200", False),
        (" ", "200", "", True),
        ("~<> +0.1", "200", "~200 +0.1", False),
        ("2X <>", "10", "2X 10", False),
        ("TYP", "10", "TYP", False),
        ("<> <>", "10", "10 10", False),
    ],
)
def test_the_override_text_replaces_the_value_at_the_placeholder_and_a_space_hides_the_text(override, formatted, expected, hidden):
    shown = D.apply_override(override, formatted)
    assert (shown.text, shown.hidden) == (expected, hidden)


def test_percent_codes_become_symbols():
    assert D.unescape_percent("%%c10 %%p0.1 90%%d") == "Ø10 ±0.1 90°"
    assert D.unescape_percent("100%%%") == "100%"
