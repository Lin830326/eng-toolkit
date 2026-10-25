"""mech.bearing_life checked against hand calculations."""

from eng_toolkit.errors import json_pointer
import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.mech.bearings import bearing_life

LBF_N = 4.4482216152605


def _call(**kwargs) -> ToolResult:
    fn = getattr(bearing_life, "fn", bearing_life)
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
        assert r.error.details.get("param") == json_pointer(param), r.error
    assert r.warnings == []
    return r


# Hand calculation: C = 30.7 kN, P = 5 kN, n = 1500 rpm, ball bearing (p = 3)
#   C/P = 6.14 ; L10 = 6.14^3 = 37.6996 * 6.14 = 231.4755 million revolutions
#   L10h = 1e6 * 231.4755 / (60 * 1500) = 231.4755e6 / 90 000 = 2571.95 h
# Roller bearing (p = 10/3): 6.14^(10/3) = 6.14^3 * 6.14^(1/3) = 231.4755 * 1.83115 = 423.865 Mrev
#   (1.83115^3 = 6.1400) ; L10h = 423.865e6 / 90 000 = 4709.61 h
BASE = dict(dynamic_load_rating="30.7 kN", equivalent_load="5 kN", speed="1500 rpm")


def test_ball_bearing_life_matches_hand_calc():
    r = _call(**BASE)
    assert r.ok, r.error
    assert r.value.unit == "hour"
    assert math.isclose(r.values["l10_million_revolutions"].quantity, 231.4755, rel_tol=1e-6)
    assert math.isclose(r.value.quantity, 2571.95, rel_tol=1e-6)
    assert math.isclose(r.values["load_ratio_p_over_c"].quantity, 5 / 30.7, rel_tol=1e-12)
    assert r.data["exponent"] == 3.0
    assert any("Basic rating life only" in w for w in r.warnings)
    assert "required_dynamic_rating" not in r.values


def test_roller_bearing_uses_exponent_ten_thirds():
    r = _call(**BASE, bearing_type="roller")
    assert r.ok, r.error
    assert math.isclose(r.values["l10_million_revolutions"].quantity, 423.865, rel_tol=1e-5)
    assert math.isclose(r.value.quantity, 4709.61, rel_tol=1e-5)
    assert math.isclose(r.data["exponent"], 10 / 3, rel_tol=1e-12)


def test_required_rating_for_a_target_life():
    # 20 000 h at 1500 rpm = 20 000 * 60 * 1500 / 1e6 = 1800 Mrev ; C_req = P * 1800^(1/3) = 5 * 12.1644 = 60.822 kN
    # (12.1644^3 = 1800.0) ; margin = 30.7 / 60.822 = 0.50475 -> does not reach the target
    r = _call(**BASE, required_life="20000 h")
    assert r.ok, r.error
    assert math.isclose(r.values["required_dynamic_rating"].quantity, 60.822, rel_tol=1e-5)
    assert math.isclose(r.values["rating_margin"].quantity, 0.50475, rel_tol=1e-4)
    assert any("does not reach the required L10 life" in w for w in r.warnings)
    # a big enough bearing reaches it: C = 70 kN -> margin 70 / 60.822 = 1.1509, no shortfall warning
    r = _call(dynamic_load_rating="70 kN", equivalent_load="5 kN", speed="1500 rpm", required_life="20000 h")
    assert math.isclose(r.values["rating_margin"].quantity, 70 / 60.822, rel_tol=1e-4)
    assert not any("does not reach" in w for w in r.warnings)
    r = _call(**BASE, required_life=f"{20000 / 24} day")
    assert math.isclose(r.values["required_dynamic_rating"].quantity, 60.822, rel_tol=1e-5)


@pytest.mark.parametrize("speed", ["25 Hz", "157.07963267949 rad/s", "1500 revolutions_per_minute", "1500 1/min"])
def test_speed_spellings_agree(speed):
    # 25 Hz is 25 revolutions per second = 1500 rpm (pint alone would read Hz as 1/s and rpm as 2 pi rad/min)
    r = _call(**{**BASE, "speed": speed})
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 2571.95, rel_tol=1e-6)


def test_force_unit_conversions():
    # 30.7 kN = 30 700 N = 6901.63 lbf
    r = _call(dynamic_load_rating=f"{30700 / LBF_N} lbf", equivalent_load="5000 N", speed="1500 rpm")
    assert math.isclose(r.value.quantity, 2571.95, rel_tol=1e-6)


def test_load_ratio_and_speed_warnings():
    # P = 20 kN -> P/C = 0.651 > 0.5
    assert any("above 0.5" in w for w in _call(**{**BASE, "equivalent_load": "20 kN"}).warnings)
    # P = 1 kN -> P/C = 0.0326 < 0.05
    assert any("below 0.05" in w for w in _call(**{**BASE, "equivalent_load": "1 kN"}).warnings)
    # n = 5 rpm
    assert any("below 10 rpm" in w for w in _call(**{**BASE, "speed": "5 rpm"}).warnings)
    # P = 1 kN at 10 rpm: L10h = 30.7^3 * 1e6 / 600 = 4.8e7 h
    r = _call(dynamic_load_rating="30.7 kN", equivalent_load="1 kN", speed="10 rpm")
    assert math.isclose(r.value.quantity, 48224071.67, rel_tol=1e-6)
    assert any("beyond any realistic service life" in w for w in r.warnings)
    # P above C: life below one million revolutions is reported, not refused
    r = _call(dynamic_load_rating="30.7 kN", equivalent_load="40 kN", speed="1500 rpm")
    assert r.ok and r.values["l10_million_revolutions"].quantity < 1.0


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"equivalent_load": "0 kN"}, "input.invalid", "equivalent_load"),
        ({"equivalent_load": "-5 kN"}, "input.invalid", "equivalent_load"),
        ({"equivalent_load": "5"}, "units.bare_number", "equivalent_load"),
        ({"equivalent_load": "5 mm"}, "units.dimension_mismatch", "equivalent_load"),
        ({"equivalent_load": "1e12 N"}, "input.out_of_range", "equivalent_load"),
        ({"dynamic_load_rating": "0 kN"}, "input.invalid", "dynamic_load_rating"),
        ({"dynamic_load_rating": "30.7 N*m"}, "units.dimension_mismatch", "dynamic_load_rating"),
        ({"dynamic_load_rating": "1e-9 N"}, "input.out_of_range", "dynamic_load_rating"),
        ({"speed": "0 rpm"}, "input.invalid", "speed"),
        ({"speed": "-1500 rpm"}, "input.invalid", "speed"),
        ({"speed": "1500"}, "units.bare_number", "speed"),
        ({"speed": "1500 m"}, "units.dimension_mismatch", "speed"),
        ({"speed": "1e9 rpm"}, "input.out_of_range", "speed"),
        ({"speed": "0.001 rpm"}, "input.out_of_range", "speed"),
        ({"speed": "fast"}, "units.parse_error", "speed"),
        ({"bearing_type": "needle"}, "input.invalid", "bearing_type"),
        ({"bearing_type": ""}, "input.invalid", "bearing_type"),
        ({"required_life": "0 h"}, "input.invalid", "required_life"),
        ({"required_life": "20000"}, "units.bare_number", "required_life"),
        ({"required_life": "20000 N"}, "units.dimension_mismatch", "required_life"),
        ({"required_life": "1e12 h"}, "input.out_of_range", "required_life"),
    ],
)
def test_bearing_life_invalid_inputs(override, family, param):
    _fails(family, param, **{**BASE, **override})
