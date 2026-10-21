"""The six machine tools through the real FastMCP gateway, including hostile inputs."""

import json
import re

import pytest
from fastmcp import Client

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.machine_testing import validate

SHAFT=dict(outer_diameter="20 mm",inner_diameter="10 mm",bending_moment="100 N*m",torque="50 N*m",yield_strength="400 MPa",ultimate_strength="600 MPa",alternating_bending="100 N*m",mean_bending="10 N*m",alternating_torque="10 N*m",mean_torque="50 N*m",length="1 m",shear_modulus="80 GPa",static_deflections="1 mm; 3 mm")
GEAR=dict(teeth_1=20,teeth_2=40,module="2 mm",face_width="20 mm",torque="100 N*m",speed="600 rpm",youngs_modulus_1="200 GPa",youngs_modulus_2="200 GPa",allowable_bending="100 MPa",allowable_contact="1000 MPa")
WELD=dict(segments_text="(0 mm,0 mm)-(100 mm,0 mm); (0 mm,60 mm)-(100 mm,60 mm)",leg_size="5 mm",force_x="100 N",force_y="1000 N",force_z="100 N",load_x="100 mm",load_y="50 mm",load_z="10 mm",point_x="100 mm",point_y="60 mm",allowable_shear="100 MPa")
VESSEL=dict(geometry="cylinder",inner_radius="50 mm",thickness="50 mm",internal_pressure="30 MPa",external_pressure="1 MPa",radius="75 mm",youngs_modulus="200 GPa",yield_strength="200 MPa",allowable_stress="60 MPa")
BELT=dict(small_diameter="100 mm",large_diameter="200 mm",centre_distance="500 mm",belt_type="v",groove_angle="40 deg",speed="600 rpm",mass_per_length="0.2 kg/m",power="1 hp",maximum_tension="1 kN")
KEY=dict(joint_type="key",length="50 mm",shaft_diameter="40 mm",width="10 mm",height="8 mm",torque="100 N*m",yield_strength="200 MPa",shear_strength="100 MPa",bearing_strength="200 MPa")
SPLINE=dict(joint_type="involute_spline",length="20 mm",mean_radius="25 mm",tooth_height="3 mm",tooth_count=10,allowable_pressure="100 MPa",torque="750 N*m")
CASES=[("machine_shaft",SHAFT),("machine_gear",GEAR),("machine_weld",WELD),("machine_pressure_vessel",VESSEL),("machine_belt",BELT),("machine_key",KEY),("machine_key",SPLINE)]
NAMES={name for name,_ in CASES}


async def mcp(client,name,args):
    result=await client.call_tool(name,args,raise_on_error=False)
    assert not result.is_error,(name,args,result.content)
    for block in result.content:
        if block.type=="text":
            json.loads(block.text,parse_constant=lambda x: pytest.fail(f"nonfinite JSON: {x}"))
    assert result.structured_content is not None
    return validate(result.structured_content)


def test_discovered_domain_registry():
    domain=next(d for d in all_domains() if d.name=="machine")
    assert {"machine_"+name for name in domain.tools}==NAMES
    assert len(domain.tools)==6
    assert domain.engines==("pint",)


async def test_gateway_lists_six_compact_tools_with_basic_described_parameters():
    async with Client(gateway) as client:
        tools={t.name:t for t in await client.list_tools()}
        listing=await client.call_tool("toolkit_list_tools",{})
    assert NAMES<=set(tools)
    domain=next(d for d in listing.structured_content["data"]["domains"] if d["name"]=="machine")
    assert set(domain["tools"])==NAMES
    for name in NAMES:
        assert 30<len(tools[name].description)<650
        for prop in tools[name].input_schema["properties"].values():
            assert prop["type"] in {"string","number","integer","boolean"}
            assert prop["description"]


@pytest.mark.parametrize("name,args",CASES)
async def test_successful_mcp_contract(name,args):
    async with Client(gateway) as client:
        result=await mcp(client,name,args)
    assert result.ok,result.error
    assert result.provenance.engine_versions["pint"]
    assert result.artifacts[0].sha256


