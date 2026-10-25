"""geometry_make_part: a lone surrogate in the spec is a refusal that names /spec, not an encoding crash."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

BACKSLASH = chr(92)


async def make_part(spec: str):
    async with Client(gateway) as client:
        reply = await client.call_tool("geometry_make_part", {"spec": spec}, raise_on_error=False)
    payload = reply.structured_content
    assert payload is not None, reply.content
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", ['{"name":"a\ud800"}', "\ud800", "{" + '"features":[{"op":"box","name":"a\ud800"}]}'])
async def test_a_lone_surrogate_in_the_spec_text_points_at_spec(spec):
    result = await make_part(spec)
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid", result["error"]
    assert result["error"]["details"]["param"] == "/spec"
    assert "\ud800" not in result["error"]["message"]


@pytest.mark.asyncio
async def test_a_json_escaped_surrogate_is_still_a_refusal_not_a_crash():
    spec = '{"features":[{"op":"box","name":"a' + BACKSLASH + 'ud800","size":["10 mm","10 mm","10 mm"]}]}'
    result = await make_part(spec)
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid", result["error"]
    assert result["error"]["details"]["param"] == "/features/0/name"
