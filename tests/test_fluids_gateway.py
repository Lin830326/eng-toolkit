"""Actual FastMCP discovery, per-parameter descriptions and hostile input refusals."""

import json
import time

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.fluids_testing import CHANNEL, EXTERNAL, NETWORK, NOZZLE, PUMP, SURGE, validate
from timing import budget

CASES = [("fluids_pipe_network", NETWORK), ("fluids_pump_system", PUMP),
         ("fluids_open_channel", CHANNEL), ("fluids_compressible", {"mach": 2}),
         ("fluids_surge", SURGE), ("fluids_external_flow", EXTERNAL)]


async def mcp(client, name, args):
    result = await client.call_tool(name, args, raise_on_error=False)
    assert not result.is_error, (name, args, result.content)
    for block in result.content:
        if block.type == "text":
            json.loads(block.text, parse_constant=lambda x: pytest.fail("nonfinite JSON: "+x))
    assert result.structured_content is not None
    return validate(result.structured_content)


async def test_gateway_lists_six_compact_tools_and_unique_quantity_descriptions():
    domain = next(d for d in all_domains() if d.name == "fluids")
    assert domain.engines == ("numpy", "pint")
    assert {"fluids_"+name for name in domain.tools} == {name for name, _ in CASES}
    async with Client(gateway) as client:
        tools = [t for t in await client.list_tools() if t.name.startswith("fluids_")]
    assert len(tools) == 6
    for tool in tools:
        assert 40 < len(tool.description) < 650
        descriptions = []
        for prop in tool.input_schema["properties"].values():
            assert prop["type"] in ("string", "number", "integer", "boolean")
            assert 8 < len(prop["description"]) <= 140
            if prop["type"] == "string":
                descriptions.append(prop["description"])
        # This is stricter than the requirement: every string parameter is unique, including quantities.
        assert len(descriptions) == len(set(descriptions)), tool.name


@pytest.mark.parametrize("name,args", CASES)
async def test_gateway_success_and_missing(name, args):
    async with Client(gateway) as client:
        result = await mcp(client, name, args)
        assert result.ok, result.error
        assert result.provenance.engine_versions["numpy"]
        assert result.provenance.engine_versions["pint"]
        assert not (await mcp(client, name, {})).ok


SCALARS = [("fluids_pipe_network", NETWORK, "density", "kg/m^3"),
           ("fluids_pump_system", PUMP, "dynamic_viscosity", "Pa*s"),
           ("fluids_open_channel", CHANNEL, "width", "m"),
           ("fluids_compressible", NOZZLE, "stagnation_temperature", "K"),
           ("fluids_surge", SURGE, "wall_thickness", "m"),
           ("fluids_external_flow", EXTERNAL, "length", "m")]


@pytest.mark.parametrize("name,args,param,unit", SCALARS)
async def test_bad_quantity_scalars(name, args, param, unit):
    async with Client(gateway) as client:
        for raw in ("0 "+unit, "-1 "+unit, "1e308 "+unit, "1e309 "+unit, "NaN "+unit, "inf "+unit,
                    "1e-400 "+unit, "1 kg", "25", "abs(2) "+unit, "__import__('os').system('x') "+unit,
                    "1 "+unit+"^99999999", "1 "+unit+" + 2 "+unit):
            r = await mcp(client, name, {**args, param: raw})
            assert not r.ok, (name, param, raw)
            assert r.error.details["param"] == "/"+param
            if raw.startswith(("1e308", "1e309", "1e-400")):
                assert r.error.family == "input.out_of_range"


@pytest.mark.parametrize("name,args,param", [("fluids_pipe_network", NETWORK, "nodes"), ("fluids_pump_system", PUMP, "pump_points"),
                                           ("fluids_open_channel", CHANNEL, "width"), ("fluids_compressible", NOZZLE, "gas_constant"),
                                           ("fluids_surge", SURGE, "locations"), ("fluids_external_flow", EXTERNAL, "length")])
async def test_6000_character_hostile_input_under_one_second(name, args, param):
    async with Client(gateway) as client:
        await client.list_tools()
        start = time.perf_counter()
        result = await mcp(client, name, {**args, param: "("*6000})
        elapsed = time.perf_counter()-start
        assert not result.ok
        assert elapsed < budget(1), (name, elapsed)


