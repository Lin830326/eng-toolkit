"""Actual MCP discovery, schemas, contract refusals and resource bounds."""
import json
import time

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.electric_testing import validate
from timing import budget

CASES = [
    ("electric_transformer", dict(rating="20 kVA", primary_voltage="8000 V", secondary_voltage="240 V", open_circuit="8000 V,.214 A,400 W", short_circuit="489 V,2.5 A,240 W")),
    ("electric_induction_motor", dict(line_voltage="460 V", r1=".641 ohm", x1="1.106 ohm", xm="26.3 ohm", r2=".332 ohm", x2=".464 ohm", slip=.025)),
    ("electric_pm_dc_motor", dict(voltage="24 V", resistance="2 ohm", torque_constant=".1 N*m/A", torque=".6 N*m")),
    ("electric_power_flow", dict(line_voltage="400 V", real_power="12 kW", power_factor=.8)),
    ("electric_drive_sizing", dict(load_inertia="2 kg*m^2", distance="6 rad", move_time="4 s", acceleration_time="1 s")),
    ("electric_converter", dict(topology="buck", input_voltage="24 V", output_voltage="12 V", output_current="5 A", switching_frequency="20 kHz", target_current_ripple="2 A", output_capacitance="470 uF")),
]


async def mcp(client, name, args):
    reply = await client.call_tool(name, args, raise_on_error=False)
    assert not reply.is_error, (name, reply.content)
    for block in reply.content:
        if block.type == "text":
            json.loads(block.text, parse_constant=lambda x: pytest.fail("nonfinite JSON "+x))
    assert reply.structured_content is not None
    return validate(reply.structured_content)


async def test_six_tools_and_unique_specific_parameter_descriptions():
    domain = next(d for d in all_domains() if d.name == "electric")
    assert domain.engines == ("numpy", "pint")
    assert {"electric_"+t for t in domain.tools} == {name for name, _ in CASES}
    async with Client(gateway) as client:
        tools = [t for t in await client.list_tools() if t.name.startswith("electric_")]
    assert len(tools) == 6
    for tool in tools:
        assert 60 <= len(tool.description) <= 650, tool.name
        descriptions = []
        for name, schema in tool.input_schema["properties"].items():
            assert schema["type"] in ("string", "number", "integer", "boolean")
            assert 15 <= len(schema["description"]) <= 140, (tool.name, name, schema["description"])
            descriptions.append(schema["description"])
        # A repeated generic quantity description fails even when unit dimensions match.
        assert len(descriptions) == len(set(descriptions)), tool.name


@pytest.mark.parametrize("name,args", CASES)
async def test_gateway_success_and_saved_finite_artifact(name, args):
    async with Client(gateway) as client:
        result = await mcp(client, name, args)
    assert result.ok, result.error
    assert result.provenance.engine_versions["numpy"]
    assert result.provenance.engine_versions["pint"]


@pytest.mark.parametrize("name,args", CASES)
async def test_gateway_missing(name, args):
    async with Client(gateway) as client:
        result = await mcp(client, name, {})
    assert not result.ok


SCALARS = [(0, "primary_voltage", "V"), (1, "line_voltage", "V"), (2, "voltage", "V"),
           (3, "line_voltage", "V"), (4, "load_inertia", "kg*m^2"), (5, "input_voltage", "V")]


@pytest.mark.parametrize("index,param,unit", SCALARS)
async def test_gateway_invalid_quantities(index, param, unit):
    name, args = CASES[index]
    async with Client(gateway) as client:
        for raw in ("0 "+unit, "-1 "+unit, "1e308 "+unit, "1e-400 "+unit, "NaN "+unit, "1 s", "100", "abs(1) "+unit):
            result = await mcp(client, name, {**args, param: raw})
            assert not result.ok, (name, param, raw)
            assert result.error.details["param"] == "/"+param
            if raw.startswith(("1e308", "1e-400")):
                assert result.error.family == "input.out_of_range"


@pytest.mark.parametrize("index,param", [(0, "open_circuit"), (1, "line_voltage"), (2, "voltage"), (3, "phasors"), (4, "profile"), (5, "input_voltage")])
async def test_gateway_hostile_strings_under_one_second(index, param):
    name, args = CASES[index]
    if index == 3:
        args = {"mode": "components"}
    async with Client(gateway) as client:
        await client.list_tools()
        start = time.perf_counter()
        result = await mcp(client, name, {**args, param: "("*6000})
        elapsed = time.perf_counter()-start
    assert not result.ok
    assert elapsed < budget(1), (name, elapsed)


@pytest.mark.parametrize("unit", ["degC", "celsius", "degree_Celsius", "degF", "degree_Fahrenheit"])
async def test_gateway_offset_temperature_difference_refused(unit):
    name, args = CASES[2]
    async with Client(gateway) as client:
        result = await mcp(client, name, {**args, "thermal_resistance": "2 K/W", "allowed_temperature_rise": "30 "+unit})
    assert not result.ok
    assert result.error.details["param"] == "/allowed_temperature_rise"


@pytest.mark.parametrize("index,changes", [
    (0, {"rating": "20 kW"}), (0, {"open_circuit": "8000 V,.214 A,.4 kVA"}),
    (1, {"slip": 0, "load_torque": "1000000 N*m"}), (1, {"curve_points": 202}),
    (2, {"torque": "1000 N*m"}), (2, {"curve_points": 101}),
    (3, {"real_power": "12 kVA"}), (3, {"source": "power_pq", "reactive_power": "9 kW"}),
    (4, {"profile": ";".join(["1 s,0 rad/s,0 rad/s,0 N*m"]*101)}),
    (5, {"output_voltage": "48 V"}), (5, {"mode": "ccm", "inductance": "1 uH", "target_current_ripple": ""}),
])
async def test_gateway_physical_and_resource_refusals(index, changes):
    name, args = CASES[index]
    async with Client(gateway) as client:
        result = await mcp(client, name, {**args, **changes})
    assert not result.ok


async def test_pint_aliases_and_post_conversion_underflow():
    async with Client(gateway) as client:
        result = await mcp(client, "electric_power_flow", {"line_voltage": "1e-300 yV", "real_power": "1 W"})
    assert not result.ok and result.error.family == "input.out_of_range"
