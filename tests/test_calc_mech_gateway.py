"""The calc additions and the mech domain through a real MCP client against the gateway."""

import math

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.gateway.server import gateway

NEW_CALC = {"calc_orifice_flow", "calc_pump_power", "calc_accumulator_precharge", "calc_cylinder_speed"}
MECH = {"mech_bolt_check", "mech_bearing_life", "mech_spring_compression", "mech_beam_bending"}


def _as_result(call_result) -> ToolResult:
    payload = call_result.structured_content
    assert payload is not None
    return ToolResult.model_validate(payload)


@pytest.mark.asyncio
async def test_new_tools_are_listed_with_basic_parameter_types():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert (NEW_CALC | MECH) <= set(tools)
    assert {"calc_cylinder_force", "calc_pipe_pressure_drop"} <= set(tools)  # the existing two stay
    for name in NEW_CALC | MECH:
        schema = tools[name].input_schema
        for pname, prop in schema.get("properties", {}).items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname, prop)
            assert prop.get("description"), (name, pname)


@pytest.mark.asyncio
async def test_orifice_flow_over_mcp():
    async with Client(gateway) as client:
        r = _as_result(
            await client.call_tool(
                "calc_orifice_flow", {"diameter": "5 mm", "density": "870 kg/m^3", "pressure_drop": "100 bar"}
            )
        )
    assert r.ok
    assert math.isclose(r.value.quantity, 110.74603886, rel_tol=1e-9)  # L/min, see test_calc_components.py
    assert r.provenance.engine == "eng_toolkit.calc.hydraulics"
    assert r.provenance.engine_versions.get("pint")
    assert r.review_status == "unreviewed"


@pytest.mark.asyncio
async def test_mech_tools_over_mcp_including_a_structured_error():
    async with Client(gateway) as client:
        beam = _as_result(
            await client.call_tool(
                "mech_beam_bending",
                {"length": "4 m", "load": "10 kN/m", "load_type": "udl", "width": "50 mm", "height": "100 mm"},
            )
        )
        bolt = _as_result(await client.call_tool("mech_bolt_check", {"diameter": "10 mm", "torque": "40 N*m"}))
        bad = _as_result(await client.call_tool("mech_bearing_life", {"dynamic_load_rating": "30.7 kN", "equivalent_load": "5", "speed": "1500 rpm"}))
        json_string = _as_result(
            await client.call_tool(
                "mech_bearing_life",
                {"dynamic_load_rating": '{"value": 30.7, "unit": "kN"}', "equivalent_load": "5 kN", "speed": "1500 rpm"},
            )
        )
    assert beam.ok and math.isclose(beam.value.quantity, 240.0, rel_tol=1e-9)
    assert bolt.ok and math.isclose(bolt.values["preload"].quantity, 20.0, rel_tol=1e-12)
    assert bad.ok is False and bad.error.family == "units.bare_number" and bad.error.details["param"] == "equivalent_load"
    assert json_string.ok and math.isclose(json_string.value.quantity, 2571.95, rel_tol=1e-5)
    for r in (beam, bolt, bad, json_string):
        assert r.review_status == "unreviewed"
        assert r.artifacts and r.artifacts[0].sha256


@pytest.mark.asyncio
async def test_toolkit_lists_the_mech_domain():
    async with Client(gateway) as client:
        r = _as_result(await client.call_tool("toolkit_list_tools", {}))
    domains = {d["name"]: d for d in r.data["domains"]}
    assert MECH <= set(domains["mech"]["tools"])
    assert NEW_CALC <= set(domains["calc"]["tools"])
    assert [e["dist"] for e in domains["mech"]["engines"]] == ["pint"]
