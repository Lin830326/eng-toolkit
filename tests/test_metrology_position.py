"""metrology_position_tolerance against hand calculations.

Zone diameter of a feature moved by (dx, dy): 2 * sqrt(dx^2 + dy^2).

    dx = 0.03, dy = 0.04  ->  2 * 0.05 = 0.10      (a 3-4-5 triangle: no rounding in the hand answer)
    dx = 0.06, dy = 0.08  ->  2 * 0.10 = 0.20
    dx = 0.03, dy = 0.04, dz = 0.12  ->  2 * sqrt(0.0009 + 0.0016 + 0.0144) = 2 * 0.13 = 0.26   (spherical zone)

Bonus tolerance, size limits in mm:

    hole, MMC 10.00 (smallest hole), actual 10.06             -> bonus 10.06 - 10.00 = 0.06
    shaft, MMC 10.00 (largest shaft), actual 9.94             -> bonus 10.00 - 9.94  = 0.06
    hole, LMC 10.10 (largest hole), actual 10.04              -> bonus 10.10 - 10.04 = 0.06
    shaft, LMC 9.90 (smallest shaft), actual 9.96             -> bonus 9.96 - 9.90   = 0.06
    hole MMC 10.00, actual 9.98 (smaller than the smallest)   -> bonus 0, flagged out of size
"""

import json

import numpy as np
import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology import position
from eng_toolkit.domains.metrology.gdt_tools import position_tolerance


def _call(**kwargs) -> ToolResult:
    kwargs.setdefault("unit", "mm")
    fn = getattr(position_tolerance, "fn", position_tolerance)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    assert result.review_status == "unreviewed"
    return result


def _one(r: ToolResult) -> dict:
    return r.data["features"][0]


# --------------------------------------------------------------------------- the zone


def test_three_four_five_deviation_needs_a_zone_of_one_tenth():
    r = _call(nominal="10,20", measured="10.03,20.04", tolerance="0.1 mm")
    assert r.ok, r.error
    f = _one(r)
    assert f["position_error"] == pytest.approx(0.1, abs=1e-12)
    assert f["radial_deviation"] == pytest.approx(0.05, abs=1e-12)
    assert f["deviation"] == pytest.approx([0.03, 0.04], abs=1e-12)
    assert f["within_tolerance"] is True  # exactly on the limit (rounding slack)
    assert r.value.quantity == pytest.approx(0.1, abs=1e-12)
    assert r.data["zone"].startswith("cylindrical")


def test_a_hair_over_the_limit_fails():
    r = _call(nominal="10,20", measured="10.03,20.04", tolerance="0.0999 mm")
    assert _one(r)["within_tolerance"] is False
    assert _one(r)["margin"] == pytest.approx(0.0999 - 0.1, abs=1e-9)
    assert r.values["features_out_of_position"].quantity == 1
    assert r.data["all_within_tolerance"] is False


def test_sphere_zone_for_three_coordinates():
    r = _call(nominal="0,0,0", measured="0.03,0.04,0.12", tolerance="0.26 mm")
    assert _one(r)["position_error"] == pytest.approx(0.26, abs=1e-12)
    assert _one(r)["within_tolerance"] is True
    assert r.data["zone"].startswith("spherical")


def test_several_features_json_and_text_agree_and_the_worst_is_reported():
    nominal = [[0, 0], [50, 0], [0, 50]]
    measured = [[0.03, 0.04], [50.06, 0.08], [0, 50.01]]
    a = _call(nominal=json.dumps(nominal), measured=json.dumps(measured), tolerance="0.15 mm")
    b = _call(nominal="0,0; 50,0; 0,50", measured="0.03,0.04; 50.06,0.08; 0,50.01", tolerance="0.15 mm")
    assert a.data["features"] == b.data["features"]
    errors = [f["position_error"] for f in a.data["features"]]
    assert errors == pytest.approx([0.1, 0.2, 0.02], abs=1e-12)
    assert a.value.quantity == pytest.approx(0.2, abs=1e-12)
    assert a.data["worst_feature"] == 2
    assert [f["within_tolerance"] for f in a.data["features"]] == [True, False, True]
    assert a.values["features_out_of_position"].quantity == 1


