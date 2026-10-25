"""A JSON integer too large for a float ends as a refusal naming the argument, never an OverflowError (engine.failure)."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway
from eng_toolkit.units import UnitsError, parse_quantity

HUGE = 10**400  # a 401-digit JSON integer


async def call(tool: str, args: dict) -> dict:
    async with Client(gateway) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return result.structured_content


def refused(payload: dict, family: str, param: str) -> None:
    assert payload["ok"] is False, payload
    assert payload["error"]["family"] == family, payload["error"]
    assert payload["error"]["details"]["param"] == param, payload["error"]


@pytest.mark.parametrize("value", [HUGE, -HUGE])
async def test_control_response_samples_beyond_float_is_out_of_range(value):
    payload = await call("control_response", {"numerator": "[1]", "denominator": "[1, 1]", "samples": value})
    refused(payload, "input.out_of_range", "/samples")


@pytest.mark.parametrize("value", [HUGE, -HUGE])
async def test_control_frequency_points_beyond_float_is_out_of_range(value):
    payload = await call("control_frequency", {"numerator": "[1]", "denominator": "[1, 1]", "points": value})
    refused(payload, "input.out_of_range", "/points")


async def test_a_polynomial_coefficient_beyond_float_is_out_of_range():
    payload = await call("control_response", {"numerator": "[" + "9" * 400 + "]", "denominator": "[1, 1]"})
    assert payload["ok"] is False and payload["error"]["family"].startswith("input."), payload["error"]
    assert payload["error"]["details"]["param"] == "/numerator"


@pytest.mark.parametrize("raw", [{"value": HUGE, "unit": "m"}, "9" * 390 + " m"])
def test_a_quantity_beyond_float_is_out_of_range_not_an_overflow(raw):
    with pytest.raises(UnitsError) as caught:
        parse_quantity(raw, name="length", dimension="length")
    assert caught.value.family == "input.out_of_range"
    assert caught.value.details["param"] == "/length"