BAD = [
    ("fluids_pipe_network", {**NETWORK, "nodes": "r,0 m,demand,0 L/s;j,0 m,demand,1 L/s"}),
    ("fluids_pipe_network", {**NETWORK, "nodes": NETWORK["nodes"]+";lost,0 m,head,10 m"}),
    ("fluids_pipe_network", {**NETWORK, "nodes": NETWORK["nodes"]+";j,0 m,demand,0 L/s"}),
    ("fluids_pipe_network", {**NETWORK, "pipes": "p,r,missing,10 m,50 mm,0 mm,0"}),
    ("fluids_pipe_network", {**NETWORK, "pipes": "p,r,j,0 m,50 mm,0 mm,0"}),
    ("fluids_pipe_network", {**NETWORK, "pipes": "p,r,j,10 m,0 mm,0 mm,0"}),
    ("fluids_pipe_network", {**NETWORK, "pipes": "p,r,j,10 m,50 mm,1e308 mm,0"}),
    ("fluids_pipe_network", {**NETWORK, "valves": "p,closed,0"}),
    ("fluids_pipe_network", {**NETWORK, "valves": "p,open,0;p,open,0"}),
    ("fluids_pipe_network", {**NETWORK, "max_iterations": 1}),
    ("fluids_pipe_network", {**NETWORK, "pipes": "p,r,j,10 m,50 mm,130,0", "headloss_model": "hazen_williams", "density": "800 kg/m^3"}),
    ("fluids_pipe_network", {"nodes": "r,0 m,head,0 m;j,0 m,demand,0.1 L/s", "pumps": "p,r,j,poly,20 m,0 s/m^2,0 s^2/m^5,yes"}),
    ("fluids_pipe_network", {"nodes": "r,0 m,head,0 m;j,0 m,head,30 m", "pumps": "p,r,j,poly,20 m,1 s/m^2,0 s^2/m^5,no"}),
    ("fluids_pump_system", {**PUMP, "static_head": "100 m"}),
    ("fluids_pump_system", {**PUMP, "diameter_ratio": 1.1}),
    ("fluids_pump_system", {**PUMP, "pipe_length": "1 m"}),
    ("fluids_pump_system", {**PUMP, "pump_points": "p,0 m^3/s,0 m,0.8;p,1 m^3/s,20 m,0.8"}),
    ("fluids_pump_system", {**PUMP, "pump_points": "p,0 m^3/s,20 m,0.6;p,1 m^3/s,0 m,0"}),
    ("fluids_pump_system", {**PUMP, "vapour_pressure": "2 kPa"}),
    ("fluids_open_channel", {**CHANNEL, "bed_slope": 0}),
    ("fluids_open_channel", {**CHANNEL, "bed_slope": -.01}),
    ("fluids_open_channel", {**CHANNEL, "depth": "0 m"}),
    ("fluids_open_channel", {"shape": "circular", "diameter": "1 m", "mode": "uniform", "depth": "1 m"}),
    ("fluids_open_channel", {"shape": "circular", "diameter": "1 m", "discharge": "100 m^3/s"}),
    ("fluids_open_channel", {"shape": "triangular", "discharge": "1 m^3/s"}),
    ("fluids_open_channel", {**CHANNEL, "mode": "uniform", "depth": "1 m"}),
    ("fluids_compressible", {"mode": "normal_shock", "mach": .9}),
    ("fluids_compressible", {"mode": "oblique_shock", "mach": 2, "branch": "weak", "deflection": "40 deg"}),
    ("fluids_compressible", {"mode": "expansion", "mach": 2, "deflection": "170 deg"}),
    ("fluids_compressible", {"mach": 2, "area_ratio_input": 2}),
    ("fluids_compressible", {"area_ratio_input": .9}),
    ("fluids_compressible", {"mach": 2, "gamma": 1}),
    ("fluids_compressible", {**NOZZLE, "back_pressure": "3 bar"}),
    ("fluids_compressible", {**NOZZLE, "stagnation_temperature": "-274 degC"}),
    ("fluids_compressible", {**NOZZLE, "gas_constant": "287 J/kg/celsius"}),
    ("fluids_surge", {**SURGE, "wall_thickness": "100 mm"}),
    ("fluids_surge", {**SURGE, "duration": "5 s", "reservoir_head": "200 m", "time_step": "1 s"}),
    ("fluids_external_flow", {**EXTERNAL, "velocity": "1e5 m/s"}),
    ("fluids_external_flow", {**EXTERNAL, "shape": "cylinder", "area": "1 m^2", "velocity": "1e-6 m/s"}),
    ("fluids_external_flow", {"shape": "flat_plate", "length": "1 m", "velocity": "1 m/s", "area": "1 m^2", "plate_regime": "laminar"}),
    ("fluids_external_flow", {**EXTERNAL, "kinematic_viscosity": "1 cSt"}),
    ("fluids_external_flow", {**EXTERNAL, "dynamic_viscosity": ""}),
    ("fluids_external_flow", {**EXTERNAL, "length": "1e-300 ym"}),
    ("fluids_compressible", {**NOZZLE, "back_pressure": "1e-300 yPa"}),
]


@pytest.mark.parametrize("name,args", BAD)
async def test_gateway_invalid_model(name, args):
    async with Client(gateway) as client:
        r = await mcp(client, name, args)
    assert not r.ok, (name, args)
    if "1e-300" in str(args):
        assert r.error.family == "input.out_of_range"


@pytest.mark.parametrize("name,args,param,value", [
    ("fluids_pipe_network", NETWORK, "nodes", ";".join(f"n{i},0 m,head,0 m" for i in range(201))),
    ("fluids_pipe_network", NETWORK, "pipes", ";".join(f"p{i},r,j,10 m,50 mm,0 mm,0" for i in range(401))),
    ("fluids_pipe_network", NETWORK, "pumps", ";".join(f"p{i},r,j,poly,20 m,1 s/m^2,0 s^2/m^5,yes" for i in range(41))),
    ("fluids_pump_system", PUMP, "pump_points", ";".join("p,0 m^3/s,20 m,0.6" for _ in range(121))),
    ("fluids_pipe_network", NETWORK, "nodes", "x"*60001),
    ("fluids_surge", {**SURGE, "duration": "100000 s", "reservoir_head": "200 m"}, "reaches", 200),
    ("fluids_open_channel", CHANNEL, "profile_steps", 501),
], ids=["nodes", "pipes", "pumps", "pump-points", "text-length", "moc-work", "profile-steps"])
async def test_gateway_resource_caps(name, args, param, value):
    async with Client(gateway) as client:
        r = await mcp(client, name, {**args, param: value})
    assert not r.ok
    assert r.error.family == "input.out_of_range"