def test_zero_deviation_has_no_angle_and_zero_error():
    r = _call(nominal="1,1", measured="1,1", tolerance="0 mm")
    assert _one(r)["position_error"] == 0 and _one(r)["within_tolerance"] is True
    assert _one(r)["deviation_angle_deg"] is None


# --------------------------------------------------------------------------- bonus tolerance


@pytest.mark.parametrize(
    "kind, modifier, size, limits, expected",
    [
        ("hole", "M", "10.06", {"mmc_size": "10 mm"}, 0.06),
        ("shaft", "M", "9.94", {"mmc_size": "10 mm"}, 0.06),
        ("hole", "L", "10.04", {"lmc_size": "10.1 mm"}, 0.06),
        ("shaft", "L", "9.96", {"lmc_size": "9.9 mm"}, 0.06),
    ],
)
def test_bonus_is_the_departure_from_the_material_limit(kind, modifier, size, limits, expected):
    # deviation 0.075 -> zone 0.15: fails 0.1 on its own, passes 0.1 + 0.06
    r = _call(
        nominal="0,0", measured="0.045,0.06", tolerance="0.1 mm", bonus_modifier=modifier, feature_kind=kind, feature_size=size,
        **limits,
    )
    f = _one(r)
    assert f["bonus"] == pytest.approx(expected, abs=1e-12)
    assert f["allowed_zone"] == pytest.approx(0.1 + expected, abs=1e-12)
    assert f["position_error"] == pytest.approx(0.15, abs=1e-12)
    assert f["within_tolerance"] is True
    assert f["margin"] == pytest.approx(0.01, abs=1e-12)
    assert r.values["max_bonus"].quantity == pytest.approx(expected, abs=1e-12)
    # the same feature with no modifier gets nothing and fails
    plain = _call(nominal="0,0", measured="0.045,0.06", tolerance="0.1 mm")
    assert _one(plain)["bonus"] == 0 and _one(plain)["within_tolerance"] is False


def test_mmc_bonus_equals_actual_size_minus_mmc_size_for_a_hole_per_feature():
    r = _call(
        nominal="0,0; 20,0; 40,0",
        measured="0,0; 20,0; 40,0",
        tolerance="0.05 mm",
        bonus_modifier="M",
        feature_kind="hole",
        feature_size="8.00; 8.013; 8.1",
        mmc_size="8 mm",
        lmc_size="8.1 mm",
    )
    assert [f["bonus"] for f in r.data["features"]] == pytest.approx([0.0, 0.013, 0.1], abs=1e-12)
    assert [f["allowed_zone"] for f in r.data["features"]] == pytest.approx([0.05, 0.063, 0.15], abs=1e-12)


def test_size_beyond_the_material_limit_gets_no_bonus_and_a_warning():
    r = _call(
        nominal="0,0", measured="0,0", tolerance="0.1 mm", bonus_modifier="M", feature_kind="hole", feature_size="9.98",
        mmc_size="10 mm",
    )
    assert _one(r)["bonus"] == 0
    assert any("beyond their MMC size" in w for w in r.warnings)


def test_a_feature_outside_both_size_limits_is_flagged():
    r = _call(
        nominal="0,0", measured="0,0", tolerance="0.1 mm", bonus_modifier="M", feature_kind="hole", feature_size="10.3",
        mmc_size="10 mm", lmc_size="10.1 mm",
    )
    assert _one(r)["bonus"] == pytest.approx(0.3, abs=1e-12)
    assert any("outside the size limits" in w for w in r.warnings)


def test_modifier_none_ignores_size_arguments_with_a_warning():
    r = _call(nominal="0,0", measured="0,0", tolerance="0.1 mm", feature_size="10.06", mmc_size="10 mm")
    assert _one(r)["bonus"] == 0
    assert any("ignored" in w for w in r.warnings)


# --------------------------------------------------------------------------- units and magnitude


