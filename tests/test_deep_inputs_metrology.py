"""A number with more digits than Python converts (4300) inside caller text is a refusal naming the argument, not engine.failure."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

LINK = '{"name":"a","nominal":"5 mm","tol":"0.1 mm","direction":"+"}'


async def call(tool: str, args: dict) -> dict:
    async with Client(gateway) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return result.structured_content


def refused(payload: dict, family, param: str) -> None:
    assert payload["ok"] is False, payload
    assert payload["error"]["family"] in ((family,) if isinstance(family, str) else family), payload["error"]
    assert payload["error"]["details"]["param"] == param, payload["error"]
    assert len(payload["error"]["message"]) < 400


@pytest.mark.parametrize(
    "chain",
    [
        "9" * 200_000,
        "[" + "9" * 200_000 + "]",
        '[{"name":"a","nominal":' + "9" * 200_000 + ',"tol":"0.1 mm","direction":"+"}]',
        "[" + LINK + ',{"name":"b","nominal":"1 mm","tol":' + "9" * 5_000 + ',"direction":"-"}]',
    ],
    ids=["bare-number", "list-of-number", "link-nominal", "link-tol-5000"],
)
async def test_stackup_chain_with_a_huge_digit_string_is_out_of_range(chain):
    refused(await call("metrology_stackup_1d", {"chain": chain}), ("input.out_of_range", "input.invalid"), "/chain")


async def test_stackup_link_quantity_text_with_200000_digits_names_the_field():
    chain = '[{"name":"a","nominal":"' + "9" * 200_000 + ' mm","tol":"0.1 mm","direction":"+"}]'
    refused(await call("metrology_stackup_1d", {"chain": chain}), "input.invalid", "/chain/0/nominal")


@pytest.mark.parametrize("points", ["[[1,2," + "9" * 5_000 + "]]", "[" + "9" * 5_000 + "]"], ids=["row", "flat"])
async def test_form_error_points_with_a_huge_digit_string_is_out_of_range(points):
    payload = await call("metrology_form_error", {"feature": "flatness", "points": points, "unit": "mm"})
    refused(payload, ("input.out_of_range", "input.invalid"), "/points")
