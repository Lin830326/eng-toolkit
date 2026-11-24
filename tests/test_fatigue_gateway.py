"""Actual MCP schemas, successes and structured refusals for all five tools."""

import json
import math
import time

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.fatigue_testing import FM, MT, RF, SL, SN, close, validate, v
from timing import budget

CASES=[("fatigue_sn",SN),("fatigue_rainflow",RF),("fatigue_strain_life",{**SL,"strain_amplitude":"0.005 dimensionless"}),
       ("fatigue_fracture",FM),("fatigue_material",MT)]


async def mcp(client,name,args):
    r=await client.call_tool(name,args,raise_on_error=False)
    assert not r.is_error,(name,args,r.content)
    assert r.structured_content is not None
    return validate(r.structured_content)


async def test_gateway_lists_five_described_tools_and_unique_parameters():
    domain=next(d for d in all_domains() if d.name=="fatigue")
    assert domain.engines==("numpy","pint")
    async with Client(gateway) as client:
        tools=[t for t in await client.list_tools() if t.name.startswith("fatigue_")]
    assert {t.name for t in tools}=={n for n,_ in CASES}
    for tool in tools:
        assert 40<len(tool.description)<650
        seen=set()
        for name,prop in tool.input_schema["properties"].items():
            assert prop["type"] in ("string","number","integer","boolean")
            desc=prop["description"]
            assert 8<len(desc)<=155,(tool.name,name,len(desc))
            # Stronger than quantity-only requirement: every parameter description is unique within a tool.
            assert desc not in seen,(tool.name,name,desc)
            seen.add(desc)


@pytest.mark.parametrize("name,args",CASES)
async def test_gateway_success_and_missing_input(name,args):
    async with Client(gateway) as client:
        r=await mcp(client,name,args)
        assert r.ok,r.error
        assert r.provenance.engine_versions["numpy"] and r.provenance.engine_versions["pint"]
        assert not (await mcp(client,name,{})).ok


SCALARS=[("fatigue_sn",dict(mode="curve",basquin_a="1000 MPa"),"basquin_a","MPa"),
         ("fatigue_rainflow",RF,"sample_scale","MPa"),
         ("fatigue_strain_life",{**SL,"strain_amplitude":".005 dimensionless"},"elastic_modulus","MPa"),
         ("fatigue_fracture",FM,"crack_size","mm"),
         ("fatigue_material",MT,"stress","MPa")]


@pytest.mark.parametrize("name,args,param,unit",SCALARS)
async def test_bad_quantity_contract(name,args,param,unit):
    async with Client(gateway) as client:
        for text in ("0 "+unit,"-1 "+unit,"1e308 "+unit,"1e309 "+unit,"NaN "+unit,"inf "+unit,
                     "1e-400 "+unit,"1 kg","100","abs(2) "+unit,"1e-300 yPa"):
            r=await mcp(client,name,{**args,param:text})
            assert not r.ok,(param,text)
            assert r.error.details["param"]=="/"+param
            if text.startswith(("1e308","1e309","1e-400")):
                assert r.error.family=="input.out_of_range"


@pytest.mark.parametrize("name,args,param",[("fatigue_sn",SN,"points"),("fatigue_rainflow",RF,"history"),
    ("fatigue_strain_life",{**SL,"strain_amplitude":".005 dimensionless"},"strain_amplitude"),
    ("fatigue_fracture",FM,"crack_size"),("fatigue_material",MT,"stress")])
async def test_hostile_6000_characters_under_second(name,args,param):
    async with Client(gateway) as client:
        await client.list_tools()
        start=time.perf_counter()
        r=await mcp(client,name,{**args,param:"("*6000})
        duration=time.perf_counter()-start
    assert not r.ok
    assert duration<budget(1),(name,duration)


@pytest.mark.parametrize("name,args",[
    ("fatigue_sn",dict(mode="fit",points="400 MPa,1000;200 MPa,1000000;")),
    ("fatigue_sn",dict(mode="fit",points="100 MPa,1000;"*2001)),
    ("fatigue_rainflow",{**RF,"history":"[0,"+"1,"*1000000+"0]"}),
    ("fatigue_rainflow",{**RF,"history":"[1]"}),
    ("fatigue_rainflow",{**RF,"history":"[1,1,1]"}),
    ("fatigue_fracture",{**FM,"geometry":"edge","width":"5 mm"}),
    ("fatigue_strain_life",{**SL,"strain_amplitude":"1e-12 dimensionless"}),
    ("fatigue_material",dict(mode="fit",points="100 MPa,0.01 dimensionless;110 MPa,0.02 dimensionless",fit_model="hollomon")),
])
async def test_gateway_refusals_and_caps(name,args):
    async with Client(gateway) as client:
        assert not (await mcp(client,name,args)).ok


async def test_marin_matches_public_machine_shaft_bending():
    # d=20 mm, bending100/50 N*m, Su600; both use same ka,kb,kc,kd,ke and Goodman proportional-load rule.
    d=20
    sa=32*100000/(math.pi*d**3)
    sm=32*50000/(math.pi*d**3)
    async with Client(gateway) as client:
        machine=await client.call_tool("machine_shaft",dict(outer_diameter="20 mm",ultimate_strength="600 MPa",
            alternating_bending="100 N*m",mean_bending="50 N*m",surface_finish="machined",reliability=99))
        assert machine.structured_content["ok"]
        fatigue=await mcp(client,"fatigue_sn",dict(mode="estimate",ultimate_strength="600 MPa",diameter="20 mm",
            alternating_stress=f"{sa:.15g} MPa",mean_stress=f"{sm:.15g} MPa",surface_finish="machined",reliability=99,mean_method="goodman"))
    assert fatigue.ok,fatigue.error
    close(v(fatigue,"endurance_limit"),machine.structured_content["values"]["endurance_limit"]["quantity"])
    close(v(fatigue,"safety_factor"),machine.structured_content["values"]["goodman_safety_factor"]["quantity"])
