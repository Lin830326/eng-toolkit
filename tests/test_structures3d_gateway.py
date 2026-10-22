"""Real MCP dispatch, schema quality and adversarial input contracts."""

import json
import time

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.structures3d_testing import FRAME, validate


PLATE = dict(length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa")
FOUNDATION = dict(length="2 m", flexural_rigidity="200000 N*m^2", foundation_stiffness="100000 N/m^2", uniform_load="100 N/m", elements=10)
CASES = [
    ("structures3d_static", {**FRAME, "loads": "b,0 N,100 N,0 N,0 N*m,0 N*m,0 N*m"}),
    ("structures3d_modal", {**FRAME, "density": "7800 kg/m^3"}),
    ("structures3d_buckling", {**FRAME, "loads": "b,-1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m"}),
    ("structures3d_plate", {**PLATE, "pressure": "1 kPa"}),
    ("structures3d_plate_stability", PLATE),
    ("structures3d_foundation", FOUNDATION),
]


async def mcp(client, name, args):
    response = await client.call_tool(name, args, raise_on_error=False)
    assert not response.is_error, (name, args, response.content)
    for block in response.content:
        if block.type == "text":
            json.loads(block.text, parse_constant=lambda value: pytest.fail("nonfinite JSON: " + value))
    assert response.structured_content is not None
    return validate(response.structured_content)


async def test_gateway_lists_exactly_six_tools_unique_specific_descriptions():
    domain = next(d for d in all_domains() if d.name == "structures3d")
    assert domain.engines == ("numpy", "pint")
    assert {"structures3d_"+name for name in domain.tools} == {name for name, _ in CASES}
    async with Client(gateway) as client:
        discovered = {t.name: t for t in await client.list_tools() if t.name.startswith("structures3d_")}
    assert set(discovered) == {name for name, _ in CASES}
    for tool in discovered.values():
        assert 40 < len(tool.description) < 650
        descriptions = []
        for name, prop in tool.input_schema["properties"].items():
            assert prop["type"] in ("string", "number", "integer", "boolean")
            assert prop.get("description"), (tool.name, name)
            assert len(prop["description"]) <= 150, (tool.name, name, prop["description"])
            descriptions.append(prop["description"])
        # More stringent than quantity-only: no two parameters may share a description.
        assert len(set(descriptions)) == len(descriptions), tool.name


@pytest.mark.parametrize("name,args", CASES)
async def test_gateway_success_and_provenance(name, args):
    async with Client(gateway) as client:
        result = await mcp(client, name, args)
    assert result.ok, result.error
    assert result.provenance.engine_versions["numpy"]
    assert result.provenance.engine_versions["pint"]


@pytest.mark.parametrize("name,args", CASES)
async def test_gateway_missing_required_inputs(name, args):
    async with Client(gateway) as client:
        assert not (await mcp(client, name, {})).ok


SCALARS = [
    ("structures3d_modal", CASES[1][1], "density", "kg/m^3"),
    ("structures3d_plate", PLATE, "length", "m"),
    ("structures3d_plate", PLATE, "width", "m"),
    ("structures3d_plate", PLATE, "thickness", "m"),
    ("structures3d_plate", PLATE, "youngs_modulus", "Pa"),
    ("structures3d_plate_stability", PLATE, "length", "m"),
    ("structures3d_plate_stability", PLATE, "thickness", "m"),
    ("structures3d_plate_stability", PLATE, "youngs_modulus", "Pa"),
    ("structures3d_foundation", FOUNDATION, "length", "m"),
    ("structures3d_foundation", FOUNDATION, "flexural_rigidity", "N*m^2"),
    ("structures3d_foundation", FOUNDATION, "foundation_stiffness", "N/m^2"),
]


@pytest.mark.parametrize("name,args,param,unit", SCALARS)
async def test_quantity_rejections(name, args, param, unit):
    async with Client(gateway) as client:
        for text in ("0 "+unit, "-1 "+unit, "1e308 "+unit, "NaN "+unit, "inf "+unit, "1e-400 "+unit, "1 kg", "3", "abs(2) "+unit):
            result = await mcp(client, name, {**args, param: text})
            assert not result.ok, (param, text)
            assert result.error.details["param"] == "/"+param
            if text.startswith(("1e308", "1e-400")):
                assert result.error.family == "input.out_of_range"


@pytest.mark.parametrize("name,args", CASES)
async def test_hostile_6000_characters_finishes_under_one_second(name, args):
    param = "nodes" if "nodes" in args else "length"
    async with Client(gateway) as client:
        await client.list_tools()
        start = time.perf_counter()
        result = await mcp(client, name, {**args, param: "("*6000})
        elapsed = time.perf_counter()-start
    assert not result.ok
    assert elapsed < 1., (name, elapsed)


FRAME_REFUSALS = [
    {"supports": ""}, {"supports": "a,pinned"}, {"supports": "a,fixed;a,pinned"},
    {"supports": "a,spring"}, {"supports": "a,settlement"}, {"supports": "a,spring,ux,0 N/m"},
    {"supports": "a,settlement,ux,1 m,2 m"}, {"supports": "a,ux+bad"},
    {"nodes": "a,0 m,0 m,0 m;a,2 m,0 m,0 m"},
    {"nodes": "a,0 m,0 m,0 m;b,0 m,0 m,0 m"},
    {"nodes": FRAME["nodes"]+";unused,0 m,1 m,0 m"},
    {"nodes": "a,0 m,0 m,0 m;b,NaN m,0 m,0 m"},
    {"nodes": "a,0 m,0 m,0 m;b,1 kg,0 m,0 m"},
    {"nodes": "a,0 m,0 m,0 m;b,1e308 m,0 m,0 m"},
    {"nodes": "a,0 m,0 m,0 m;b,abs(2) m,0 m,0 m"},
    {"members": FRAME["members"]+";"+FRAME["members"]},
    {"members": FRAME["members"].replace(",b,", ",missing,")},
    {"members": FRAME["members"].replace("200 GPa", "1e308 GPa")},
    {"members": FRAME["members"].replace("200 GPa", "1e-300 yPa")},
    {"members": FRAME["members"].replace("1000 mm^2", "0 mm^2")},
    {"members": FRAME["members"].replace("1000 mm^2", "-1 mm^2")},
    {"members": FRAME["members"]+",0 rad", "orientations": "e,roll,10 deg"},
    {"orientations": "e,vector,1,0,0"}, {"orientations": "e,vector,0,0,0"},
    {"orientations": "missing,roll,10 deg"}, {"orientations": "e,vector,1e308,0,1"},
    {"orientations": "e,vector,1e-400,1,0"}, {"orientations": "e,vector,0,1,0;e,roll,10 deg"},
    {"loads": "missing,0 N,0 N,0 N,0 N*m,0 N*m,0 N*m"},
    {"loads": "b,1e308 N,0 N,0 N,0 N*m,0 N*m,0 N*m"},
    {"member_loads": "missing,uniform,local,0 N/m,1 N/m,0 N/m"},
    {"member_loads": "e,point,local,3 m,0 N,1 N,0 N"},
    {"member_loads": "e,uniform,bad,0 N/m,1 N/m,0 N/m"},
    {"releases": "e,start,ux"}, {"releases": "e,end,ry;e,end,ry"},
    {"releases": "e,start,rx;e,end,rx"},
    {"releases": "e,end,rz", "loads": "b,0 N,0 N,0 N,0 N*m,0 N*m,1 N*m"},
    {"shear_properties": "e,0 mm^2,1 mm^2"}, {"shear_properties": "missing,1 mm^2,1 mm^2"},
    {"thermal": "e,12e-6 1/K,1e308 K"}, {"thermal": "e,12e-6 1/K,1 K;e,12e-6 1/K,1 K"},
]


@pytest.mark.parametrize("changes", FRAME_REFUSALS)
async def test_gateway_frame_record_refusals(changes):
    async with Client(gateway) as client:
        result = await mcp(client, "structures3d_static", {**FRAME, **changes})
    assert not result.ok, changes


@pytest.mark.parametrize("unit", ["degC", "celsius", "degree_Celsius", "degF", "degree_Fahrenheit", "°C"])
async def test_offset_temperature_aliases_refused(unit):
    async with Client(gateway) as client:
        result = await mcp(client, "structures3d_static", {**FRAME, "thermal": f"e,12e-6 1/K,20 {unit}"})
    assert not result.ok
    assert result.error.details["param"] == "/thermal/0/2"


@pytest.mark.parametrize("name,args,param,text", [
    ("structures3d_static", FRAME, "nodes", ";".join(f"n{i},{i} m,0 m,0 m" for i in range(201))),
    ("structures3d_static", FRAME, "members", ";".join(f"e{i},a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4" for i in range(401))),
    ("structures3d_static", FRAME, "loads", ";".join("b,0 N,0 N,0 N,0 N*m,0 N*m,0 N*m" for _ in range(801))),
    ("structures3d_static", FRAME, "nodes", "x"*60001),
    ("structures3d_modal", CASES[1][1], "elements_per_member", 21),
    ("structures3d_buckling", CASES[2][1], "modes_count", 31),
    ("structures3d_plate", PLATE, "terms", 122),
    ("structures3d_plate", PLATE, "point_loads", ";".join("0.5 m,0.5 m,1 N" for _ in range(11))),
    ("structures3d_plate_stability", PLATE, "series_terms", 201),
    ("structures3d_foundation", FOUNDATION, "elements", 201),
    ("structures3d_foundation", FOUNDATION, "point_loads", ";".join("1 m,1 N,0 N*m" for _ in range(101))),
], ids=["nodes", "members", "loads", "text-length", "modal-subdivisions", "buckling-modes", "plate-terms", "plate-points", "plate-search", "foundation-mesh", "foundation-points"])
async def test_gateway_resource_caps(name, args, param, text):
    async with Client(gateway) as client:
        result = await mcp(client, name, {**args, param: text})
    assert not result.ok
    assert result.error.family == "input.out_of_range"


@pytest.mark.parametrize("name,args,changes", [
    (CASES[1][0], CASES[1][1], {"mass_per_length": "2 kg/m"}),
    (CASES[1][0], CASES[1][1], {"member_masses": "missing,2 kg/m"}),
    (CASES[1][0], CASES[1][1], {"nodal_masses": "missing,2 kg,1 kg*m^2"}),
    (CASES[1][0], CASES[1][1], {"nodal_masses": "b,1e308 kg,1 kg*m^2"}),
    (CASES[2][0], CASES[2][1], {"loads": "b,1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m"}),
    (CASES[2][0], CASES[2][1], {"supports": ""}),
    (CASES[3][0], CASES[3][1], {"thickness": "100 mm"}),
    (CASES[3][0], CASES[3][1], {"point_loads": "2 m,0.5 m,100 N"}),
    (CASES[3][0], CASES[3][1], {"method": "levy", "point_loads": "0.5 m,0.5 m,100 N"}),
    (CASES[4][0], CASES[4][1], {"analysis": "vibration"}),
    (CASES[4][0], CASES[4][1], {"edges": "SCSC"}),
    (CASES[4][0], CASES[4][1], {"analysis": "shear", "length": "0.5 m"}),
    (CASES[5][0], CASES[5][1], {"point_loads": "3 m,100 N,0 N*m"}),
    (CASES[5][0], CASES[5][1], {"point_loads": "1 m,1 kg,0 N*m"}),
    (CASES[5][0], CASES[5][1], {"foundation_model": "tension-free"}),
])
async def test_gateway_conflicts_and_applicability(name, args, changes):
    async with Client(gateway) as client:
        assert not (await mcp(client, name, {**args, **changes})).ok
