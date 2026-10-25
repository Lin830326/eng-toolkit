"""Every metrology_stackup_1d refusal about the chain names the argument: details.param is a JSON pointer starting at /chain."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway


async def stackup(**arguments):
    async with Client(gateway) as client:
        reply = await client.call_tool("metrology_stackup_1d", arguments, raise_on_error=False)
    payload = reply.structured_content
    assert payload is not None, reply.content
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("chain", ["NaN", "[]", "not json", '{"a": 1}'])
async def test_a_chain_that_is_not_a_non_empty_array_points_at_chain(chain):
    result = await stackup(chain=chain)
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid"
    assert result["error"]["details"]["param"] == "/chain"


@pytest.mark.asyncio
async def test_too_many_links_points_at_chain():
    link = '{"nominal":"1 mm","tol":"0.01 mm","direction":"+"}'
    result = await stackup(chain="[" + ",".join([link] * 500) + "]")
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid"
    assert result["error"]["details"]["param"] == "/chain"


@pytest.mark.asyncio
async def test_a_refusal_inside_a_link_points_into_that_link():
    chain = '[{"nominal":"5 mm","tol":"0.1 mm","direction":"+"},{"nominal":"5 mm","direction":"+"}]'
    result = await stackup(chain=chain)
    assert result["ok"] is False
    details = result["error"]["details"]
    assert details["link_index"] == 1
    assert details["param"] == "/chain/1"


@pytest.mark.asyncio
async def test_a_unit_error_in_a_link_points_at_the_field():
    chain = '[{"nominal":"5","tol":"0.1 mm","direction":"+"}]'
    result = await stackup(chain=chain)
    assert result["ok"] is False
    assert result["error"]["details"]["param"] == "/chain/0/nominal"
