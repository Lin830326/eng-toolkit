"""digital refusals about the Verilog text name the argument the text came from."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway


async def call(tool: str, **arguments):
    async with Client(gateway) as client:
        reply = await client.call_tool(tool, arguments, raise_on_error=False)
    payload = reply.structured_content
    assert payload is not None, reply.content
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["module m;\x00endmodule", "module m;\x01endmodule"])
async def test_digital_check_control_character_points_at_source(text):
    result = await call("digital_check", source=text)
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid"
    assert result["error"]["details"]["param"] == "/source"
    assert result["error"]["details"]["file"] == "source"  # the existing details stay


@pytest.mark.asyncio
async def test_digital_check_forbidden_construct_points_at_source():
    result = await call("digital_check", source='`include "x.v"\nmodule m; endmodule')
    assert result["ok"] is False
    assert result["error"]["family"] == "input.forbidden_construct"
    assert result["error"]["details"]["param"] == "/source"


@pytest.mark.asyncio
async def test_digital_synthesize_control_character_points_at_source():
    result = await call("digital_synthesize", source="module m;\x00endmodule", top="m")
    assert result["ok"] is False
    assert result["error"]["details"]["param"] == "/source"


@pytest.mark.asyncio
async def test_digital_equivalent_control_character_points_at_the_right_source():
    good = "module m(input a, output y); assign y = a; endmodule"
    result = await call("digital_equivalent", source_a=good, source_b="module m;\x00endmodule", top_a="m", top_b="m")
    assert result["ok"] is False
    assert result["error"]["details"]["param"] == "/source_b"