def test_inch_and_micrometre_inputs_give_the_same_physical_answer():
    mm = _call(nominal="25.4,25.4", measured="25.4254,25.4", tolerance="0.06 mm")
    inch = _call(nominal="1,1", measured="1.001,1", tolerance="0.06 mm", unit="inch")
    um = _call(nominal="25400,25400", measured="25425.4,25400", tolerance="0.06 mm", unit="um")
    for r in (mm, inch, um):
        assert _one(r)["position_error"] == pytest.approx(0.0508, abs=1e-9)
        assert _one(r)["within_tolerance"] is True
    out = _call(nominal="1,1", measured="1.001,1", tolerance="0.002 inch", unit="inch", output_unit="inch")
    assert out.value.unit == "inch"
    assert out.value.quantity == pytest.approx(0.002, abs=1e-12)
    assert _one(out)["within_tolerance"] is True


def test_coordinates_of_a_million_millimetres_do_not_lose_the_answer():
    r = _call(nominal="1000000,2000000", measured="1000000.03,2000000.04", tolerance="0.1 mm")
    assert _one(r)["position_error"] == pytest.approx(0.1, abs=1e-8)
    assert _one(r)["within_tolerance"] is True  # on the limit to within the rounding of the coordinates


# --------------------------------------------------------------------------- errors


def _fail(**kwargs) -> tuple[str, str]:
    r = _call(**kwargs)
    assert not r.ok
    return r.error.family, r.error.message


def test_errors_are_clear():
    assert _fail(nominal="0,0", measured="0,0", tolerance="0.1")[0] == "units.bare_number"
    assert _fail(nominal="0,0", measured="0,0", tolerance="-0.1 mm")[0] == "input.invalid"
    assert _fail(nominal="0,0", measured="0,0", tolerance="0.1 s")[0] == "units.dimension_mismatch"
    assert _fail(nominal="0,0", measured="0,0", tolerance="0.1 mm", unit="2 mm")[0] == "input.invalid"
    assert _fail(nominal="0,0", measured="0,0", tolerance="0.1 mm", bonus_modifier="X")[0] == "input.invalid"
    assert _fail(nominal="0,0", measured="0,0", tolerance="0.1 mm", feature_kind="pin")[0] == "input.invalid"


def test_shape_and_value_errors():
    family, msg = _fail(nominal="0,0; 1,1", measured="0,0", tolerance="0.1 mm")
    assert family == "input.invalid" and "rows" in msg
    assert _fail(nominal="0,0", measured="0,0,0", tolerance="0.1 mm")[0] == "input.invalid"
    assert _fail(nominal="0", measured="0", tolerance="0.1 mm")[0] == "input.invalid"  # one value per row
    family, msg = _fail(nominal="0,0", measured="nan,0", tolerance="0.1 mm")
    assert family == "input.invalid" and "finite" in msg and "row 1" in msg
    assert _fail(nominal="0,0; 1", measured="0,0; 1,1", tolerance="0.1 mm")[0] == "input.invalid"
    assert _fail(nominal="", measured="0,0", tolerance="0.1 mm")[0] == "input.missing"
    assert _fail(nominal="0,a", measured="0,0", tolerance="0.1 mm")[0] == "input.invalid"


def test_bonus_needs_its_inputs():
    base = dict(nominal="0,0", measured="0,0", tolerance="0.1 mm", bonus_modifier="M")
    assert _fail(**base, mmc_size="10 mm")[0] == "input.missing"  # no feature_size
    assert _fail(**base, feature_size="10.1")[0] == "input.missing"  # no mmc_size
    assert _fail(**base, feature_size="10.1; 10.2", mmc_size="10 mm")[0] == "input.invalid"  # count mismatch
    family, msg = _fail(**base, feature_size="10.1", mmc_size="10.2 mm", lmc_size="10 mm")
    assert family == "input.invalid" and "MMC size" in msg  # a hole's MMC is below its LMC


# --------------------------------------------------------------------------- pure module


def test_module_evaluate_matches_the_tool_arithmetic():
    res = position.evaluate(
        np.array([[0.0, 0.0]]), np.array([[0.03, 0.04]]), 0.1, modifier="M", kind="shaft", sizes=np.array([9.97]), mmc=10.0, lmc=9.9
    )
    assert res.error[0] == pytest.approx(0.1)
    assert res.bonus[0] == pytest.approx(0.03)
    assert res.allowed[0] == pytest.approx(0.13)
    assert res.within[0] and not res.beyond_limit[0] and not res.outside_size_limits[0]
