"""Exercise all six tools through the real gateway and strict JSON contract."""

import json
import time

from fastmcp import Client
import pytest

from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.optimize_testing import validate
from timing import budget


CASES = [
    ("optimize_minimize", {"expression":"(x-2)^2", "initial":"x=0"}),
    ("optimize_linprog", {"c":"[-1,-1]", "A_ub":"[[1,1]]", "b_ub":"[3]"}),
    ("optimize_root", {"expression":"x^2-2", "initial":"x=1", "bracket":"[0,2]"}),
    ("optimize_fit", {"model":"a*x+b", "initial":"a=1;b=0", "data":'{"x":[0,1,2,3],"y":[1,3,5,7]}'}),
    ("optimize_sensitivity", {"expression":"x*y", "nominal":"x=2;y=3", "standard_uncertainties":"x=0.1;y=0.2"}),
    ("optimize_doe", {"factors":'{"x":[0,2],"y":[1,3]}'}),
]


async def mcp(client, name, args):
    response = await client.call_tool(name, args, raise_on_error=False)
    assert not response.is_error, response.content
    for item in response.content:
        if item.type == "text":
            json.loads(item.text, parse_constant=lambda value: pytest.fail("nonfinite JSON: " + value))
    assert response.structured_content is not None
    return validate(response.structured_content)


async def test_gateway_names_descriptions_and_unique_parameter_help():
    domain = next(d for d in all_domains() if d.name == "optimize")
    assert domain.engines == ("numpy", "pint")
    assert {"optimize_"+n for n in domain.tools} == {n for n,_ in CASES}
    async with Client(gateway) as client:
        tools = [t for t in await client.list_tools() if t.name.startswith("optimize_")]
    assert len(tools) == 6
    for tool in tools:
        assert 40 < len(tool.description) < 650, (tool.name, len(tool.description))
        assert "Grammar:" in tool.description
        assert "does not track units inside expressions" in tool.description
        descriptions = []
        for name, prop in tool.input_schema["properties"].items():
            assert prop["type"] in ("string", "number", "integer", "boolean")
            assert 5 < len(prop["description"]) <= 140, (tool.name, name, prop["description"])
            descriptions.append(prop["description"])
        assert len(descriptions) == len(set(descriptions)), tool.name


@pytest.mark.parametrize("name,args", CASES)
async def test_all_six_success(name, args):
    async with Client(gateway) as client:
        result = await mcp(client, name, args)
    assert result.ok, result.error
    assert result.provenance.engine_versions["numpy"]
    assert result.provenance.engine_versions["pint"]


@pytest.mark.parametrize("name,args", CASES)
async def test_missing_inputs_are_contract_failures(name, args):
    async with Client(gateway) as client:
        assert not (await mcp(client, name, {})).ok


@pytest.mark.parametrize("name,args,param", [
    (*CASES[0], "expression"), (*CASES[2], "expression"), (*CASES[3], "model"),
    (*CASES[4], "expression"), (*CASES[5], "model"),
])
@pytest.mark.parametrize("text", ["(" * 6000, "1+" * 3000, "-" * 6000, "undeclared", "sqrt(-1)", "NaN", "1e308", "10^10^10"])
async def test_expression_refusals_through_client(name, args, param, text):
    async with Client(gateway) as client:
        await client.list_tools()
        start = time.perf_counter()
        r = await mcp(client, name, {**args, param:text})
        elapsed = time.perf_counter()-start
    assert not r.ok
    if len(text)>2000:
        assert elapsed < budget(1)


BAD = [
    ("optimize_minimize", {"expression":"x^2", "initial":"x=0", "bounds":"[[2,1]]"}),
    ("optimize_minimize", {"expression":"x^2", "initial":"x=0", "constraints":";".join(["x<=0"]*21)}),
    ("optimize_minimize", {"expression":"v0^2", "initial":";".join(f"v{i}=0" for i in range(17))}),
    ("optimize_minimize", {"expression":"x^2", "initial":"x=NaN"}),
    ("optimize_minimize", {"expression":"x^2", "initial":"x=1e308"}),
    ("optimize_minimize", {"expression":"x", "initial":"x=0", "max_iterations":1}),
    ("optimize_minimize", {"expression":"x^2", "initial":"x=1", "parameters":"x=2"}),
    ("optimize_linprog", {"c":"[-1]"}),
    ("optimize_linprog", {"c":"[1]", "bounds":"[[2,1]]"}),
    ("optimize_linprog", {"c":"[1]", "A_ub":"[[1]]"}),
    ("optimize_linprog", {"c":"[1e308]"}),
    ("optimize_linprog", {"c":"[NaN]"}),
    ("optimize_root", {"expression":"x^2+1", "initial":"x=0", "method":"newton"}),
    ("optimize_root", {"expression":"x^2-2", "initial":"x=1", "bounds":"[[2,1]]"}),
    ("optimize_root", {"expression":"x-1", "initial":"x=0;y=0"}),
    ("optimize_fit", {**CASES[3][1], "data_file":"missing.csv"}),
    ("optimize_fit", {**CASES[3][1], "model":"a+b", "initial":"a=1;b=0"}),
    ("optimize_fit", {**CASES[3][1], "bounds":"[[2,1],[0,3]]"}),
    ("optimize_sensitivity", {**CASES[4][1], "correlation":"[[1,2],[2,1]]"}),
    ("optimize_sensitivity", {**CASES[4][1], "nominal":"x=1e308;y=1"}),
    ("optimize_sensitivity", {**CASES[4][1], "samples":200001, "method":"monte_carlo"}),
    ("optimize_doe", {"factors":'{"x":[2,1]}'}),
    ("optimize_doe", {"factors":'{"x":[0,1e308]}'}),
    ("optimize_doe", {**CASES[5][1], "model":"x", "responses":"[1]"}),
]


@pytest.mark.parametrize("name,args", BAD)
async def test_client_model_refusals(name, args):
    async with Client(gateway) as client:
        assert not (await mcp(client, name, args)).ok


@pytest.mark.parametrize("name,args", CASES)
async def test_wall_budget_refusal_through_client(name, args, monkeypatch):
    from eng_toolkit.domains.optimize import _common
    original = _common.Budget.check
    def expired(self):
        self.deadline = -1
        original(self)
    monkeypatch.setattr(_common.Budget, "check", expired)
    async with Client(gateway) as client:
        r = await mcp(client, name, args)
    assert not r.ok
    assert r.error.family == "input.out_of_range"
    assert r.error.details["reason"] == "optimize.time_budget_exceeded"


@pytest.mark.parametrize("name,args", CASES)
@pytest.mark.parametrize("value", ["1", "1e308 s", "1e-90 ys", "NaN s", "1 kg", "(1+1) s"])
async def test_physical_time_budget_validation(name, args, value):
    async with Client(gateway) as client:
        assert not (await mcp(client, name, {**args, "time_limit":value})).ok
