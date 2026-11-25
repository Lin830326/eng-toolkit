"""Six actual MCP tools, including strict text and magnitude refusals."""

import json
import time

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.structures_testing import COLUMN, FRAME, STRESS, TRUSS, validate
from timing import budget

CASES = [
    ("structures_section",dict(width="100 mm",height="200 mm",at_height="100 mm",shear_force="10 kN")),
    ("structures_truss",TRUSS),
    ("structures_frame",dict(**FRAME,loads="b,0 N,-1000 N,0 N*m")),
    ("structures_modal",dict(**FRAME,density="7800 kg/m^3")),
    ("structures_column",COLUMN),
    ("structures_stress",STRESS),
]


async def mcp(client,name,args):
    result = await client.call_tool(name,args,raise_on_error=False)
    assert not result.is_error,(name,args,result.content)
    for block in result.content:
        if block.type == "text":
            json.loads(block.text,parse_constant=lambda x: pytest.fail("nonfinite JSON: "+x))
    assert result.structured_content is not None
    return validate(result.structured_content)


async def test_gateway_lists_six_tools_and_compact_descriptions():
    domain = next(d for d in all_domains() if d.name == "structures")
    assert domain.engines == ("numpy","pint")
    assert {"structures_"+name for name in domain.tools} == {name for name,_ in CASES}
    async with Client(gateway) as client:
        tools = {t.name:t for t in await client.list_tools() if t.name.startswith("structures_")}
    assert len(tools) == 6
    for tool in tools.values():
        assert 40 < len(tool.description) < 650
        for prop in tool.input_schema["properties"].values():
            assert prop["type"] in ("string","number","integer","boolean")
            assert prop["description"]


@pytest.mark.parametrize("name,args",CASES)
async def test_gateway_success(name,args):
    async with Client(gateway) as client:
        r = await mcp(client,name,args)
    assert r.ok,r.error
    assert r.provenance.engine_versions["numpy"]
    assert r.provenance.engine_versions["pint"]


@pytest.mark.parametrize("name,args",CASES)
async def test_gateway_missing_inputs(name,args):
    async with Client(gateway) as client:
        assert not (await mcp(client,name,{})).ok


SCALARS = [
    ("structures_section",CASES[0][1],"width","mm"),
    ("structures_column",COLUMN,"length","m"),
    ("structures_column",COLUMN,"youngs_modulus","Pa"),
    ("structures_column",COLUMN,"area","m^2"),
    ("structures_column",COLUMN,"inertia","m^4"),
    ("structures_column",COLUMN,"yield_strength","Pa"),
    ("structures_modal",CASES[3][1],"density","kg/m^3"),
]


@pytest.mark.parametrize("name,args,param,unit",SCALARS)
async def test_gateway_bad_physical_scalar(name,args,param,unit):
    async with Client(gateway) as client:
        for text in ("0 "+unit,"-1 "+unit,"1e308 "+unit,"NaN "+unit,"1e309 "+unit,"1e-400 "+unit,"1 kg","25","abs(2) "+unit):
            r = await mcp(client,name,{**args,param:text})
            assert not r.ok,(param,text)
            assert r.error.details["param"] == "/"+param
            if text.startswith("1e3"):
                assert r.error.family == "input.out_of_range"


@pytest.mark.parametrize("name,args,param",[("structures_section",CASES[0][1],"width"),("structures_truss",TRUSS,"nodes"),("structures_frame",FRAME,"members"),("structures_modal",CASES[3][1],"nodes"),("structures_column",COLUMN,"length"),("structures_stress",STRESS,"sigma_x")])
async def test_hostile_6000_character_input_under_one_second(name,args,param):
    async with Client(gateway) as client:
        # Warm MCP dispatch; measure parser plus runner call, not server setup/imports.
        await client.list_tools()
        start = time.perf_counter()
        r = await mcp(client,name,{**args,param:"("*6000})
        elapsed = time.perf_counter()-start
    assert not r.ok
    assert elapsed < budget(1.),(name,elapsed)


