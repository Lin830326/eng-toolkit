"""Absurd magnitudes must be refused, never turned into inf / NaN / a silently wrong zero.

A coordinate of 1e200 squared overflows to inf, which serialises as JSON null; an MCP client then rejects the whole result
(``None is not of type 'number'``) instead of receiving a ToolResult.  So both GD&T tools refuse values above
``form.MAX_ABS_MM`` (1e9 mm, 1000 km) as ``input.out_of_range``, in the tools and in the pure functions, while realistic data
(up to about 1e6 mm in the tests) keeps working.  The tests go through the gateway's MCP client because a direct call does
not show the serialisation failure.
"""

import json
import math

import numpy as np
import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology import form
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway

HUGE = ["1e150", "1e200", "1e308"]

# a small valid point set per feature, scaled by the huge factor in the tests
BASE = {
    "flatness": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0.01], [0.5, 0.5, 0]],
    "straightness": [[0, 0], [1, 0.01], [2, 0], [3, 0.01]],
    "circularity": [[1, 0], [0, 1], [-1, 0], [0, -1.01], [0.7, 0.7]],
    "cylindricity": [
        [math.cos(a), math.sin(a), z] for z in (0.0, 2.0) for a in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    ],
}


async def _call(name: str, args: dict) -> ToolResult:
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args)
    data = raw.structured_content if getattr(raw, "structured_content", None) else json.loads(raw.content[0].text)
    return ToolResult.model_validate(data)


def _scaled(feature: str, factor: float) -> str:
    top = max(abs(v) for row in BASE[feature] for v in row)  # keep the largest coordinate finite as a double
    return json.dumps([[v * min(factor, 1.7e308 / top) for v in row] for row in BASE[feature]])


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", sorted(BASE))
@pytest.mark.parametrize("huge", HUGE)
async def test_form_error_refuses_huge_coordinates(feature, huge):
    r = await _call("metrology_form_error", {"feature": feature, "points": _scaled(feature, float(huge))})
    assert not r.ok
    assert r.error.family == "input.out_of_range"
    assert "unit" in r.error.message  # tells the caller to check the unit


@pytest.mark.asyncio
async def test_form_error_refuses_coordinates_that_overflow_only_after_the_unit_is_applied():
    r = await _call(
        "metrology_form_error", {"feature": "circularity", "points": _scaled("circularity", 1e300), "unit": "inch"}
    )
    assert not r.ok and r.error.family == "input.out_of_range"
    r = await _call("metrology_form_error", {"feature": "circularity", "points": _scaled("circularity", 1.0), "unit": "parsec"})
    assert not r.ok and r.error.family == "input.out_of_range"


@pytest.mark.asyncio
async def test_form_error_refuses_a_huge_tolerance():
    r = await _call(
        "metrology_form_error",
        {"feature": "circularity", "points": _scaled("circularity", 1.0), "tolerance": "1e200 mm"},
    )
    assert not r.ok and r.error.family == "input.out_of_range"


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", sorted(BASE))
async def test_the_limit_itself_and_realistic_magnitudes_still_work(feature):
    for factor in (1.0, 1e3, 1e6):  # 1 m, 1 km worth of mm
        r = await _call("metrology_form_error", {"feature": feature, "points": _scaled(feature, factor)})
        assert r.ok, (feature, factor, r.error)
        assert math.isfinite(r.value.quantity)


@pytest.mark.asyncio
@pytest.mark.parametrize("huge", HUGE)
@pytest.mark.parametrize(
    "nominal, measured",
    [("{h},0", "-{h},0"), ("{h},{h}", "-{h},-{h}"), ("0,0,0", "{h},{h},{h}"), ("{h},0", "{h},0")],
)
async def test_position_refuses_huge_coordinates(huge, nominal, measured):
    args = {"nominal": nominal.format(h=huge), "measured": measured.format(h=huge), "tolerance": "0.1 mm"}
    r = await _call("metrology_position_tolerance", args)
    assert not r.ok
    assert r.error.family == "input.out_of_range"


@pytest.mark.asyncio
async def test_position_refuses_huge_tolerance_sizes_and_limits():
    base = {"nominal": "0,0", "measured": "0,0"}
    r = await _call("metrology_position_tolerance", {**base, "tolerance": "1e300 mm"})
    assert not r.ok and r.error.family == "input.out_of_range"
    for extra in ({"feature_size": "1e200"}, {"mmc_size": "1e200 mm"}, {"lmc_size": "1e200 mm"}):
        args = {**base, "tolerance": "0.1 mm", "bonus_modifier": "M", "feature_size": "10", "mmc_size": "10 mm", **extra}
        r = await _call("metrology_position_tolerance", args)
        assert not r.ok and r.error.family == "input.out_of_range", extra


@pytest.mark.asyncio
async def test_position_with_large_but_real_coordinates_serialises():
    r = await _call(
        "metrology_position_tolerance",
        {"nominal": "1000000,2000000", "measured": "1000000.03,2000000.04", "tolerance": "0.1 mm"},
    )
    assert r.ok
    assert math.isfinite(r.value.quantity) and r.value.quantity == pytest.approx(0.1, abs=1e-8)


@pytest.mark.parametrize("name", sorted(BASE))
def test_pure_functions_refuse_huge_points_too(name):
    fn = getattr(form, name)
    with pytest.raises(ToolFailure) as exc:
        fn(np.array(BASE[name], dtype=float) * 1e200)
    assert exc.value.family == "input.out_of_range"


def test_pure_functions_refuse_too_few_points_when_called_directly():
    three = np.array([[1.0, 0], [0, 1], [-1, 0]])
    with pytest.raises(ToolFailure) as exc:
        form.circularity(three)
    assert exc.value.family == "input.too_few_points"
    with pytest.raises(ToolFailure):
        form.flatness(np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]))
    with pytest.raises(ToolFailure):
        form.straightness(np.array([[0.0, 0], [1, 0]]))
