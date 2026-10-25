"""spice refusals about the netlist name the argument it came from: /netlist for text, /netlist_file for a path."""

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
async def test_check_netlist_control_character_points_at_netlist():
    result = await call("spice_check_netlist", netlist="R1 1 0 1k\x00")
    assert result["ok"] is False
    assert result["error"]["family"] == "input.invalid"
    assert result["error"]["details"]["param"] == "/netlist"
    assert result["error"]["details"]["reason_code"] == "control_character"


@pytest.mark.asyncio
async def test_check_netlist_lone_surrogate_points_at_netlist():
    result = await call("spice_check_netlist", netlist="* t\nR1 1 0 1k\ud800")
    assert result["ok"] is False
    assert result["error"]["details"]["param"] == "/netlist"


@pytest.mark.asyncio
async def test_check_netlist_nothing_given_is_missing_netlist():
    result = await call("spice_check_netlist", netlist="")
    assert result["ok"] is False
    assert result["error"]["family"] == "input.missing"
    assert result["error"]["details"]["param"] == "/netlist"


@pytest.mark.asyncio
async def test_check_netlist_a_file_with_a_bad_line_points_at_netlist_file(tmp_path):
    path = tmp_path / "bad.cir"
    path.write_text("* t\nR1 1 0 1k\x00\n", encoding="utf-8")
    result = await call("spice_check_netlist", netlist_file=str(path))
    assert result["ok"] is False
    assert result["error"]["details"]["param"] == "/netlist_file"


@pytest.mark.asyncio
async def test_simulate_a_netlist_without_elements_points_at_netlist():
    result = await call("spice_simulate", analysis="op", netlist="* nothing")
    assert result["ok"] is False
    assert result["error"]["family"] == "input.netlist_invalid"
    assert result["error"]["details"]["param"] == "/netlist"


@pytest.mark.asyncio
async def test_simulate_nothing_given_is_missing_netlist():
    result = await call("spice_simulate", analysis="op")
    assert result["ok"] is False
    assert result["error"]["family"] == "input.missing"
    assert result["error"]["details"]["param"] == "/netlist"