BAD_MODELS = [
    ("structures_frame",{**FRAME,"supports":""}),
    ("structures_frame",{**FRAME,"nodes":"a,0 m,0 m;a,2 m,0 m"}),
    ("structures_frame",{**FRAME,"nodes":"a,0 m,0 m;b,0 m,0 m"}),
    ("structures_frame",{**FRAME,"members":"e,a,missing,200 GPa,1000 mm^2,1e6 mm^4"}),
    ("structures_frame",{**FRAME,"members":FRAME["members"]+";"+FRAME["members"]}),
    ("structures_frame",{**FRAME,"loads":"missing,0 N,0 N,0 N*m"}),
    ("structures_frame",{**FRAME,"member_loads":"missing,uniform,local,0 N/m,1 N/m"}),
    ("structures_frame",{**FRAME,"member_loads":"e,point,local,3 m,0 N,1 N"}),
    ("structures_frame",{**FRAME,"member_loads":"e,uniform,bad,0 N/m,1 N/m"}),
    ("structures_frame",{**FRAME,"shear_properties":"e,80 GPa,0 mm^2"}),
    ("structures_frame",{**FRAME,"supports":"a,fixed;a,pinned"}),
    ("structures_frame",{**FRAME,"nodes":"a,1e308 m,0 m;b,2 m,0 m"}),
    ("structures_frame",{**FRAME,"nodes":"a,NaN m,0 m;b,2 m,0 m"}),
    ("structures_frame",{**FRAME,"nodes":"a,1 kg,0 m;b,2 m,0 m"}),
    ("structures_frame",{**FRAME,"nodes":"a,abs(1) m,0 m;b,2 m,0 m"}),
    ("structures_frame",{**FRAME,"members":"e,a,b,1e308 GPa,1000 mm^2,1e6 mm^4"}),
    ("structures_frame",{**FRAME,"loads":"b,1e308 N,0 N,0 N*m"}),
    ("structures_frame",{**FRAME,"member_loads":"e,uniform,local,0 N/m,1e308 N/m"}),
    ("structures_truss",{**TRUSS,"supports":"a,xy"}),
    ("structures_truss",{**TRUSS,"members":"e,a,missing,200 GPa,100 mm^2"}),
    ("structures_truss",{**TRUSS,"members":"e,a,b,200 GPa,-1 mm^2"}),
    ("structures_truss",{**TRUSS,"thermal":"e,1e308 K,0.00001 1/K"}),
    ("structures_truss",{**TRUSS,"supports":"a,xyz;b,y"}),
    ("structures_truss",{**TRUSS,"loads":"missing,1 N,0 N"}),
    ("structures_modal",{**FRAME,"density":"7800 kg/m^3","elements_per_member":21}),
    ("structures_modal",{**FRAME,"density":"7800 kg/m^3","member_masses":"missing,1 kg/m"}),
    ("structures_modal",{**FRAME,"density":"7800 kg/m^3","nodal_masses":"b,1e308 kg,0 kg*m^2"}),
    ("structures_stress",{**STRESS,"sigma_x":"1e308 Pa"}),
    ("structures_stress",{**STRESS,"tau_xy":"NaN Pa"}),
    ("structures_stress",{**STRESS,"tau_xy":"1e-400 Pa"}),
    ("structures_section",{"shape":"circle","diameter":"2 mm","width":"1 mm"}),
    ("structures_section",{"shape":"hollow_circle","diameter":"2 mm","thickness":"2 mm"}),
    ("structures_section",{"shape":"polygon","polygon":"0 m,0 m;1 m,1 m;0 m,1 m;1 m,0 m"}),
    ("structures_section",{"width":"1 m 2","height":"2 m"}),
    ("structures_section",{"width":"1 m^9999999999","height":"2 m"}),
    ("structures_column",{**COLUMN,"eccentricity":"1 mm","extreme_fibre":"20 mm","end_condition":"fixed-free"}),
]


@pytest.mark.parametrize("name,args",BAD_MODELS)
async def test_gateway_structural_refusals(name,args):
    async with Client(gateway) as client:
        assert not (await mcp(client,name,args)).ok


@pytest.mark.parametrize("name,args,param,text",[
    ("structures_frame",FRAME,"nodes",";".join(f"n{i},{i} m,0 m" for i in range(101))),
    ("structures_truss",TRUSS,"nodes",";".join(f"n{i},{i} m,0 m" for i in range(201))),
    ("structures_frame",FRAME,"members",";".join(f"e{i},a,b,200 GPa,1000 mm^2,1e6 mm^4" for i in range(201))),
    ("structures_truss",TRUSS,"members",";".join(f"e{i},a,b,200 GPa,100 mm^2" for i in range(401))),
    ("structures_frame",FRAME,"loads",";".join("b,0 N,0 N,0 N*m" for _ in range(401))),
    ("structures_section",{"shape":"polygon"},"polygon",";".join(f"{i} m,0 m" for i in range(201))),
    ("structures_frame",FRAME,"nodes","x"*60001),
], ids=["frame-nodes","truss-nodes","frame-members","truss-members","loads","polygon-vertices","text-length"])
async def test_gateway_caps(name,args,param,text):
    async with Client(gateway) as client:
        r = await mcp(client,name,{**args,param:text})
    assert not r.ok
    assert r.error.family == "input.out_of_range"