@pytest.mark.parametrize("name,args",CASES)
async def test_every_supplied_physical_parameter_rejects_absurd_magnitudes(name,args):
    # All representative physical inputs, including optional inputs, cross the real MCP boundary.
    async with Client(gateway) as client:
        for param,raw in args.items():
            if not isinstance(raw,str) or not re.match(r"^[+-]?\d",raw) or ";" in raw:
                continue
            match=re.match(r"^[+-]?[\d.eE]+\s+(.+)$",raw)
            assert match,(param,raw)
            for value in ("1e308 ","-1e308 "):
                result=await mcp(client,name,{**args,param:value+match[1]})
                assert not result.ok,(name,param)
                assert result.error.family=="input.out_of_range",result.error


@pytest.mark.parametrize("name,args,param",[("machine_shaft",SHAFT,"outer_diameter"),("machine_gear",GEAR,"face_width"),("machine_weld",WELD,"leg_size"),("machine_pressure_vessel",VESSEL,"inner_radius"),("machine_belt",BELT,"small_diameter"),("machine_key",KEY,"length")])
async def test_zero_negative_bare_wrong_dimension_nan_text(name,args,param):
    async with Client(gateway) as client:
        for raw,family in [("0 mm","input.invalid"),("-1 mm","input.invalid"),("25","units.bare_number"),("1 kg","units.dimension_mismatch"),("NaN mm","units.parse_error"),("1e309 mm","input.invalid")]:
            result=await mcp(client,name,{**args,param:raw})
            assert not result.ok
            assert result.error.family==family,result.error
            assert result.error.details["param"]=="/"+param


BAD=[
    ("machine_shaft",{**SHAFT,"inner_diameter":"30 mm"}),
    ("machine_shaft",dict(outer_diameter="20 mm")),
    ("machine_shaft",{**SHAFT,"outer_diameter":"300 mm"}),
    ("machine_gear",{**GEAR,"diametral_pitch":"10 / inch"}),
    ("machine_gear",{**GEAR,"torque":"","power":"1 hp","speed":""}),
    ("machine_weld",{**WELD,"segments_text":"(0 mm,0 mm)-(abs(1) mm,0 mm)"}),
    ("machine_weld",{**WELD,"segments_text":";".join(["(0 mm,0 mm)-(1 mm,0 mm)"]*1000)}),
    ("machine_pressure_vessel",{**VESSEL,"outer_radius":"100 mm"}),
    ("machine_pressure_vessel",{**VESSEL,"thickness":"","outer_radius":"40 mm"}),
    ("machine_pressure_vessel",{**VESSEL,"thickness":"","allowable_stress":""}),
    ("machine_belt",{**BELT,"tight_tension":"100 N"}),
    ("machine_belt",{**BELT,"centre_distance":"100 mm"}),
    ("machine_key",{**KEY,"torque":""}),
    ("machine_key",{**SPLINE,"load_sharing":0}),
]


@pytest.mark.parametrize("name,args",BAD)
async def test_conflicts_missing_inputs_geometry_and_hostile_parser(name,args):
    async with Client(gateway) as client:
        result=await mcp(client,name,args)
    assert not result.ok
    assert result.error.family.startswith("input.")


@pytest.mark.parametrize("name,args",CASES)
async def test_dimensionless_parameters_are_finite_and_bounded(name,args):
    async with Client(gateway) as client:
        tools={t.name:t for t in await client.list_tools()}
        for param,prop in tools[name].input_schema["properties"].items():
            if prop["type"]=="number":
                result=await mcp(client,name,{**args,param:1e308})
                assert not result.ok,(name,param)
                assert result.error.family.startswith("input.")


@pytest.mark.parametrize("raw",['{"value":NaN,"unit":""}', '{"value":Infinity,"unit":"mm"}', '{"value":-Infinity,"unit":"kg"}'])
async def test_nested_nonfinite_json_quantity_diagnostics_remain_strict_json(raw):
    async with Client(gateway) as client:
        result=await mcp(client,"machine_shaft",{**SHAFT,"outer_diameter":raw})
    assert not result.ok
    assert result.error.details["param"]=="/outer_diameter"
